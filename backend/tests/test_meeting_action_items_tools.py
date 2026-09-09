"""
Tests for app.verticals.meeting_action_items.tools (Vertical 4, PR 1).

action_items has a NOT NULL FK to meetings, which has no shared
factory function yet (that arrives with graph.py in PR 2) — a small
local fixture inserts both directly via raw SQL, matching the
project's existing pattern for FK setup in tests written before the
relevant shared factory exists.
"""

import pytest
from app.core.db import get_connection
from app.core.tool_registry import get_tools_for_vertical, execute_tool
from app.verticals.meeting_action_items.tools import (
    send_nudge,
    escalate_to_manager,
    _resolve_manager,
    MANAGER_MAP,
)


@pytest.fixture
def action_item(existing_run_id):
    """
    Creates a real meetings row + action_items row, returns the
    action_item's id. existing_run_id (from conftest.py) isn't
    directly referenced by action_items, but tools.py's functions
    need a valid run_id for the notifications FK — existing_run_id
    provides that.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO meetings (meeting_date) VALUES (now()) RETURNING id;")
            meeting_id = cur.fetchone()["id"]

            cur.execute(
                """
                INSERT INTO action_items (meeting_id, description, owner)
                VALUES (%s, %s, %s)
                RETURNING id;
                """,
                (meeting_id, "Follow up on the deployment checklist.", "alex"),
            )
            action_item_id = cur.fetchone()["id"]
        conn.commit()
        return str(action_item_id)
    finally:
        conn.close()


def _fetch_row(action_item_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM action_items WHERE id = %s;", (action_item_id,))
            return cur.fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------
# Registration
# ---------------------------------------------------------------

def test_both_tools_are_registered():
    schemas = get_tools_for_vertical("meeting_action_items")
    names = {s["name"] for s in schemas}
    assert names == {"send_nudge", "escalate_to_manager"}


def test_registered_schemas_only_expose_action_item_id():
    """
    run_id is deliberately NOT part of the LLM-facing schema (it's
    system context, not something an LLM should decide) — this test
    locks that design choice in.
    """
    for schema in get_tools_for_vertical("meeting_action_items"):
        assert set(schema["parameters"]["properties"].keys()) == {"action_item_id"}


# ---------------------------------------------------------------
# send_nudge
# ---------------------------------------------------------------

def test_send_nudge_increments_nudge_count(existing_run_id, action_item):
    result = send_nudge(run_id=existing_run_id, action_item_id=action_item)

    assert result["nudge_count"] == 1
    row = _fetch_row(action_item)
    assert row["nudge_count"] == 1
    assert row["escalated"] is False


def test_send_nudge_creates_notification_to_owner(existing_run_id, action_item):
    send_nudge(run_id=existing_run_id, action_item_id=action_item)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT recipient, message FROM notifications WHERE run_id = %s;",
                (existing_run_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row["recipient"] == "alex@example.com"
    assert "still open" in row["message"]


def test_send_nudge_called_twice_increments_each_time(existing_run_id, action_item):
    send_nudge(run_id=existing_run_id, action_item_id=action_item)
    result = send_nudge(run_id=existing_run_id, action_item_id=action_item)

    assert result["nudge_count"] == 2


def test_send_nudge_raises_for_unknown_action_item(existing_run_id):
    with pytest.raises(ValueError, match="No action_item found"):
        send_nudge(run_id=existing_run_id, action_item_id="00000000-0000-0000-0000-000000000000")


# ---------------------------------------------------------------
# escalate_to_manager
# ---------------------------------------------------------------

def test_escalate_to_manager_sets_escalated_flag(existing_run_id, action_item):
    escalate_to_manager(run_id=existing_run_id, action_item_id=action_item)

    row = _fetch_row(action_item)
    assert row["escalated"] is True


def test_escalate_to_manager_notifies_the_mapped_manager(existing_run_id, action_item):
    """action_item's owner is 'alex', mapped in MANAGER_MAP."""
    result = escalate_to_manager(run_id=existing_run_id, action_item_id=action_item)

    assert result["notified"] == MANAGER_MAP["alex"]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT recipient, message FROM notifications WHERE run_id = %s;",
                (existing_run_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row["recipient"] == MANAGER_MAP["alex"]
    assert "Escalation" in row["message"]


def test_escalate_to_manager_falls_back_for_unmapped_owner(existing_run_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO meetings (meeting_date) VALUES (now()) RETURNING id;")
            meeting_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO action_items (meeting_id, description, owner) "
                "VALUES (%s, %s, %s) RETURNING id;",
                (meeting_id, "Some task.", "an_owner_not_in_the_map"),
            )
            action_item_id = str(cur.fetchone()["id"])
        conn.commit()
    finally:
        conn.close()

    result = escalate_to_manager(run_id=existing_run_id, action_item_id=action_item_id)

    assert result["notified"] == "manager-of-an_owner_not_in_the_map@example.com"


def test_escalate_to_manager_raises_for_unknown_action_item(existing_run_id):
    with pytest.raises(ValueError, match="No action_item found"):
        escalate_to_manager(
            run_id=existing_run_id, action_item_id="00000000-0000-0000-0000-000000000000"
        )


def test_resolve_manager_is_case_insensitive():
    assert _resolve_manager("Alex") == MANAGER_MAP["alex"]
    assert _resolve_manager("PRIYA") == MANAGER_MAP["priya"]


# ---------------------------------------------------------------
# Invoked via the shared tool registry (the real calling convention
# followup.py, PR 3, will use)
# ---------------------------------------------------------------

def test_send_nudge_via_execute_tool(existing_run_id, action_item):
    result = execute_tool(
        "meeting_action_items",
        "send_nudge",
        {"run_id": existing_run_id, "action_item_id": action_item},
    )
    assert result["nudge_count"] == 1


def test_escalate_to_manager_via_execute_tool(existing_run_id, action_item):
    result = execute_tool(
        "meeting_action_items",
        "escalate_to_manager",
        {"run_id": existing_run_id, "action_item_id": action_item},
    )
    assert result["escalated"] is True