"""
Tests for app.verticals.meeting_action_items.followup (Vertical 4,
Trigger 2, PR 3 + run-clutter fix).

Split the same way as test_meeting_action_items_graph.py:
- Pure logic / DB-only tests (no real embedding model needed).
- Full check_and_act_on_item() integration tests — these ALWAYS need
  the real embedding model (network access to huggingface.co on
  first run in a fresh environment), even for a zero-evidence case,
  since search_embeddings() embeds the query text before filtering —
  unlike Trigger 1's extraction, there's no zero-item shortcut here.
"""

import pytest
from app.core.db import get_connection
from app.core.embeddings import upsert_embedding
from app.verticals.meeting_action_items.followup import (
    _fetch_overdue_open_items,
    _judge_verdict,
    _mark_resolved,
    check_and_act_on_item,
)


def _create_meeting() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO meetings (meeting_date) VALUES (now()) RETURNING id;")
            meeting_id = str(cur.fetchone()["id"])
        conn.commit()
        return meeting_id
    finally:
        conn.close()


def _insert_action_item(
    meeting_id: str, description: str, owner: str, deadline, status="open",
    nudge_count=0, escalated=False,
) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO action_items
                    (meeting_id, description, owner, deadline, status, nudge_count, escalated)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (meeting_id, description, owner, deadline, status, nudge_count, escalated),
            )
            action_item_id = str(cur.fetchone()["id"])
        conn.commit()
        return action_item_id
    finally:
        conn.close()


def _fetch_item(action_item_id: str) -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM action_items WHERE id = %s;", (action_item_id,))
            return cur.fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------
# Pure logic / DB-only (no embedding model needed)
# ---------------------------------------------------------------

def test_fetch_overdue_open_items_includes_only_open_and_overdue():
    meeting_id = _create_meeting()
    overdue_open = _insert_action_item(meeting_id, "Overdue open item", "alex", "2020-01-01")
    _insert_action_item(meeting_id, "Overdue but resolved", "alex", "2020-01-01", status="resolved")
    _insert_action_item(meeting_id, "Open but not due yet", "alex", "2099-01-01")

    items = _fetch_overdue_open_items()
    ids = {str(i["id"]) for i in items}
    assert overdue_open in ids
    assert len(ids) == 1


def test_fetch_overdue_open_items_excludes_null_deadline():
    """
    Per Section 8.4's literal query (deadline <= today), an item with
    no deadline never matches — expected behavior, not a gap.
    """
    meeting_id = _create_meeting()
    _insert_action_item(meeting_id, "No deadline set", "alex", None)

    items = _fetch_overdue_open_items()
    assert items == []


def test_judge_verdict_returns_no_evidence_for_malformed_json(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.followup.call_llm",
        lambda messages: {"content": "not valid json", "tool_calls": []},
    )
    result = _judge_verdict("some task", [])
    assert result == {"verdict": "no_evidence", "confidence": 0.0}


def test_judge_verdict_returns_no_evidence_for_invalid_verdict_value(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.followup.call_llm",
        lambda messages: {"content": '{"verdict": "maybe", "confidence": 0.9}', "tool_calls": []},
    )
    result = _judge_verdict("some task", [])
    assert result == {"verdict": "no_evidence", "confidence": 0.0}


def test_judge_verdict_happy_path(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.followup.call_llm",
        lambda messages: {"content": '{"verdict": "done", "confidence": 0.85}', "tool_calls": []},
    )
    result = _judge_verdict("some task", [])
    assert result == {"verdict": "done", "confidence": 0.85}


def test_mark_resolved_updates_status():
    meeting_id = _create_meeting()
    action_item_id = _insert_action_item(meeting_id, "Task", "alex", "2020-01-01")

    _mark_resolved(action_item_id)

    row = _fetch_item(action_item_id)
    assert row["status"] == "resolved"


# ---------------------------------------------------------------
# Full pipeline (needs real embedding model — network access to
# huggingface.co required on first run in a fresh environment)
# ---------------------------------------------------------------

def test_check_and_act_on_item_marks_resolved_when_done(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.followup.call_llm",
        lambda messages: {"content": '{"verdict": "done", "confidence": 0.9}', "tool_calls": []},
    )

    meeting_id = _create_meeting()
    action_item_id = _insert_action_item(meeting_id, "Fix the bug", "alex", "2020-01-01")
    item = _fetch_item(action_item_id)

    output = check_and_act_on_item(item)

    assert output["status"] == "completed"
    assert output["escalated"] is False
    assert output["actions_taken"][0]["action_name"] == "mark_resolved"
    assert _fetch_item(action_item_id)["status"] == "resolved"


