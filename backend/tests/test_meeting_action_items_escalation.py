"""
Tests for Vertical 4's handling of wrong documents and failures
(PR A): empty or unreadable extraction escalates instead of reporting a
confident success, extraction runs report no fabricated confidence, and
no run — Trigger 1 or Trigger 2 — is ever left stuck in 'running'.

These tests avoid the real embedding model: recurrence search and
embedding writes are replaced with local stand-ins, so they run
anywhere (rollback is still exercised against real embeddings rows).
"""

import pytest

from app.core.db import get_connection
from app.schemas.agent_contract import AgentRunInput, TriggerType
import app.verticals.meeting_action_items.graph as graph
import app.verticals.meeting_action_items.followup as followup
from app.verticals.meeting_action_items.graph import (
    run_meeting_action_items,
    NO_ITEMS_REASON,
    UNREADABLE_OUTPUT_REASON,
)

ONE_ITEM_JSON = '[{"description": "Update the API docs", "owner": "morgan", "deadline": null}]'
TWO_ITEMS_JSON = (
    '[{"description": "Update the API docs", "owner": "morgan", "deadline": null}, '
    '{"description": "Review the PR", "owner": "jamie", "deadline": null}]'
)
ZERO_VECTOR = "[" + ",".join(["0.1"] * 384) + "]"


def _llm_returning(content):
    return lambda messages: {"content": content, "tool_calls": []}


def _no_recurrence(monkeypatch):
    monkeypatch.setattr(graph, "_check_recurrence", lambda *a, **k: (False, None, None, []))


