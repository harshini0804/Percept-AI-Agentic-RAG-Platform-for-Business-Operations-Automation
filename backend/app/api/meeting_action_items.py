"""
Meeting Action-Item Enforcement (Vertical 4, Section 8.4) — Tracker
API. Section 8.4's own description: "a living tracker view, not a
one-time report — status changes across repeated visits as the daily
job runs." This queries action_items directly, not agent_runs, since
an item's state accumulates across multiple separate Trigger 1/
Trigger 2 runs over time — no single run's detail page can represent
"this item's current status" the way it can for the other verticals.

Also provides the manual "mark resolved" override discussed and
deferred from PR 4: an item can only ever auto-resolve when Trigger 2
finds matching owner_activity evidence — but real resolution often
happens through channels the system can never observe (a verbal
conversation with the manager, an email outside the tracked tools).
Without a manual override, such an item would stay escalated forever.
This is a genuinely different kind of event from an agent's own
action — a HUMAN decided this is done, not the LLM — so it does NOT
fabricate a fake agent_run (that would misrepresent who made the
call). It's a direct, honest status update, nothing more.
"""

from fastapi import APIRouter, HTTPException, Query
from app.core.db import get_connection
from app.schemas.api_models import ActionItemTrackerEntry

router = APIRouter(prefix="/meeting-action-items", tags=["meeting-action-items"])


@router.get("/tracker", response_model=list[ActionItemTrackerEntry])
def list_tracker_items(status: str | None = Query(default=None)):
    """
    Lists every action item's current status, optionally filtered by
    status (open/resolved). Ordered so escalated, still-open items
    surface first — the ones most likely to need a human's attention.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT * FROM action_items WHERE status = %s "
                    "ORDER BY escalated DESC, created_at DESC;",
                    (status,),
                )
            else:
                cur.execute(
                    "SELECT * FROM action_items ORDER BY escalated DESC, created_at DESC;"
                )
            rows = cur.fetchall()
            return [ActionItemTrackerEntry(**row) for row in rows]
    finally:
        conn.close()


@router.post("/{action_item_id}/resolve", response_model=ActionItemTrackerEntry)
def resolve_action_item_manually(action_item_id: str):
    """
    Manual human override — marks an item resolved directly,
    bypassing the LLM verdict requirement entirely. Intended for
    items resolved through channels the system has no visibility
    into. escalated is left as-is (a true historical record that
    this item WAS escalated at some point), only status changes.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE action_items SET status = 'resolved' WHERE id = %s RETURNING *;",
                (action_item_id,),
            )
            updated = cur.fetchone()
            if not updated:
                raise HTTPException(status_code=404, detail="Action item not found.")
            conn.commit()
            return ActionItemTrackerEntry(**updated)
    finally:
        conn.close()