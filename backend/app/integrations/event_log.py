"""
The integration_events table: a record of every message received.

Used for two things: the Live Feed page, and making a repeated
delivery harmless. Providers re-send a message when they do not get a
timely reply, so the same delivery can arrive twice; a partial unique
index on (source, delivery_id) lets only the first copy through.
"""

import uuid

from psycopg2 import errors
from psycopg2.extras import Json

from app.core.db import get_connection

_UPDATABLE = {"status", "summary", "owner", "run_id", "detail"}


def record_event(
    source: str,
    delivery_id: str,
    event_type: str | None,
    status: str,
    summary: str | None = None,
    owner: str | None = None,
    detail: dict | None = None,
) -> tuple[str, bool]:
    """
    Inserts one row. Returns (event_id, is_new). If a live row with the
    same (source, delivery_id) already exists, the new message is stored
    as a 'duplicate' row and is_new is False — the caller must not
    process it again.
    """
    conn = get_connection()
    try:
        try:
            event_id = _insert(conn, source, delivery_id, event_type, status, summary, owner, detail)
            conn.commit()
            return event_id, True
        except errors.UniqueViolation:
            conn.rollback()
            event_id = _insert(
                conn, source, delivery_id, event_type, "duplicate",
                "Already received — ignored (no second copy processed).", owner, detail,
            )
            conn.commit()
            return event_id, False
    finally:
        conn.close()


def _insert(conn, source, delivery_id, event_type, status, summary, owner, detail) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO integration_events
                (source, delivery_id, event_type, status, summary, owner, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (source, delivery_id, event_type, status, summary, owner,
             Json(detail) if detail is not None else None),
        )
        return str(cur.fetchone()["id"])


def record_rejected(source: str, event_type: str | None, reason: str, delivery_id: str | None = None) -> str:
    """A message that failed verification. Never processed; shown in the Live Feed."""
    event_id, _ = record_event(
        source=source,
        delivery_id=delivery_id or f"unverified-{uuid.uuid4()}",
        event_type=event_type,
        status="rejected",
        summary=reason,
    )
    return event_id


def update_event(event_id: str, **fields) -> None:
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"Cannot update fields: {sorted(unknown)}")
    if not fields:
        return
    assignments = []
    values = []
    for key, value in fields.items():
        assignments.append(f"{key} = %s")
        values.append(Json(value) if key == "detail" and value is not None else value)
    values.append(event_id)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE integration_events SET {', '.join(assignments)} WHERE id = %s;",
                tuple(values),
            )
        conn.commit()
    finally:
        conn.close()


def list_events(limit: int = 50, source: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if source:
                cur.execute(
                    "SELECT * FROM integration_events WHERE source = %s "
                    "ORDER BY received_at DESC LIMIT %s;",
                    (source, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM integration_events ORDER BY received_at DESC LIMIT %s;",
                    (limit,),
                )
            return cur.fetchall()
    finally:
        conn.close()
