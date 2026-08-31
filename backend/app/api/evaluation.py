"""
Evaluation API (Category A) — powers the Evaluation dashboard shared
UI screen (Section 5), and implements the metrics from Section 11.1:
resolution rate, escalation rate, confidence distribution, retrieval
retry frequency, tool-call frequency — aggregated per vertical.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from app.core.db import get_connection

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


class VerticalMetrics(BaseModel):
    vertical: str
    total_runs: int
    completed_count: int
    escalated_count: int
    resolution_rate: float          # completed / total_runs
    escalation_rate: float          # escalated / total_runs
    avg_confidence: float | None
    min_confidence: float | None
    max_confidence: float | None
    retrieval_retry_rate: float     # retried retrievals / total retrievals
    tool_call_count: int
    tool_calls_per_run: float


@router.get("/metrics", response_model=list[VerticalMetrics])
def get_evaluation_metrics():
    """
    Aggregated metrics per vertical, shown side by side
    (Section 11.1, 11.2's comparative research narrative).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Run-level stats: totals, status breakdown, confidence distribution
            cur.execute(
                """
                SELECT
                    vertical,
                    COUNT(*) AS total_runs,
                    COUNT(*) FILTER (WHERE status = 'completed') AS completed_count,
                    COUNT(*) FILTER (WHERE status = 'escalated') AS escalated_count,
                    AVG(confidence) AS avg_confidence,
                    MIN(confidence) AS min_confidence,
                    MAX(confidence) AS max_confidence
                FROM agent_runs
                GROUP BY vertical;
                """
            )
            run_stats = {row["vertical"]: row for row in cur.fetchall()}

            # Retrieval retry rate: how often a 'retrieval' decision was retried
            cur.execute(
                """
                SELECT
                    r.vertical,
                    COUNT(*) AS total_retrievals,
                    COUNT(*) FILTER (WHERE (d.detail->>'retried')::boolean = TRUE) AS retried_retrievals
                FROM agent_decisions d
                JOIN agent_runs r ON r.id = d.run_id
                WHERE d.step_type = 'retrieval'
                GROUP BY r.vertical;
                """
            )
            retry_stats = {row["vertical"]: row for row in cur.fetchall()}

            # Tool-call frequency
            cur.execute(
                """
                SELECT
                    r.vertical,
                    COUNT(*) AS tool_call_count
                FROM agent_decisions d
                JOIN agent_runs r ON r.id = d.run_id
                WHERE d.step_type = 'tool_call'
                GROUP BY r.vertical;
                """
            )
            tool_stats = {row["vertical"]: row["tool_call_count"] for row in cur.fetchall()}

            results = []
            for vertical, stats in run_stats.items():
                total = stats["total_runs"]
                retry = retry_stats.get(vertical)
                retry_rate = (
                    retry["retried_retrievals"] / retry["total_retrievals"]
                    if retry and retry["total_retrievals"] > 0
                    else 0.0
                )
                tool_calls = tool_stats.get(vertical, 0)

                results.append(
                    VerticalMetrics(
                        vertical=vertical,
                        total_runs=total,
                        completed_count=stats["completed_count"],
                        escalated_count=stats["escalated_count"],
                        resolution_rate=stats["completed_count"] / total if total else 0.0,
                        escalation_rate=stats["escalated_count"] / total if total else 0.0,
                        avg_confidence=float(stats["avg_confidence"]) if stats["avg_confidence"] is not None else None,
                        min_confidence=float(stats["min_confidence"]) if stats["min_confidence"] is not None else None,
                        max_confidence=float(stats["max_confidence"]) if stats["max_confidence"] is not None else None,
                        retrieval_retry_rate=retry_rate,
                        tool_call_count=tool_calls,
                        tool_calls_per_run=tool_calls / total if total else 0.0,
                    )
                )

            return results
    finally:
        conn.close()