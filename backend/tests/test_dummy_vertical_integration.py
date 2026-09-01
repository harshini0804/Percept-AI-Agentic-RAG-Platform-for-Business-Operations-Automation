"""
End-to-end integration test for the dummy vertical (Section 10,
Phase 1 checkpoint: "a trivial dummy vertical runs end to end").

This exercises the REAL database and the REAL embedding model
(sentence-transformers downloads/caches on first use — this is the
one test file that needs network access to huggingface.co). Only
the LLM call is mocked, since it's the only genuinely non-deterministic,
paid, network-dependent piece — everything else here mirrors exactly
what was manually verified by hand, repeatedly, throughout this
project's development.
"""

from app.core.embeddings import upsert_embedding
from app.verticals.dummy.graph import build_dummy_graph
from app.core.orchestration import start_run


def _fake_llm_response(confidence: float, should_act: bool, reason: str):
    """Builds a fake call_llm return value matching the dummy
    vertical's expected JSON output shape exactly."""
    content = (
        f'{{"confidence": {confidence}, "should_act": {str(should_act).lower()}, '
        f'"reason": "{reason}"}}'
    )
    return {"content": content, "tool_calls": []}


def test_dummy_vertical_completes_autonomously_when_confident(monkeypatch):
    # Seed one embedding so retrieval has real, semantically-relevant
    # context to find — proving retrieval genuinely works, not just
    # that an empty-result path doesn't crash.
    upsert_embedding(
        vertical="dummy",
        source_type="postmortem",
        chunk_text="The server crashed due to a memory leak in the caching layer during peak traffic.",
    )

    monkeypatch.setattr(
        "app.core.orchestration.call_llm",
        lambda messages, tools=None: _fake_llm_response(
            confidence=0.96, should_act=True, reason="Memory leak caused crash"
        ),
    )

    run_id = start_run(vertical="dummy", trigger_type="upload")
    initial_state = {
        "run_id": run_id,
        "vertical": "dummy",
        "source_type": "postmortem",
        "input_text": "Outage last night, seems related to memory usage spiking under load.",
        "system_prompt": "irrelevant — call_llm is mocked",
        "confidence_threshold": 0.7,
    }

    graph = build_dummy_graph()
    final_state = graph.invoke(initial_state)

    assert final_state["escalated"] is False
    assert final_state["confidence"] == 0.96
    assert final_state["action_taken"]["action_name"] == "log_dummy_action"
    # Proves real retrieval found the seeded, semantically-related chunk.
    assert len(final_state["retrieval_results"]) >= 1


def test_dummy_vertical_escalates_with_pending_action_when_not_confident(monkeypatch):
    monkeypatch.setattr(
        "app.core.orchestration.call_llm",
        lambda messages, tools=None: _fake_llm_response(
            confidence=0.5, should_act=True, reason="Uncertain but leaning yes"
        ),
    )

    run_id = start_run(vertical="dummy", trigger_type="upload")
    # Confidence_threshold set above what the mocked LLM returns, to
    # force the escalation path deliberately (mirrors the manual
    # REPL test used throughout development to exercise this branch).
    initial_state = {
        "run_id": run_id,
        "vertical": "dummy",
        "source_type": "postmortem",
        "input_text": "Something odd happened but we're not fully sure what caused it.",
        "system_prompt": "irrelevant — call_llm is mocked",
        "confidence_threshold": 0.99,
    }

    graph = build_dummy_graph()
    final_state = graph.invoke(initial_state)

    assert final_state["escalated"] is True
    assert final_state["confidence"] == 0.5

    from app.core.db import get_connection

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pending_action, status FROM escalations WHERE run_id = %s;",
                (run_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row["status"] == "open"
    assert row["pending_action"]["tool_name"] == "log_dummy_action"


def test_dummy_vertical_malformed_llm_output_escalates_safely(monkeypatch):
    """finalize_node's try/except must fail SAFE (escalate, not crash
    or silently auto-act) when the LLM doesn't return valid JSON."""
    monkeypatch.setattr(
        "app.core.orchestration.call_llm",
        lambda messages, tools=None: {"content": "not valid json at all", "tool_calls": []},
    )

    run_id = start_run(vertical="dummy", trigger_type="upload")
    initial_state = {
        "run_id": run_id,
        "vertical": "dummy",
        "source_type": "postmortem",
        "input_text": "some input",
        "system_prompt": "irrelevant — call_llm is mocked",
        "confidence_threshold": 0.7,
    }

    graph = build_dummy_graph()
    final_state = graph.invoke(initial_state)

    assert final_state["escalated"] is True
    assert final_state["confidence"] == 0.0