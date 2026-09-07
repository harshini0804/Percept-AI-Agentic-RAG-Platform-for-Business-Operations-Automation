"""
Tests for app.verticals.meeting_action_items.graph (Vertical 4,
Trigger 1, PR 2).

Split into two groups:
- Pure logic / DB-only tests (no real embedding model needed) —
  fully runnable anywhere.
- Full run_meeting_action_items() integration tests — need the real
  embedding model (network access to huggingface.co on first run in
  a fresh environment), same as test_dummy_vertical_integration.py.
  Only call_llm is mocked.
"""

import pytest
from app.core.db import get_connection
from app.core.documents import create_document
from app.schemas.agent_contract import AgentRunInput, TriggerType
from app.verticals.meeting_action_items.graph import (
    _create_meeting,
    _insert_action_item,
    _extract_candidate_items,
    run_meeting_action_items,
)


def _fetch_action_items(meeting_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM action_items WHERE meeting_id = %s ORDER BY created_at;",
                (meeting_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------
# Pure logic / DB-only (no embedding model needed)
# ---------------------------------------------------------------

def test_extract_candidate_items_happy_path(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.graph.call_llm",
        lambda messages: {
            "content": '[{"description": "Fix the deploy script", "owner": "alex", "deadline": "2026-01-15"}]',
            "tool_calls": [],
        },
    )

    items = _extract_candidate_items("some transcript")
    assert items == [
        {"description": "Fix the deploy script", "owner": "alex", "deadline": "2026-01-15"}
    ]


def test_extract_candidate_items_returns_empty_list_for_malformed_json(monkeypatch):
    """Fails SAFE — malformed LLM output must not crash the run."""
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.graph.call_llm",
        lambda messages: {"content": "not valid json", "tool_calls": []},
    )
    assert _extract_candidate_items("transcript") == []


def test_extract_candidate_items_returns_empty_list_for_non_list_json(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.graph.call_llm",
        lambda messages: {"content": '{"not": "a list"}', "tool_calls": []},
    )
    assert _extract_candidate_items("transcript") == []


def test_extract_candidate_items_filters_out_malformed_entries(monkeypatch):
    """A mix of well-formed and malformed items — only the
    well-formed ones (with both description and owner) should survive."""
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.graph.call_llm",
        lambda messages: {
            "content": (
                '[{"description": "Good item", "owner": "alex", "deadline": null}, '
                '{"missing_owner": true}, '
                '"not even a dict"]'
            ),
            "tool_calls": [],
        },
    )
    items = _extract_candidate_items("transcript")
    assert items == [{"description": "Good item", "owner": "alex", "deadline": None}]


def test_extract_candidate_items_empty_array_for_no_items(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.graph.call_llm",
        lambda messages: {"content": "[]", "tool_calls": []},
    )
    assert _extract_candidate_items("transcript") == []


def test_create_meeting_with_and_without_doc_id():
    meeting_id_no_doc = _create_meeting(None)
    assert meeting_id_no_doc is not None

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT doc_id FROM meetings WHERE id = %s;", (meeting_id_no_doc,))
            assert cur.fetchone()["doc_id"] is None
    finally:
        conn.close()


def test_insert_action_item_stores_all_fields():
    meeting_id = _create_meeting(None)
    action_item_id = _insert_action_item(
        meeting_id, "Do the thing", "priya", "2026-02-01", True, None
    )

    row = _fetch_action_items(meeting_id)[0]
    assert str(row["id"]) == action_item_id
    assert row["description"] == "Do the thing"
    assert row["owner"] == "priya"
    assert row["is_recurring"] is True
    assert row["status"] == "open"
    assert row["nudge_count"] == 0
    assert row["escalated"] is False


def test_insert_action_item_recurring_from_reference():
    meeting_id = _create_meeting(None)
    original_id = _insert_action_item(meeting_id, "Original item", "alex", None, False, None)
    duplicate_id = _insert_action_item(
        meeting_id, "Duplicate item", "alex", None, True, original_id
    )

    row = [r for r in _fetch_action_items(meeting_id) if str(r["id"]) == duplicate_id][0]
    assert str(row["recurring_from"]) == original_id


# ---------------------------------------------------------------
# Full pipeline (needs real embedding model — network access to
# huggingface.co required on first run in a fresh environment)
# ---------------------------------------------------------------

