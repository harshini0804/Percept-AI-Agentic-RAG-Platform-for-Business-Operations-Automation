"""
Unit tests for app.core.orchestration's node functions (Section 3.3).

Each node is tested in isolation — its collaborators (embed_text,
search_with_retry, call_llm, execute_tool) are monkeypatched at the
name they're bound to *inside orchestration.py* (since it does
`from x import y`, patching `app.core.orchestration.y` is what
actually intercepts the call — patching `app.core.x.y` would not).

Database-writing side effects (log_decision, create_escalation,
complete_agent_run) are NOT mocked here — they hit the real test
database via conftest.py's fixtures, and are asserted on directly,
since logging_service already has its own dedicated test file
proving those functions work correctly in isolation.
"""

import pytest
from app.core.orchestration import (
    embed_node,
    retrieve_node,
    reason_node,
    action_gate_node,
    start_run,
)
from app.core.db import get_connection


def _fetch_decisions(run_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT step_type, detail FROM agent_decisions WHERE run_id = %s ORDER BY created_at;",
                (run_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _fetch_run(run_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM agent_runs WHERE id = %s;", (run_id,))
            return cur.fetchone()
    finally:
        conn.close()


def _fetch_escalation(run_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM escalations WHERE run_id = %s;", (run_id,))
            return cur.fetchone()
    finally:
        conn.close()


def test_embed_node_sets_embedding(monkeypatch):
    monkeypatch.setattr(
        "app.core.orchestration.embed_text", lambda text: [0.1, 0.2, 0.3]
    )

    state = {"input_text": "some input"}
    result = embed_node(state)

    assert result["embedding"] == [0.1, 0.2, 0.3]


def test_retrieve_node_logs_decision_with_top_score(monkeypatch, existing_run_id):
    fake_results = [
        {"chunk_text": "chunk one", "similarity": 0.87},
        {"chunk_text": "chunk two", "similarity": 0.5},
    ]
    monkeypatch.setattr(
        "app.core.orchestration.search_with_retry",
        lambda **kwargs: (fake_results, False),
    )

    state = {
        "run_id": existing_run_id,
        "vertical": "dummy",
        "source_type": "postmortem",
        "input_text": "test query",
        "confidence_threshold": 0.7,
    }
    result = retrieve_node(state)

    assert result["retrieval_results"] == fake_results
    assert result["retrieval_retried"] is False

    decisions = _fetch_decisions(existing_run_id)
    assert len(decisions) == 1
    assert decisions[0]["step_type"] == "retrieval"
    assert decisions[0]["detail"]["top_score"] == pytest.approx(0.87)
    assert decisions[0]["detail"]["num_results"] == 2
    assert decisions[0]["detail"]["retried"] is False


def test_retrieve_node_handles_empty_results_without_error(monkeypatch, existing_run_id):
    monkeypatch.setattr(
        "app.core.orchestration.search_with_retry", lambda **kwargs: ([], False)
    )

    state = {
        "run_id": existing_run_id,
        "vertical": "dummy",
        "source_type": "postmortem",
        "input_text": "test query",
        "confidence_threshold": 0.7,
    }
    result = retrieve_node(state)

    assert result["retrieval_results"] == []
    decisions = _fetch_decisions(existing_run_id)
    assert decisions[0]["detail"]["top_score"] == 0.0


def test_reason_node_without_tool_calls(monkeypatch, existing_run_id):
    monkeypatch.setattr("app.core.orchestration.get_tools_for_vertical", lambda v: [])
    monkeypatch.setattr(
        "app.core.orchestration.call_llm",
        lambda messages, tools=None: {"content": "plain answer", "tool_calls": []},
    )

    state = {
        "run_id": existing_run_id,
        "vertical": "dummy",
        "input_text": "input",
        "system_prompt": "prompt",
        "retrieval_results": [],
    }
    result = reason_node(state)

    assert result["llm_content"] == "plain answer"
    assert result["tool_calls"] == []

    decisions = _fetch_decisions(existing_run_id)
    assert decisions[-1]["step_type"] == "llm_reasoning"
    assert decisions[-1]["detail"]["content"] == "plain answer"


def test_reason_node_executes_tool_call_then_reasons_again(monkeypatch, existing_run_id):
    call_log = []

    def fake_call_llm(messages, tools=None):
        call_log.append(len(messages))
        if len(call_log) == 1:
            return {
                "content": None,
                "tool_calls": [{"name": "some_tool", "arguments": {"x": 1}}],
            }
        return {"content": "final answer after tool", "tool_calls": []}

    monkeypatch.setattr(
        "app.core.orchestration.get_tools_for_vertical",
        lambda v: [{"name": "some_tool", "description": "d", "parameters": {}}],
    )
    monkeypatch.setattr("app.core.orchestration.call_llm", fake_call_llm)
    monkeypatch.setattr(
        "app.core.orchestration.execute_tool",
        lambda vertical, name, args: {"tool_result": "ok"},
    )

    state = {
        "run_id": existing_run_id,
        "vertical": "dummy",
        "input_text": "input",
        "system_prompt": "prompt",
        "retrieval_results": [],
    }
    result = reason_node(state)

    assert result["llm_content"] == "final answer after tool"
    assert len(call_log) == 2  # called once before the tool, once after

    decisions = _fetch_decisions(existing_run_id)
    step_types = [d["step_type"] for d in decisions]
    assert "tool_call" in step_types
    assert step_types[-1] == "llm_reasoning"


def test_reason_node_two_sequential_tool_rounds(monkeypatch, existing_run_id):
    """Verifies the multi-round tool-calling loop: LLM calls tool A in
    round 1, then tool B in round 2, then produces a final text answer
    that reflects both results."""
    call_count = 0

    def fake_call_llm(messages, tools=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Round 1: call get_incident_details
            return {
                "content": None,
                "tool_calls": [{"name": "get_incident_details", "arguments": {"incident_id": "inc-1"}}],
            }
        if call_count == 2:
            # Round 2: call lookup_incidents_by_service
            return {
                "content": None,
                "tool_calls": [{"name": "lookup_incidents_by_service", "arguments": {"service": "payments"}}],
            }
        # Round 3: final text answer
        return {"content": "Root cause is db-pool-exhaustion across payments incidents inc-1 and inc-2", "tool_calls": []}

    tool_results = []

    def fake_execute_tool(vertical, name, args):
        result = {"tool": name, "args": args, "data": f"result_for_{name}"}
        tool_results.append(result)
        return result

    monkeypatch.setattr(
        "app.core.orchestration.get_tools_for_vertical",
        lambda v: [
            {"name": "get_incident_details", "description": "d", "parameters": {}},
            {"name": "lookup_incidents_by_service", "description": "d", "parameters": {}},
        ],
    )
    monkeypatch.setattr("app.core.orchestration.call_llm", fake_call_llm)
    monkeypatch.setattr("app.core.orchestration.execute_tool", fake_execute_tool)

    state = {
        "run_id": existing_run_id,
        "vertical": "dummy",
        "input_text": "input",
        "system_prompt": "prompt",
        "retrieval_results": [],
    }
    result = reason_node(state)

    # call_llm invoked 3 times: initial + after round 1 + after round 2
    assert call_count == 3
    # Both tools were executed
    assert len(tool_results) == 2
    assert tool_results[0]["tool"] == "get_incident_details"
    assert tool_results[1]["tool"] == "lookup_incidents_by_service"
    # all_tool_calls in state contains both rounds
    assert len(result["tool_calls"]) == 2
    # Final answer reflects both results
    assert "db-pool-exhaustion" in result["llm_content"]

    # Decision log should contain 2 tool_call entries + 1 llm_reasoning
    decisions = _fetch_decisions(existing_run_id)
    step_types = [d["step_type"] for d in decisions]
    assert step_types.count("tool_call") == 2
    assert step_types[-1] == "llm_reasoning"


def test_reason_node_forced_text_fallback_after_max_steps(monkeypatch, existing_run_id):
    """Verifies the forced-text-completion fallback: if the LLM keeps
    requesting tool calls after max_steps is reached, reason_node makes
    one final call_llm(tools=None) to force a text response."""
    # Use env var to set max_steps=2 (validates env-config fix too)
    monkeypatch.setenv("REASON_NODE_MAX_STEPS", "2")

    call_count = 0
    tools_arg_log = []

    def fake_call_llm(messages, tools=None):
        nonlocal call_count
        call_count += 1
        tools_arg_log.append(tools)

        if call_count <= 3:
            # Rounds 1-3: stubbornly keep calling tools
            return {
                "content": None,
                "tool_calls": [{"name": "some_tool", "arguments": {"round": call_count}}],
            }
        # Final forced-text call (tools=None)
        return {"content": "forced fallback answer after max steps", "tool_calls": []}

    monkeypatch.setattr(
        "app.core.orchestration.get_tools_for_vertical",
        lambda v: [{"name": "some_tool", "description": "d", "parameters": {}}],
    )
    monkeypatch.setattr("app.core.orchestration.call_llm", fake_call_llm)
    monkeypatch.setattr(
        "app.core.orchestration.execute_tool",
        lambda vertical, name, args: {"ok": True},
    )

    state = {
        "run_id": existing_run_id,
        "vertical": "dummy",
        "input_text": "input",
        "system_prompt": "prompt",
        "retrieval_results": [],
    }
    result = reason_node(state)

    # call_llm called 4 times:
    #   1: initial (returns tool_calls) → round 1
    #   2: after round 1 tools, step_count=1 < max_steps=2, tools passed (returns tool_calls) → round 2
    #   3: after round 2 tools, step_count=2 == max_steps=2, tools=None passed (returns tool_calls still)
    #   4: forced fallback (tools=None) because response still had tool_calls
    assert call_count == 4

    # The 3rd call should have tools=None (step_count reached max_steps)
    assert tools_arg_log[2] is None
    # The 4th call (forced fallback) should also have tools=None
    assert tools_arg_log[3] is None

    # Final content is not None — the fallback worked
    assert result["llm_content"] == "forced fallback answer after max steps"
    # 2 tool calls were executed (one per round within max_steps)
    assert len(result["tool_calls"]) == 2



def test_action_gate_node_fires_action_when_confidence_meets_threshold(
    monkeypatch, existing_run_id
):
    monkeypatch.setattr(
        "app.core.orchestration.execute_tool",
        lambda vertical, name, args: {"logged": True},
    )

    state = {"run_id": existing_run_id, "vertical": "dummy", "confidence_threshold": 0.7}
    result = action_gate_node(
        state,
        confidence=0.9,
        action_tool_name="log_dummy_action",
        action_tool_args={"note": "x"},
    )

    assert result["escalated"] is False
    assert result["action_taken"] == {"action_name": "log_dummy_action", "result": {"logged": True}}

    run_row = _fetch_run(existing_run_id)
    assert run_row["status"] == "completed"
    assert float(run_row["confidence"]) == pytest.approx(0.9)

    # No escalation should have been created on the successful path.
    assert _fetch_escalation(existing_run_id) is None


def test_action_gate_node_escalates_and_never_calls_execute_tool_when_below_threshold(
    monkeypatch, existing_run_id
):
    def fail_if_called(vertical, name, args):
        raise AssertionError(
            "execute_tool must NOT be called when confidence is below threshold"
        )

    monkeypatch.setattr("app.core.orchestration.execute_tool", fail_if_called)

    state = {"run_id": existing_run_id, "vertical": "dummy", "confidence_threshold": 0.99}
    result = action_gate_node(
        state,
        confidence=0.5,
        action_tool_name="log_dummy_action",
        action_tool_args={"note": "should not fire"},
    )

    assert result["escalated"] is True
    assert result["action_taken"] is None

    run_row = _fetch_run(existing_run_id)
    assert run_row["status"] == "escalated"

    escalation_row = _fetch_escalation(existing_run_id)
    assert escalation_row is not None
    assert escalation_row["pending_action"] == {
        "tool_name": "log_dummy_action",
        "arguments": {"note": "should not fire"},
    }


def test_action_gate_node_escalation_with_no_proposed_action_stores_null_pending_action(
    existing_run_id,
):
    """Covers the 'nothing to do here' case (e.g. should_act was False) —
    pending_action should be NULL, and approving it later should be
    rejected by the API (tested separately in test_api.py)."""
    state = {"run_id": existing_run_id, "vertical": "dummy", "confidence_threshold": 0.7}
    result = action_gate_node(
        state, confidence=0.9, action_tool_name=None, escalation_reason="No action needed."
    )

    assert result["escalated"] is True
    escalation_row = _fetch_escalation(existing_run_id)
    assert escalation_row["pending_action"] is None
    assert escalation_row["reason"] == "No action needed."


def test_start_run_creates_a_real_agent_run():
    run_id = start_run(vertical="dummy", trigger_type="upload")
    row = _fetch_run(run_id)
    assert row is not None
    assert row["status"] == "running"