def test_check_and_act_on_item_sends_nudge_on_first_no_evidence(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.followup.call_llm",
        lambda messages: {"content": '{"verdict": "no_evidence", "confidence": 0.7}', "tool_calls": []},
    )

    meeting_id = _create_meeting()
    action_item_id = _insert_action_item(
        meeting_id, "Write the doc", "priya", "2020-01-01", nudge_count=0
    )
    item = _fetch_item(action_item_id)

    output = check_and_act_on_item(item)

    assert output["escalated"] is False
    assert output["actions_taken"][0]["action_name"] == "send_nudge"
    assert _fetch_item(action_item_id)["nudge_count"] == 1
    assert _fetch_item(action_item_id)["status"] == "open"


def test_check_and_act_on_item_escalates_after_one_nudge(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.followup.call_llm",
        lambda messages: {"content": '{"verdict": "in_progress", "confidence": 0.6}', "tool_calls": []},
    )

    meeting_id = _create_meeting()
    action_item_id = _insert_action_item(
        meeting_id, "Review the design", "morgan", "2020-01-01", nudge_count=1, escalated=False
    )
    item = _fetch_item(action_item_id)

    output = check_and_act_on_item(item)

    assert output["escalated"] is True
    assert output["escalation_reason"] is not None
    assert output["actions_taken"][0]["action_name"] == "escalate_to_manager"
    assert _fetch_item(action_item_id)["escalated"] is True


def test_check_and_act_on_item_returns_none_when_already_escalated(monkeypatch):
    """
    Regression test: previously, an already-escalated, still-open
    item would spawn a brand-new agent_run every single scheduled
    cycle forever (a real run-clutter bug found via the Dashboard
    showing the same 3 items repeating every 10 minutes). Now
    returns None and creates NO agent_run for this no-op case —
    while the evidence check itself still runs every cycle (so the
    item could still auto-resolve later if real evidence appears).
    """
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.followup.call_llm",
        lambda messages: {"content": '{"verdict": "no_evidence", "confidence": 0.5}', "tool_calls": []},
    )

    meeting_id = _create_meeting()
    action_item_id = _insert_action_item(
        meeting_id, "Old task", "jamie", "2020-01-01", nudge_count=2, escalated=True
    )
    item = _fetch_item(action_item_id)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM agent_runs;")
            runs_before = cur.fetchone()["c"]
    finally:
        conn.close()

    output = check_and_act_on_item(item)

    assert output is None

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM agent_runs;")
            runs_after = cur.fetchone()["c"]
    finally:
        conn.close()

    assert runs_after == runs_before  # no new agent_run was created

    # Item's own row still shows it was already escalated, unchanged.
    assert _fetch_item(action_item_id)["escalated"] is True
    assert _fetch_item(action_item_id)["nudge_count"] == 2


def test_check_and_act_on_item_still_auto_resolves_an_already_escalated_item(monkeypatch):
    """
    Confirms the no-op skip doesn't break auto-resolution: if real
    evidence appears for an already-escalated item, it should still
    resolve normally (and DOES create a run, since this is a
    meaningful outcome).
    """
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.followup.call_llm",
        lambda messages: {"content": '{"verdict": "done", "confidence": 0.9}', "tool_calls": []},
    )

    meeting_id = _create_meeting()
    action_item_id = _insert_action_item(
        meeting_id, "Old task", "jamie", "2020-01-01", nudge_count=2, escalated=True
    )
    item = _fetch_item(action_item_id)

    output = check_and_act_on_item(item)

    assert output is not None
    assert output["actions_taken"][0]["action_name"] == "mark_resolved"
    assert _fetch_item(action_item_id)["status"] == "resolved"


def test_check_and_act_on_item_logs_retrieval_and_reasoning_decisions(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.followup.call_llm",
        lambda messages: {"content": '{"verdict": "no_evidence", "confidence": 0.4}', "tool_calls": []},
    )

    meeting_id = _create_meeting()
    action_item_id = _insert_action_item(meeting_id, "Some task", "alex", "2020-01-01")
    item = _fetch_item(action_item_id)

    output = check_and_act_on_item(item)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT step_type FROM agent_decisions WHERE run_id = %s ORDER BY created_at;",
                (output["run_id"],),
            )
            step_types = [r["step_type"] for r in cur.fetchall()]
    finally:
        conn.close()

    assert step_types == ["retrieval", "llm_reasoning", "action"]


