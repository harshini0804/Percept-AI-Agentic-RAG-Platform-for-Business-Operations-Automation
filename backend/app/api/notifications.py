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
    recipient: str | None = Query(default=None),
    unread_only: bool = Query(default=False),
    limit: int = 50,
):
    """Lists notifications, optionally filtered by recipient or unread status."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            sql = "SELECT id, run_id, recipient, message, read, created_at FROM notifications WHERE 1=1"
            params: tuple = ()

            if recipient:
                sql += " AND recipient = %s"
                params += (recipient,)

            if unread_only:
                sql += " AND read = FALSE"

            sql += " ORDER BY created_at DESC LIMIT %s;"
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
            conn.commit()
            if not updated:
                raise HTTPException(status_code=404, detail="Notification not found.")
            return NotificationSummary(**updated)
    finally:
        conn.close()