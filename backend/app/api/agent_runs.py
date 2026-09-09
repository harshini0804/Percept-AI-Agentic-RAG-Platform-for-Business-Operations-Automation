"""
Agent Runs API (Category A) — powers the Dashboard and the
Report/Result viewer shared UI screens (Section 5).
"""

from fastapi import APIRouter, HTTPException, Query
from app.core.db import get_connection
from app.schemas.api_models import AgentRunSummary, AgentRunDetail, AgentDecisionDetail, AgentRunStats

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])


@router.get("", response_model=list[AgentRunSummary])
def list_agent_runs(vertical: str | None = Query(default=None), limit: int = 50):
    """Recent runs, optionally filtered by vertical. Powers the Dashboard."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if vertical:
                cur.execute(
                    """
                    SELECT id, vertical, trigger_type, status, confidence, created_at
                    FROM agent_runs
                    WHERE vertical = %s
                    ORDER BY created_at DESC
                    LIMIT %s;
                    """,
                    (vertical, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT id, vertical, trigger_type, status, confidence, created_at
                    FROM agent_runs
                    ORDER BY created_at DESC
                    LIMIT %s;
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
            return [AgentRunSummary(**row) for row in rows]
    finally:
        conn.close()


@router.get("/summary", response_model=AgentRunStats)
def get_agent_run_stats(vertical: str | None = Query(default=None)):
    """
    Real aggregate counts across ALL runs, via SQL GROUP BY (not
    computed client-side from a paginated page). MUST be registered
    before /{run_id} below — otherwise FastAPI would match a request
    to /agent-runs/summary against {run_id} first (treating the
    literal string "summary" as a run_id), which would fail trying
    to cast it to a UUID.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if vertical:
                cur.execute(
                    "SELECT status, COUNT(*) AS count FROM agent_runs WHERE vertical = %s GROUP BY status;",
                    (vertical,),
                )
            else:
                cur.execute("SELECT status, COUNT(*) AS count FROM agent_runs GROUP BY status;")
            rows = cur.fetchall()
    finally:
        conn.close()

    counts = {row["status"]: row["count"] for row in rows}
    return AgentRunStats(
        total=sum(counts.values()),
        completed=counts.get("completed", 0),
        escalated=counts.get("escalated", 0),
        running=counts.get("running", 0),
        rejected=counts.get("rejected", 0),
    )


@router.get("/{run_id}", response_model=AgentRunDetail)
def get_agent_run(run_id: str):
    """One run's full detail, joined with its decision audit trail. Powers the Report viewer."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, vertical, trigger_type, status, confidence, created_at
                FROM agent_runs WHERE id = %s;
                """,
                (run_id,),
            )
            run = cur.fetchone()
            if not run:
                raise HTTPException(status_code=404, detail="Agent run not found.")

            cur.execute(
                """
                SELECT id, step_type, detail, created_at
                FROM agent_decisions WHERE run_id = %s
                ORDER BY created_at ASC;
                """,
                (run_id,),
            )
            decisions = cur.fetchall()

            return AgentRunDetail(
                **run,
                decisions=[AgentDecisionDetail(**d) for d in decisions],
            )
    finally:
        conn.close()