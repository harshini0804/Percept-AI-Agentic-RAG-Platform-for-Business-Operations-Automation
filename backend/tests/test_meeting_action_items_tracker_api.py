"""
Tests for app.api.meeting_action_items (Vertical 4, PR 5) — the
Tracker view's backend: listing every action item's current status,
and the manual resolve override. Pure DB, no embedding model needed.
"""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.db import get_connection

client = TestClient(app)


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
    meeting_id: str, description: str = "Task", owner: str = "alex",
    status: str = "open", nudge_count: int = 0, escalated: bool = False,
) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO action_items
                    (meeting_id, description, owner, status, nudge_count, escalated)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (meeting_id, description, owner, status, nudge_count, escalated),
            )
            item_id = str(cur.fetchone()["id"])
        conn.commit()
        return item_id
    finally:
        conn.close()


# ---------------------------------------------------------------
# GET /meeting-action-items/tracker
# ---------------------------------------------------------------

def test_list_tracker_items_returns_all_items():
    meeting_id = _create_meeting()
    _insert_action_item(meeting_id, "First task", "alex")
    _insert_action_item(meeting_id, "Second task", "priya", status="resolved")

    response = client.get("/meeting-action-items/tracker")
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 2


def test_list_tracker_items_filters_by_status():
    meeting_id = _create_meeting()
    _insert_action_item(meeting_id, "Open task", "alex", status="open")
    _insert_action_item(meeting_id, "Done task", "priya", status="resolved")

    response = client.get("/meeting-action-items/tracker?status=open")
    results = response.json()
    assert len(results) == 1
    assert results[0]["description"] == "Open task"


def test_list_tracker_items_orders_escalated_first():
    meeting_id = _create_meeting()
    _insert_action_item(meeting_id, "Not escalated", "alex", escalated=False)
    _insert_action_item(meeting_id, "Escalated", "priya", escalated=True)

    response = client.get("/meeting-action-items/tracker")
    results = response.json()
    assert results[0]["description"] == "Escalated"


def test_list_tracker_items_includes_all_expected_fields():
    meeting_id = _create_meeting()
    item_id = _insert_action_item(meeting_id, "A task", "morgan", nudge_count=2, escalated=True)

    response = client.get("/meeting-action-items/tracker")
    result = response.json()[0]

    assert result["id"] == item_id
    assert result["meeting_id"] == meeting_id
    assert result["description"] == "A task"
    assert result["owner"] == "morgan"
    assert result["status"] == "open"
    assert result["nudge_count"] == 2
    assert result["escalated"] is True
    assert result["is_recurring"] is False
    assert "created_at" in result


def test_list_tracker_items_empty_is_ok():
    response = client.get("/meeting-action-items/tracker")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------
# POST /meeting-action-items/{id}/resolve
# ---------------------------------------------------------------

def test_resolve_action_item_manually_sets_status_resolved():
    meeting_id = _create_meeting()
    item_id = _insert_action_item(meeting_id, "Stuck task", "jamie", escalated=True, nudge_count=2)

    response = client.post(f"/meeting-action-items/{item_id}/resolve")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"


def test_resolve_action_item_manually_preserves_escalated_history():
    """
    escalated stays True after manual resolution — an honest
    historical record that this item WAS escalated at some point,
    not silently erased.
    """
    meeting_id = _create_meeting()
    item_id = _insert_action_item(meeting_id, "Stuck task", "jamie", escalated=True)

    response = client.post(f"/meeting-action-items/{item_id}/resolve")
    body = response.json()
    assert body["escalated"] is True
    assert body["status"] == "resolved"


def test_resolve_action_item_manually_reflected_in_tracker_list():
    meeting_id = _create_meeting()
    item_id = _insert_action_item(meeting_id, "Task", "alex")

    client.post(f"/meeting-action-items/{item_id}/resolve")

    response = client.get("/meeting-action-items/tracker?status=resolved")
    results = response.json()
    assert len(results) == 1
    assert results[0]["id"] == item_id


def test_resolve_nonexistent_action_item_returns_404():
    import uuid

    response = client.post(f"/meeting-action-items/{uuid.uuid4()}/resolve")
    assert response.status_code == 404


def test_resolve_action_item_does_not_create_an_agent_run():
    """
    Confirms the manual override is honestly represented as a direct
    status change, not disguised as an agent action — no new
    agent_runs row should appear from this endpoint.
    """
    meeting_id = _create_meeting()
    item_id = _insert_action_item(meeting_id, "Task", "alex")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM agent_runs;")
            runs_before = cur.fetchone()["c"]
    finally:
        conn.close()

    client.post(f"/meeting-action-items/{item_id}/resolve")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM agent_runs;")
            runs_after = cur.fetchone()["c"]
    finally:
        conn.close()

    assert runs_after == runs_before