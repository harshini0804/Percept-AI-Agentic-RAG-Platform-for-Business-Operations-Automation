"""
Tests for app.core.logging_service (Section 3.1). Requires a real
database (provided by conftest.py's fixtures) — this module is pure
DB reads/writes, nothing worth mocking.
"""

import pytest
from app.core.db import get_connection
from app.core.logging_service import (
    create_agent_run,
    log_decision,
    complete_agent_run,
    update_run_status,
    create_escalation,
    create_notification,
)


def _fetch_run(run_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM agent_runs WHERE id = %s;", (run_id,))
            return cur.fetchone()
    finally:
        conn.close()


def test_create_agent_run_starts_in_running_status():
    run_id = create_agent_run(vertical="dummy", trigger_type="upload")
    row = _fetch_run(run_id)
    assert row["status"] == "running"
    assert row["vertical"] == "dummy"
    assert row["confidence"] is None


def test_log_decision_stores_jsonb_detail(existing_run_id):
    log_decision(existing_run_id, "retrieval", {"top_score": 0.8, "num_results": 3})

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT step_type, detail FROM agent_decisions WHERE run_id = %s;",
                (existing_run_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row["step_type"] == "retrieval"
    assert row["detail"] == {"top_score": 0.8, "num_results": 3}


def test_log_decision_rejects_invalid_step_type_before_hitting_db(existing_run_id):
    with pytest.raises(ValueError, match="Invalid step_type"):
        log_decision(existing_run_id, "not_a_real_step_type", {})


def test_complete_agent_run_sets_status_and_confidence(existing_run_id):
    complete_agent_run(existing_run_id, status="completed", confidence=0.91)
    row = _fetch_run(existing_run_id)
    assert row["status"] == "completed"
    assert float(row["confidence"]) == pytest.approx(0.91)


def test_update_run_status_does_not_touch_confidence(existing_run_id):
    """
    Regression test for the bug caught while building the escalations
    API: resolving an escalation must not reset the run's original
    confidence score back to 0 or null.
    """
    complete_agent_run(existing_run_id, status="escalated", confidence=0.42)
    update_run_status(existing_run_id, status="completed")

    row = _fetch_run(existing_run_id)
    assert row["status"] == "completed"
    assert float(row["confidence"]) == pytest.approx(0.42)


def test_create_escalation_stores_pending_action(existing_run_id):
    escalation_id = create_escalation(
        existing_run_id,
        reason="Confidence below threshold.",
        pending_action={"tool_name": "log_dummy_action", "arguments": {"note": "x"}},
    )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reason, status, pending_action FROM escalations WHERE id = %s;",
                (escalation_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row["status"] == "open"
    assert row["pending_action"] == {"tool_name": "log_dummy_action", "arguments": {"note": "x"}}


def test_create_escalation_without_pending_action(existing_run_id):
    """Covers the 'nothing to do here' escalation case (no action was
    ever proposed) — pending_action should be storable as NULL."""
    escalation_id = create_escalation(existing_run_id, reason="No action proposed.")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pending_action FROM escalations WHERE id = %s;", (escalation_id,)
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row["pending_action"] is None


def test_create_notification(existing_run_id):
    notification_id = create_notification(
        existing_run_id, recipient="test@example.com", message="Hello."
    )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT recipient, message, read FROM notifications WHERE id = %s;",
                (notification_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row["recipient"] == "test@example.com"
    assert row["read"] is False


def test_log_decision_foreign_key_rejects_nonexistent_run():
    """agent_decisions.run_id has a NOT NULL FK to agent_runs — this
    should fail loudly, not silently, for a run_id that doesn't exist."""
    import uuid
    import psycopg2

    fake_run_id = str(uuid.uuid4())
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        log_decision(fake_run_id, "retrieval", {})