"""
Meeting Action-Item Enforcement (Vertical 4, Section 8.4) — Trigger 2
(follow-up): a scheduled job re-checks every open, overdue action
item against evidence of the owner's activity, and applies a
deterministic state machine (verdict + nudge_count + escalated) to
decide whether to nudge, escalate, mark resolved, or do nothing.

Per Section 8.4 Notes: "Two separate agent_runs are created per
item's lifecycle (one at creation, one per follow-up check), linked
via the item's own ID rather than sharing a run" — unlike Trigger 1
(many items from one meeting share one run), EACH item checked here
gets its own agent_run, since an item's follow-up history lives on
the item's own row (nudge_count, escalated), not on a shared run.

This vertical's action gate is NOT a numeric confidence threshold
like the shared action_gate_node — Section 8.4 itself calls this
"the most structurally distinct" gate: a discrete state machine over
verdict + nudge_count + escalated. It's implemented directly here
rather than reusing action_gate_node, which doesn't fit this shape.
"""

import json

from app.core.db import get_connection
from app.core.orchestration import start_run
from app.core.logging_service import log_decision, complete_agent_run
from app.core.llm_gateway import call_llm
from app.core.retrieval import search_embeddings
from app.core.tool_registry import execute_tool
from app.schemas.agent_contract import TriggerType, build_agent_run_output
from app.verticals.meeting_action_items.prompts import FOLLOWUP_VERDICT_PROMPT


def _fetch_overdue_open_items() -> list[dict]:
    """
    Section 8.4: "Daily job queries action_items WHERE status='open'
    AND deadline <= today." Items with a NULL deadline never match
    this filter — per spec, only items with an actual due date are
    followed up on; this is expected, not a gap.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM action_items WHERE status = 'open' AND deadline <= CURRENT_DATE;"
            )
            return cur.fetchall()
    finally:
        conn.close()


def _search_owner_activity(owner: str, description: str, since: str) -> list[dict]:
    """
    Section 8.4: search embeddings (source_type='owner_activity',
    same owner, date > item.created_at) using the item description
    as the query. No retry-on-weak-match here, matching Trigger 1's
    recurrence check — Section 8.4 doesn't call for one for this
    search either.
    """
    return search_embeddings(
        query_text=description,
        vertical="meeting_action_items",
        source_type="owner_activity",
        top_k=5,
        extra_filter_sql=" AND metadata->>'owner' = %s AND (metadata->>'date')::date > %s",
        extra_filter_params=(owner, since),
    )


def _judge_verdict(description: str, evidence: list[dict]) -> dict:
    """
    Calls the LLM to judge completion status. Fails SAFE to
    'no_evidence' on malformed output — same pattern as Trigger 1's
    extraction step (see graph.py's _extract_candidate_items).
    """
    context_text = (
        "\n\n".join(e["chunk_text"] for e in evidence) if evidence else "No related activity found."
    )
    response = call_llm(
        messages=[
            {"role": "system", "content": FOLLOWUP_VERDICT_PROMPT},
            {
                "role": "user",
                "content": f"Action item: {description}\n\nEvidence:\n{context_text}",
            },
        ]
    )

    try:
        parsed = json.loads(response["content"])
        verdict = parsed.get("verdict")
        confidence = float(parsed.get("confidence", 0.0))
        if verdict not in ("done", "in_progress", "no_evidence"):
            return {"verdict": "no_evidence", "confidence": 0.0}
        return {"verdict": verdict, "confidence": confidence}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"verdict": "no_evidence", "confidence": 0.0}


def _mark_resolved(action_item_id: str) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE action_items SET status = 'resolved' WHERE id = %s;", (action_item_id,)
            )
        conn.commit()
    finally:
        conn.close()


def check_and_act_on_item(item: dict) -> dict:
    """
    Runs the full Trigger 2 check for ONE action item: its own
    agent_run, retrieval, LLM verdict, and the deterministic
    resolve/nudge/escalate/no-op state machine (Section 8.4):

        verdict == 'done'                       -> mark_resolved
        verdict in (in_progress, no_evidence)
            AND nudge_count == 0                -> send_nudge
            AND nudge_count >= 1 AND not escalated -> escalate_to_manager
            AND already escalated                -> no further action
    """
    action_item_id = str(item["id"])
    owner = item["owner"]
    description = item["description"]

    run_id = start_run(
        vertical="meeting_action_items",
        trigger_type=TriggerType.SCHEDULED_FOLLOWUP.value,
    )

    evidence = _search_owner_activity(owner, description, str(item["created_at"].date()))
    log_decision(run_id, "retrieval", {"owner": owner, "num_results": len(evidence)})

    verdict_result = _judge_verdict(description, evidence)
    log_decision(run_id, "llm_reasoning", verdict_result)

    action_taken = None
    escalated = False
    escalation_reason = None

    if verdict_result["verdict"] == "done":
        _mark_resolved(action_item_id)
        action_taken = {
            "action_name": "mark_resolved",
            "result": {"action_item_id": action_item_id},
        }
    elif item["nudge_count"] == 0:
        result = execute_tool(
            "meeting_action_items",
            "send_nudge",
            {"run_id": run_id, "action_item_id": action_item_id},
        )
        action_taken = {"action_name": "send_nudge", "result": result}
    elif not item["escalated"]:
        result = execute_tool(
            "meeting_action_items",
            "escalate_to_manager",
            {"run_id": run_id, "action_item_id": action_item_id},
        )
        action_taken = {"action_name": "escalate_to_manager", "result": result}
        escalated = True
        escalation_reason = (
            f"Action item unresolved after {item['nudge_count']} nudge(s): {description}"
        )
    # else: already escalated, nudge_count >= 1 -> no further action.
    # No synthetic decision is logged for this branch — the item's
    # own row (escalated=True) already tells the full story.

    if action_taken:
        log_decision(run_id, "action", action_taken)

    confidence = verdict_result["confidence"]
    complete_agent_run(
        run_id, status="escalated" if escalated else "completed", confidence=confidence
    )

    state = {
        "run_id": run_id,
        "confidence": confidence,
        "action_taken": action_taken,
        "escalated": escalated,
        "escalation_reason": escalation_reason,
    }
    return build_agent_run_output(state).model_dump()


def run_followup_check() -> dict:
    """
    Entry point for the scheduled job (registered in main.py). Checks
    every open, overdue action item independently. Not registered
    via vertical_registry/AgentRunInput — nothing submits this
    through the API, it's purely scheduler-driven.
    """
    items = _fetch_overdue_open_items()
    results = [check_and_act_on_item(item) for item in items]
    return {"checked": len(items), "results": results}