def _fake_upsert_that_writes_a_row(**kwargs):
    """Writes a real embeddings row (constant vector) so rollback has
    something genuine to delete, without needing the embedding model."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO embeddings (vertical, source_type, source_id, chunk_text, embedding) "
                "VALUES (%s, %s, %s, %s, %s::vector);",
                (kwargs["vertical"], kwargs["source_type"], kwargs["source_id"],
                 kwargs["chunk_text"], ZERO_VECTOR),
            )
        conn.commit()
    finally:
        conn.close()


def _submit(text="some transcript"):
    return run_meeting_action_items(
        AgentRunInput(
            vertical="meeting_action_items",
            trigger_type=TriggerType.UPLOAD,
            input_payload={"text": text},
        )
    )


def _scalar(sql, params=()):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()["n"]
    finally:
        conn.close()


def _run_row(run_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status, confidence FROM agent_runs WHERE id = %s;", (run_id,))
            return cur.fetchone()
    finally:
        conn.close()


def _step_types(run_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT step_type FROM agent_decisions WHERE run_id = %s ORDER BY created_at;",
                (run_id,),
            )
            return [r["step_type"] for r in cur.fetchall()]
    finally:
        conn.close()


def _escalation_reasons(run_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT reason FROM escalations WHERE run_id = %s;", (run_id,))
            return [r["reason"] for r in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------
# Trigger 1: a successful extraction reports no fabricated confidence
# ---------------------------------------------------------------

def test_successful_extraction_completes_with_null_confidence(monkeypatch):
    monkeypatch.setattr(graph, "call_llm", _llm_returning(TWO_ITEMS_JSON))
    monkeypatch.setattr(graph, "upsert_embedding", _fake_upsert_that_writes_a_row)
    _no_recurrence(monkeypatch)

    output = _submit()

    assert output.status == "completed"
    assert output.confidence is None
    assert output.escalated is False
    assert len(output.actions_taken) == 2

    row = _run_row(output.run_id)
    assert row["status"] == "completed"
    assert row["confidence"] is None  # stored as NULL, not a fixed 1.0
    assert _step_types(output.run_id).count("action") == 2
    assert _scalar("SELECT COUNT(*) AS n FROM action_items;") == 2
    assert _scalar("SELECT COUNT(*) AS n FROM meetings;") == 1


def test_markdown_fenced_json_is_accepted(monkeypatch):
    """A reply wrapped in ```json fences is valid content, not an
    unreadable output that should escalate."""
    fenced = "```json\n" + ONE_ITEM_JSON + "\n```"
    monkeypatch.setattr(graph, "call_llm", _llm_returning(fenced))
    monkeypatch.setattr(graph, "upsert_embedding", _fake_upsert_that_writes_a_row)
    _no_recurrence(monkeypatch)

    output = _submit()

    assert output.status == "completed"
    assert len(output.actions_taken) == 1


# ---------------------------------------------------------------
# Trigger 1: wrong or empty documents escalate
# ---------------------------------------------------------------

def test_zero_extracted_items_escalates_with_specific_reason(monkeypatch):
    monkeypatch.setattr(graph, "call_llm", _llm_returning("[]"))

    output = _submit("This is a vendor contract, not a meeting.")

    assert output.status == "escalated"
    assert output.escalated is True
    assert output.confidence is None
    assert output.escalation_reason == NO_ITEMS_REASON
    assert output.actions_taken == []

    row = _run_row(output.run_id)
    assert row["status"] == "escalated"
    assert row["confidence"] is None
    assert _escalation_reasons(output.run_id) == [NO_ITEMS_REASON]
    # The Report viewer's "Escalated for Human Review" panel reads this decision.
    assert "escalation" in _step_types(output.run_id)


def test_zero_extracted_items_leaves_no_orphan_meeting(monkeypatch):
    monkeypatch.setattr(graph, "call_llm", _llm_returning("[]"))

    _submit("Not a meeting transcript.")

    assert _scalar("SELECT COUNT(*) AS n FROM meetings;") == 0
    assert _scalar("SELECT COUNT(*) AS n FROM action_items;") == 0


def test_unreadable_output_escalates_with_a_different_reason(monkeypatch):
    monkeypatch.setattr(graph, "call_llm", _llm_returning("I could not find anything, sorry."))

    output = _submit()

    assert output.status == "escalated"
    assert output.escalation_reason == UNREADABLE_OUTPUT_REASON
    assert output.escalation_reason != NO_ITEMS_REASON
    assert _scalar("SELECT COUNT(*) AS n FROM meetings;") == 0


def test_all_entries_unusable_is_treated_as_unreadable(monkeypatch):
    monkeypatch.setattr(graph, "call_llm", _llm_returning('[{"owner": null}, "text"]'))

    output = _submit()

    assert output.escalation_reason == UNREADABLE_OUTPUT_REASON


# ---------------------------------------------------------------
# Trigger 1: failures never leave a run stuck, and undo partial work
# ---------------------------------------------------------------

def test_failure_midway_rolls_back_everything_and_escalates(monkeypatch):
    monkeypatch.setattr(graph, "call_llm", _llm_returning(TWO_ITEMS_JSON))
    _no_recurrence(monkeypatch)

    calls = {"n": 0}

    def upsert_that_fails_on_second_item(**kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("embedding service unavailable")
        _fake_upsert_that_writes_a_row(**kwargs)

    monkeypatch.setattr(graph, "upsert_embedding", upsert_that_fails_on_second_item)

    output = _submit()

    # The run is closed, not left 'running'.
    assert output.status == "escalated"
    assert _run_row(output.run_id)["status"] == "escalated"
    assert "RuntimeError" in output.escalation_reason
    assert "removed" in output.escalation_reason

    # Item 1 was fully stored before the failure — all of it is undone.
    assert _scalar("SELECT COUNT(*) AS n FROM action_items;") == 0
    assert _scalar("SELECT COUNT(*) AS n FROM embeddings WHERE source_type = 'action_item';") == 0
    assert _scalar("SELECT COUNT(*) AS n FROM meetings;") == 0

    # No "action taken" is reported for work that was undone.
    assert "action" not in _step_types(output.run_id)
    assert len(_escalation_reasons(output.run_id)) == 1


def test_failure_during_extraction_escalates_and_does_not_stay_running(monkeypatch):
    def llm_outage(messages):
        raise ConnectionError("model provider unreachable")

    monkeypatch.setattr(graph, "call_llm", llm_outage)

    output = _submit()

    assert output.status == "escalated"
    assert "ConnectionError" in output.escalation_reason
    assert _run_row(output.run_id)["status"] == "escalated"
    assert _scalar("SELECT COUNT(*) AS n FROM agent_runs WHERE status = 'running';") == 0
    assert _scalar("SELECT COUNT(*) AS n FROM meetings;") == 0


def test_failed_rollback_is_reported_honestly(monkeypatch):
    monkeypatch.setattr(graph, "call_llm", _llm_returning(ONE_ITEM_JSON))
    _no_recurrence(monkeypatch)

    def always_fails(**kwargs):
        raise RuntimeError("write failed")

    def rollback_also_fails(created):
        raise RuntimeError("database gone")

    monkeypatch.setattr(graph, "upsert_embedding", always_fails)
    monkeypatch.setattr(graph, "_rollback_created_records", rollback_also_fails)

    output = _submit()

    # Still closed and escalated — and it does not claim items were removed.
    assert output.status == "escalated"
    assert "manual cleanup" in output.escalation_reason
    assert "were removed" not in output.escalation_reason
    assert _run_row(output.run_id)["status"] == "escalated"


def test_resubmitting_after_a_failure_starts_clean(monkeypatch):
    """Rollback means a failed transcript can simply be resubmitted: the
    second attempt must not see leftovers from the first."""
    _no_recurrence(monkeypatch)
    monkeypatch.setattr(graph, "call_llm", _llm_returning(ONE_ITEM_JSON))

    def fails(**kwargs):
        raise RuntimeError("temporary failure")

    monkeypatch.setattr(graph, "upsert_embedding", fails)
    first = _submit()
    assert first.status == "escalated"

    monkeypatch.setattr(graph, "upsert_embedding", _fake_upsert_that_writes_a_row)
    second = _submit()

    assert second.status == "completed"
    assert _scalar("SELECT COUNT(*) AS n FROM action_items;") == 1
    assert _scalar("SELECT COUNT(*) AS n FROM meetings;") == 1


# ---------------------------------------------------------------
# Trigger 2: failures close the run, and one item can't stop the rest
# ---------------------------------------------------------------

def _create_meeting():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO meetings (meeting_date) VALUES (now()) RETURNING id;")
            meeting_id = str(cur.fetchone()["id"])
        conn.commit()
        return meeting_id
    finally:
        conn.close()


def _overdue_item(meeting_id, description, owner="alex"):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO action_items (meeting_id, description, owner, deadline) "
                "VALUES (%s, %s, %s, '2020-01-01') RETURNING id;",
                (meeting_id, description, owner),
            )
            item_id = cur.fetchone()["id"]
        conn.commit()
    finally:
        conn.close()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM action_items WHERE id = %s;", (item_id,))
            return cur.fetchone()
    finally:
        conn.close()


def _verdict(content):
    return lambda messages: {"content": content, "tool_calls": []}


def test_followup_failure_after_run_starts_closes_the_run(monkeypatch):
    item = _overdue_item(_create_meeting(), "Fix the pipeline")
    monkeypatch.setattr(followup, "_search_owner_activity", lambda *a, **k: [])
    monkeypatch.setattr(followup, "call_llm", _verdict('{"verdict": "no_evidence", "confidence": 0.5}'))

    def tool_outage(*args, **kwargs):
        raise RuntimeError("notification service down")

    monkeypatch.setattr(followup, "execute_tool", tool_outage)

    with pytest.raises(RuntimeError):
        followup.check_and_act_on_item(item)

    assert _scalar("SELECT COUNT(*) AS n FROM agent_runs WHERE status = 'running';") == 0
    assert _scalar("SELECT COUNT(*) AS n FROM agent_runs WHERE status = 'escalated';") == 1
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM agent_runs;")
            run_id = str(cur.fetchone()["id"])
    finally:
        conn.close()
    assert "Follow-up check failed" in _escalation_reasons(run_id)[0]


def test_followup_failure_before_a_run_exists_creates_no_records(monkeypatch):
    """An outage during the search/LLM step must not create runs or
    escalations — otherwise every 10-minute cycle would flood the queue."""
    item = _overdue_item(_create_meeting(), "Fix the pipeline")

    def search_outage(*args, **kwargs):
        raise ConnectionError("search unavailable")

    monkeypatch.setattr(followup, "_search_owner_activity", search_outage)

    with pytest.raises(ConnectionError):
        followup.check_and_act_on_item(item)

    assert _scalar("SELECT COUNT(*) AS n FROM agent_runs;") == 0
    assert _scalar("SELECT COUNT(*) AS n FROM escalations;") == 0


def test_run_followup_check_continues_past_a_failing_item(monkeypatch):
    meeting_id = _create_meeting()
    _overdue_item(meeting_id, "Broken task")
    _overdue_item(meeting_id, "Resolved task")

    monkeypatch.setattr(followup, "_search_owner_activity", lambda *a, **k: [])

    def verdict_by_description(messages):
        if "Resolved task" in messages[1]["content"]:
            return {"content": '{"verdict": "done", "confidence": 0.9}', "tool_calls": []}
        return {"content": '{"verdict": "no_evidence", "confidence": 0.5}', "tool_calls": []}

    monkeypatch.setattr(followup, "call_llm", verdict_by_description)

    def tool_outage(*args, **kwargs):
        raise RuntimeError("notification service down")

    monkeypatch.setattr(followup, "execute_tool", tool_outage)

    summary = followup.run_followup_check()  # must not raise

    assert summary["checked"] == 2
    assert summary["failed"] == 1
    assert summary["processed"] == 1  # the second item still got processed
    assert _scalar("SELECT COUNT(*) AS n FROM agent_runs WHERE status = 'running';") == 0
    assert _scalar("SELECT COUNT(*) AS n FROM action_items WHERE status = 'resolved';") == 1