def test_search_owner_activity_finds_matching_evidence(monkeypatch):
    """
    Confirms the owner/date metadata filter actually works — seeds
    one matching owner_activity chunk (dated after the item's
    creation, as the filter requires) and one for a different owner,
    expects only the matching one to be retrieved.
    """
    from datetime import timedelta

    meeting_id = _create_meeting()
    action_item_id = _insert_action_item(meeting_id, "Fix the deploy pipeline", "alex", "2020-01-01")
    item = _fetch_item(action_item_id)
    activity_date = (item["created_at"].date() + timedelta(days=1)).isoformat()

    upsert_embedding(
        vertical="meeting_action_items",
        source_type="owner_activity",
        chunk_text="Alex merged a fix for the deploy pipeline.",
        metadata={"owner": "alex", "date": activity_date},
    )
    upsert_embedding(
        vertical="meeting_action_items",
        source_type="owner_activity",
        chunk_text="Priya updated the onboarding docs.",
        metadata={"owner": "priya", "date": activity_date},
    )

    monkeypatch.setattr(
        "app.verticals.meeting_action_items.followup.call_llm",
        lambda messages: {"content": '{"verdict": "done", "confidence": 0.9}', "tool_calls": []},
    )

    output = check_and_act_on_item(item)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT detail FROM agent_decisions WHERE run_id = %s AND step_type = 'retrieval';",
                (output["run_id"],),
            )
            detail = cur.fetchone()["detail"]
    finally:
        conn.close()

    assert detail["num_results"] == 1


def test_check_and_act_on_item_logs_top_score_when_evidence_found(monkeypatch):
    """
    Regression test: Trigger 2's retrieval logging previously
    discarded the actual similarity score, always leaving the Report
    viewer's Retrieved Context panel showing 'Top score: —' even
    when real matching evidence was found.
    """
    from datetime import timedelta

    meeting_id = _create_meeting()
    action_item_id = _insert_action_item(meeting_id, "Fix the deploy pipeline", "alex", "2020-01-01")
    item = _fetch_item(action_item_id)
    activity_date = (item["created_at"].date() + timedelta(days=1)).isoformat()

    upsert_embedding(
        vertical="meeting_action_items",
        source_type="owner_activity",
        chunk_text="Alex fixed the deploy pipeline issue.",
        metadata={"owner": "alex", "date": activity_date},
    )

    monkeypatch.setattr(
        "app.verticals.meeting_action_items.followup.call_llm",
        lambda messages: {"content": '{"verdict": "done", "confidence": 0.9}', "tool_calls": []},
    )

    output = check_and_act_on_item(item)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT detail FROM agent_decisions WHERE run_id = %s AND step_type = 'retrieval';",
                (output["run_id"],),
            )
            detail = cur.fetchone()["detail"]
    finally:
        conn.close()

    assert detail["top_score"] is not None
    assert detail["top_score"] > 0


def test_check_and_act_on_item_logs_null_top_score_when_no_evidence(monkeypatch):
    meeting_id = _create_meeting()
    action_item_id = _insert_action_item(meeting_id, "Some unmatched task", "jamie", "2020-01-01")
    item = _fetch_item(action_item_id)

    monkeypatch.setattr(
        "app.verticals.meeting_action_items.followup.call_llm",
        lambda messages: {"content": '{"verdict": "no_evidence", "confidence": 0.5}', "tool_calls": []},
    )

    output = check_and_act_on_item(item)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT detail FROM agent_decisions WHERE run_id = %s AND step_type = 'retrieval';",
                (output["run_id"],),
            )
            detail = cur.fetchone()["detail"]
    finally:
        conn.close()

    assert detail["top_score"] is None
    assert detail["num_results"] == 0


def test_run_followup_check_separates_processed_from_skipped_noops(monkeypatch):
    """
    Confirms run_followup_check's summary correctly distinguishes
    real (logged) checks from skipped no-ops, across a mixed batch —
    one already-escalated item (skipped) and one fresh overdue item
    (processed).
    """
    meeting_id = _create_meeting()
    noop_item_id = _insert_action_item(
        meeting_id, "Stuck task", "jamie", "2020-01-01", nudge_count=2, escalated=True
    )
    fresh_item_id = _insert_action_item(
        meeting_id, "Fresh overdue task", "alex", "2020-01-01", nudge_count=0, escalated=False
    )

    monkeypatch.setattr(
        "app.verticals.meeting_action_items.followup.call_llm",
        lambda messages: {"content": '{"verdict": "no_evidence", "confidence": 0.5}', "tool_calls": []},
    )

    from app.verticals.meeting_action_items.followup import run_followup_check

    summary = run_followup_check()

    assert summary["checked"] == 2
    assert summary["processed"] == 1
    assert summary["skipped_noop"] == 1
    assert len(summary["results"]) == 1