def test_run_meeting_action_items_creates_items_and_persists_embeddings(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.graph.call_llm",
        lambda messages: {
            "content": (
                '[{"description": "Update the API docs", "owner": "morgan", "deadline": null}, '
                '{"description": "Review the PR", "owner": "jamie", "deadline": "2026-03-01"}]'
            ),
            "tool_calls": [],
        },
    )

    agent_input = AgentRunInput(
        vertical="meeting_action_items",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "some transcript text"},
    )
    output = run_meeting_action_items(agent_input)

    assert output.status == "completed"
    assert output.confidence == 1.0
    assert output.escalated is False
    assert len(output.actions_taken) == 2
    assert {a.action_name for a in output.actions_taken} == {"create_action_item"}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT chunk_text FROM embeddings WHERE vertical = 'meeting_action_items' "
                "AND source_type = 'action_item' ORDER BY chunk_text;"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    chunk_texts = {r["chunk_text"] for r in rows}
    assert "Update the API docs" in chunk_texts
    assert "Review the PR" in chunk_texts

def test_run_meeting_action_items_logs_an_action_decision_per_item(monkeypatch):
    """
    Regression test: the Report/Result viewer's Action Taken panel
    reads agent_decisions for step_type='action' — this was
    previously never logged for this vertical, leaving that panel
    blank even though real action items were being created.
    """
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.graph.call_llm",
        lambda messages: {
            "content": (
                '[{"description": "Item one", "owner": "alex", "deadline": null}, '
                '{"description": "Item two", "owner": "priya", "deadline": null}]'
            ),
            "tool_calls": [],
        },
    )

    agent_input = AgentRunInput(
        vertical="meeting_action_items",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "transcript"},
    )
    output = run_meeting_action_items(agent_input)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT detail FROM agent_decisions WHERE run_id = %s AND step_type = 'action' "
                "ORDER BY created_at;",
                (output.run_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    assert len(rows) == 2
    assert rows[0]["detail"]["action_name"] == "create_action_item"
    assert {r["detail"]["owner"] for r in rows} == {"alex", "priya"}


def test_run_meeting_action_items_detects_recurrence(monkeypatch):
    """
    Seeds one existing open action item for 'alex', then extracts a
    near-duplicate — expects is_recurring=True and recurring_from
    pointing at the seeded item.
    """
    from app.core.embeddings import upsert_embedding

    meeting_id = _create_meeting(None)
    original_id = _insert_action_item(
        meeting_id, "Fix the deployment pipeline", "alex", None, False, None
    )
    upsert_embedding(
        vertical="meeting_action_items",
        source_type="action_item",
        chunk_text="Fix the deployment pipeline",
        source_id=original_id,
    )

    monkeypatch.setattr(
        "app.verticals.meeting_action_items.graph.call_llm",
        lambda messages: {
            "content": '[{"description": "Fix the deployment pipeline", "owner": "alex", "deadline": null}]',
            "tool_calls": [],
        },
    )

    agent_input = AgentRunInput(
        vertical="meeting_action_items",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "transcript mentioning the deployment pipeline again"},
    )
    output = run_meeting_action_items(agent_input)

    new_item_id = output.actions_taken[0].target_id
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT is_recurring, recurring_from FROM action_items WHERE id = %s;", (new_item_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    assert row["is_recurring"] is True
    assert str(row["recurring_from"]) == original_id


def test_run_meeting_action_items_resolves_input_document_id(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.graph.call_llm",
        lambda messages: {"content": "[]", "tool_calls": []},
    )

    document_id = create_document(
        vertical="meeting_action_items",
        filename="transcript.txt",
        raw_bytes=b"A transcript with no action items.",
    )
    agent_input = AgentRunInput(
        vertical="meeting_action_items",
        trigger_type=TriggerType.UPLOAD,
        input_document_id=document_id,
    )
    output = run_meeting_action_items(agent_input)

    assert output.status == "completed"
    assert output.actions_taken == []


def test_run_meeting_action_items_malformed_llm_output_completes_with_no_items(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.graph.call_llm",
        lambda messages: {"content": "not valid json", "tool_calls": []},
    )

    agent_input = AgentRunInput(
        vertical="meeting_action_items",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "transcript"},
    )
    output = run_meeting_action_items(agent_input)

    assert output.status == "completed"
    assert output.actions_taken == []
    assert output.escalated is False