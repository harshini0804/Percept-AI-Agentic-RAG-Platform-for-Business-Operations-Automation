"""
Logging Service (Section 3.1) — writes every retrieval, tool call,
and decision to the audit trail (agent_decisions), plus the
run-level lifecycle (agent_runs) and the two conditional side-effect
tables that feed shared UI screens: escalations (HITL queue) and
notifications (Notifications panel).
"""

from psycopg2.extras import Json
from app.core.db import get_connection

VALID_STEP_TYPES = {"retrieval", "tool_call", "llm_reasoning", "action", "escalation"}


def create_agent_run(
    vertical: str,
    trigger_type: str,
    input_document_id: str | None = None,
) -> str:
    """
    Opens a new run — called once, at the very start of a vertical's
    pipeline, before Stage 1 (Embed). Returns the run_id every other
    function in this module needs.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_runs
                    (vertical, trigger_type, input_document_id, status)
                VALUES (%s, %s, %s, %s)
                RETURNING id;
                """,
                (vertical, trigger_type, input_document_id, "running"),
            )
            run_id = cur.fetchone()["id"]
        conn.commit()
        return str(run_id)
    finally:
        conn.close()


def log_decision(run_id: str, step_type: str, detail: dict) -> str:
    """
    Records one step of the audit trail (Section 3.1). Called
    repeatedly throughout a run — once per retrieval, tool call,
    LLM reasoning step, action, or escalation.
    """
    if step_type not in VALID_STEP_TYPES:
        raise ValueError(
            f"Invalid step_type '{step_type}'. Must be one of: {sorted(VALID_STEP_TYPES)}"
        )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_decisions (run_id, step_type, detail)
                VALUES (%s, %s, %s)
                RETURNING id;
                """,
                (run_id, step_type, Json(detail)),
            )
            decision_id = cur.fetchone()["id"]
        conn.commit()
        return str(decision_id)
    finally:
        conn.close()


def complete_agent_run(run_id: str, status: str, confidence: float) -> None:
    """
    Closes out a run — called once, at Stage 5, after the confidence
    gate has decided the run's final outcome.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agent_runs
                SET status = %s, confidence = %s
                WHERE id = %s;
                """,
                (status, confidence, run_id),
            )
        conn.commit()
    finally:
        conn.close()


def create_escalation(run_id: str, reason: str, assigned_to: str | None = None) -> str:
    """
    Routes a run to the HITL queue (Section 3.1 guardrails) when
    confidence falls below the action threshold. Feeds the
    Escalation/HITL queue shared UI screen (Section 5).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO escalations (run_id, reason, assigned_to, status)
                VALUES (%s, %s, %s, %s)
                RETURNING id;
                """,
                (run_id, reason, assigned_to, "open"),
            )
            escalation_id = cur.fetchone()["id"]
        conn.commit()
        return str(escalation_id)
    finally:
        conn.close()


def create_notification(run_id: str, recipient: str, message: str) -> str:
    """
    Records an auto-sent message (e.g. notify_candidate(),
    send_nudge()). Feeds the Notifications panel shared UI screen
    (Section 5). Can be called multiple times per run — e.g. once
    per qualifying candidate in Vertical 2.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO notifications (run_id, recipient, message, read)
                VALUES (%s, %s, %s, %s)
                RETURNING id;
                """,
                (run_id, recipient, message, False),
            )
            notification_id = cur.fetchone()["id"]
        conn.commit()
        return str(notification_id)
    finally:
        conn.close()