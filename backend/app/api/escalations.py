"""
Escalations API (Category A) — powers the Escalation/HITL queue
shared UI screen (Section 5). Includes the resolve-and-approve flow
(Option B, agreed decision): approving an escalation fires the
pending_action that was stored at escalation time (Section 3.1
guardrails — a human's approval is meant to result in the action
actually happening, not a dead end).
"""

from fastapi import APIRouter, HTTPException, Query
from app.core.db import get_connection  
from app.core.tool_registry import execute_tool
from app.core.logging_service import log_decision, update_run_status
from app.schemas.api_models import EscalationSummary, ResolveEscalationRequest

router = APIRouter(prefix="/escalations", tags=["escalations"])


@router.get("", response_model=list[EscalationSummary])
def list_escalations(
    vertical: str | None = Query(default=None),
    status: str = Query(default="open"),
):
    """
    Lists escalations, optionally filtered by vertical. Defaults to
    open ones only, since that's what the HITL queue displays by
    default (Section 5: "a single page listing items awaiting human
    approval across all verticals, filterable by vertical").
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            sql = """
                SELECT e.id, e.run_id, r.vertical, e.reason, e.assigned_to,
                       e.status, e.pending_action, e.created_at
                FROM escalations e
                JOIN agent_runs r ON r.id = e.run_id
                WHERE e.status = %s
            """
            params: tuple = (status,)

            if vertical:
                sql += " AND r.vertical = %s"
                params += (vertical,)

            sql += " ORDER BY e.created_at DESC;"

            cur.execute(sql, params)
            rows = cur.fetchall()
            return [EscalationSummary(**row) for row in rows]
    finally:
        conn.close()


@router.post("/{escalation_id}/resolve", response_model=EscalationSummary)
def resolve_escalation(escalation_id: str, body: ResolveEscalationRequest):
    """
    Resolves an escalation. If approve=True, fires the stored
    pending_action via the tool registry — completing the
    human-in-the-loop, not just marking status resolved. If
    approve=False, marks it rejected with no action fired.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.run_id, r.vertical, e.reason, e.assigned_to,
                       e.status, e.pending_action, e.created_at
                FROM escalations e
                JOIN agent_runs r ON r.id = e.run_id
                WHERE e.id = %s;
                """,
                (escalation_id,),
            )
            escalation = cur.fetchone()
            if not escalation:
                raise HTTPException(status_code=404, detail="Escalation not found.")

            if escalation["status"] != "open":
                raise HTTPException(
                    status_code=400,
                    detail=f"Escalation already {escalation['status']}, cannot resolve again.",
                )

            if body.approve:
                pending = escalation["pending_action"]
                if not pending or not pending.get("tool_name"):
                    raise HTTPException(
                        status_code=400,
                        detail="No pending_action was stored for this escalation; cannot approve.",
                    )

                result = execute_tool(
                    escalation["vertical"], pending["tool_name"], pending.get("arguments", {})
                )
                log_decision(
                    escalation["run_id"],
                    "action",
                    {"action_name": pending["tool_name"], "result": result, "approved_by_human": True},
                )
                update_run_status(escalation["run_id"], status="completed")
                new_status = "approved"
                
            else:
                update_run_status(escalation["run_id"], status="rejected")
                new_status = "rejected"

            cur.execute(
                """
                UPDATE escalations
                SET status = %s, assigned_to = COALESCE(%s, assigned_to)
                WHERE id = %s
                RETURNING id, run_id, reason, assigned_to, status, pending_action, created_at;
                """,
                (new_status, body.resolved_by, escalation_id),
            )
            updated = cur.fetchone()
            conn.commit()

            return EscalationSummary(**updated, vertical=escalation["vertical"])
    finally:
        conn.close()