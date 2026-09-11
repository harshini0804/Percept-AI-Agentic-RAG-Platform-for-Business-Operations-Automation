"""
Internal Mobility & Skill-Gap Matching Agent — Tools (Vertical 2,
Section 8.2).

Two tools, registered with the shared tool-calling framework
(app.core.tool_registry):

Read tools:
  - check_capacity: a DIRECT lookup of an employee's current workload
    (employee_workload) — utilization percentage and when they free up.
    Used by the LLM as an optional "audit current assignments" step on
    frontrunner candidates. IMPORTANT (Section 8.2 decision mechanics):
    capacity is NEVER a hard structural filter on retrieval — it would
    hide a busy-but-elite candidate. It is only an optional tool lookup
    and a UI badge.

Write tools:
  - notify_candidate: sends an automated invitation to apply. Inserts
    (or flips) the candidate's role_matches row to notified=TRUE and
    posts a notification via the shared notifications table. Additive
    and non-destructive (Section 8.2 Autonomous Operations).

role_matches logging (Section 8.2 step 7: "Profiles are logged into
role_matches") is handled by record_role_match() — a plain helper (not
a registered tool) so the graph can log EVERY candidate, notified or
not, with notified=False for those that stay on the manager's board
without an alert. notify_candidate() reuses this helper with
notified=True so the DB write logic lives in one place.
"""

import re
import unicodedata

from app.core.tool_registry import tool
from app.core.db import get_connection
from app.core.logging_service import create_notification


def _employee_email(employee_id: str) -> str | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM employees WHERE id = %s;", (employee_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None or not row["name"]:
        return None
    name = unicodedata.normalize("NFKD", row["name"])
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    local = re.sub(r"[^a-z0-9]+", ".", name.lower())
    return local.strip(".") + "@example.com"


def _resolve_recipient(employee_id: str) -> str:
    email = _employee_email(employee_id)
    if email is None:
        raise ValueError(f"cannot derive a recipient email for employee {employee_id}")
    return email


def record_role_match(
    run_id: str,
    role_id: str,
    employee_id: str,
    rank: int,
    rationale: str,
    confidence: float,
    notified: bool,
) -> str:
    """
    Inserts a role_matches row and returns its id. Used for every
    candidate the agent ranks — the notified flag is what distinguishes
    an auto-notified candidate from one that merely appears on the
    manager's board.

    Idempotence: if a (run_id, role_id, employee_id) row already
    exists, updates it instead of inserting a duplicate, so re-running
    or re-notifying never creates stray rows.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM role_matches
                WHERE run_id = %s AND role_id = %s AND employee_id = %s
                """,
                (run_id, role_id, employee_id),
            )
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    """
                    UPDATE role_matches
                    SET rank = %s, rationale = %s, confidence = %s, notified = %s
                    WHERE id = %s
                    RETURNING id;
                    """,
                    (rank, rationale, confidence, notified, existing["id"]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO role_matches (run_id, role_id, employee_id, rank, rationale, confidence, notified)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (run_id, role_id, employee_id, rank, rationale, confidence, notified),
                )
            match_id = cur.fetchone()["id"]
        conn.commit()
        return str(match_id)
    finally:
        conn.close()


@tool(
    vertical="internal_mobility",
    name="check_capacity",
    description=(
        "Look up an employee's current workload and when they are next "
        "free. Use this after ranking a frontrunner candidate, to audit "
        "whether they have short-term capacity to take on the role. This is "
        "an optional badge signal only — it must NEVER be used to exclude a "
        "candidate from consideration."
    ),
    parameters={
        "type": "object",
        "properties": {
            "employee_id": {
                "type": "string",
                "description": "The employee's id to check capacity for.",
            },
        },
        "required": ["employee_id"],
    },
    tool_type="read",
)
def check_capacity(employee_id: str) -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT employee_id, utilization_pct, free_by_date, updated_at
                FROM employee_workload
                WHERE employee_id = %s;
                """,
                (employee_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        return {
            "employee_id": employee_id,
            "found": False,
            "available": None,
            "utilization_pct": None,
            "free_by_date": None,
        }

    util = row["utilization_pct"]
    return {
        "employee_id": str(row["employee_id"]),
        "found": True,
        "utilization_pct": util,
        "free_by_date": str(row["free_by_date"]) if row["free_by_date"] else None,
        # Simple heuristic: capacity is available when utilization is
        # below 100% (i.e. not fully committed right now).
        "available": util is not None and util < 100,
    }


@tool(
    vertical="internal_mobility",
    name="notify_candidate",
    description=(
        "Send an automated invitation to apply to a candidate. Only invoke "
        "this for a candidate whose fit confidence meets the threshold AND "
        "whose short-term capacity has been verified (available). Additive "
        "only — never modifies the candidate's profile or the role."
    ),
    parameters={
        "type": "object",
        "properties": {
            "employee_id": {"type": "string"},
            "role_id": {"type": "string"},
            "run_id": {"type": "string"},
            "rank": {"type": "integer"},
            "rationale": {
                "type": "string",
                "description": "Brief rationale for why this candidate fits.",
            },
            "confidence": {"type": "number"},
            "message": {
                "type": "string",
                "description": "The invitation message to the candidate.",
            },
        },
        "required": ["employee_id", "role_id", "run_id", "rank", "rationale", "confidence", "message"],
    },
    tool_type="write",
)
def notify_candidate(
    employee_id: str,
    role_id: str,
    run_id: str,
    rank: int,
    rationale: str,
    confidence: float,
    message: str,
) -> dict:
    match_id = record_role_match(
        run_id=run_id,
        role_id=role_id,
        employee_id=employee_id,
        rank=rank,
        rationale=rationale,
        confidence=confidence,
        notified=True,
    )
    recipient = _resolve_recipient(employee_id)
    notification_id = create_notification(
        run_id=run_id,
        recipient=recipient,
        message=message,
    )
    return {
        "notified": True,
        "match_id": match_id,
        "employee_id": employee_id,
        "role_id": role_id,
        "recipient": recipient,
        "notification_id": notification_id,
    }
