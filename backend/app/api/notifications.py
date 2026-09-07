"""
Notifications API (Category A) — powers the Notifications panel
shared UI screen (Section 5): "in-app list of auto-sent
nudges/notifications."
"""

from fastapi import APIRouter, HTTPException, Query
from app.core.db import get_connection
from app.schemas.api_models import NotificationSummary

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationSummary])
def list_notifications(
    vertical: str | None = Query(default=None),
    recipient: str | None = Query(default=None),
    unread_only: bool = Query(default=False),
    limit: int = 50,
):
    """Lists notifications, optionally filtered by vertical, recipient, or unread status."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            sql = """
                SELECT n.id, n.run_id, r.vertical, n.recipient, n.message, n.read, n.created_at
                FROM notifications n
                JOIN agent_runs r ON r.id = n.run_id
                WHERE 1=1
            """
            params: tuple = ()

            if vertical:
                sql += " AND r.vertical = %s"
                params += (vertical,)

            if recipient:
                sql += " AND n.recipient = %s"
                params += (recipient,)

            if unread_only:
                sql += " AND n.read = FALSE"

            sql += " ORDER BY n.created_at DESC LIMIT %s;"
            params += (limit,)

            cur.execute(sql, params)
            rows = cur.fetchall()
            return [NotificationSummary(**row) for row in rows]
    finally:
        conn.close()


@router.post("/{notification_id}/mark-read", response_model=NotificationSummary)
def mark_notification_read(notification_id: str):
    """Marks a single notification as read."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE notifications SET read = TRUE
                WHERE id = %s
                RETURNING id, run_id, recipient, message, read, created_at;
                """,
                (notification_id,),
            )
            updated = cur.fetchone()
            if not updated:
                raise HTTPException(status_code=404, detail="Notification not found.")

            cur.execute(
                "SELECT vertical FROM agent_runs WHERE id = %s;", (updated["run_id"],)
            )
            run_row = cur.fetchone()
            conn.commit()

            return NotificationSummary(**updated, vertical=run_row["vertical"])
    finally:
        conn.close()