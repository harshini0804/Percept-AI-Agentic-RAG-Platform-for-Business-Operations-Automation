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
import logging
from datetime import datetime

from app.core.db import get_connection
from app.core.orchestration import start_run
from app.core.logging_service import log_decision, complete_agent_run, create_escalation
from app.core.llm_gateway import call_llm
from app.core.retrieval import search_embeddings
from app.core.tool_registry import execute_tool
from app.schemas.agent_contract import TriggerType, build_agent_run_output
from app.verticals.meeting_action_items.prompts import FOLLOWUP_VERDICT_PROMPT


logger = logging.getLogger(__name__)


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


def _search_owner_activity(owner: str, description: str, since: "str | datetime") -> list[dict]:
    """
    Section 8.4: search embeddings (source_type='owner_activity',
    same owner, happened after item.created_at) using the item
    description as the query. No retry-on-weak-match here, matching
    Trigger 1's recurrence check — Section 8.4 doesn't call for one
    for this search either.

    "Happened after" is compared as an exact moment, not a calendar
    date, so evidence that arrives minutes after the meeting (e.g. a
    GitHub merge delivered by webhook the same day) counts. Evidence
    carries `occurred_at` (a full timestamp) when it came from an
    integration; older/seeded evidence only has a `date`, which is
    treated as midnight at the start of that day — exactly equivalent
    to the previous date-only rule for that data.
    """
    return search_embeddings(
        query_text=description,
        vertical="meeting_action_items",
        source_type="owner_activity",
        top_k=5,
        extra_filter_sql=(
            " AND metadata->>'owner' = %s"
            " AND COALESCE((metadata->>'occurred_at')::timestamptz,"
            " (metadata->>'date')::date::timestamptz) > %s"
        ),
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


def _mark_run_failed(run_id: str, exc: Exception) -> None:
    """Closes a started run that hit an unexpected error, so it is never
    left 'running' forever. Best effort — if recording the failure
    itself fails, that is logged and the original error still propagates."""
    reason = f"Follow-up check failed ({type(exc).__name__}). Please review this action item."
    try:
        create_escalation(run_id, reason=reason)
        log_decision(run_id, "escalation", {"reason": reason, "pending_action": None})
        complete_agent_run(run_id, status="escalated", confidence=None)
    except Exception:
        logger.exception("Could not record the failure of follow-up run %s", run_id)


def _record_and_act(run_id: str, item: dict, evidence: list[dict], verdict_result: dict) -> dict:
    """Everything that happens once a run exists: log the retrieval and
    verdict, apply the resolve/nudge/escalate state machine, close the run."""
    action_item_id = str(item["id"])
    owner = item["owner"]
    description = item["description"]

    top_score = evidence[0]["similarity"] if evidence else None
    log_decision(
        run_id,
        "retrieval",
        {
            "owner": owner,
            "num_results": len(evidence),
            "top_score": top_score,
            "results": [
                {"id": str(e["id"]), "chunk_text": e["chunk_text"], "similarity": e["similarity"]}
                for e in evidence
            ],
        },
    )
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
    else:
        # not item["escalated"] — the only remaining case, since
        # the caller already filtered out "already escalated".
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

    log_decision(run_id, "action", action_taken)

    print(
        f"[scheduled followup] item {action_item_id} (owner={owner}): "
        f"verdict={verdict_result['verdict']}, action={action_taken}"
    )

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


def check_and_act_on_item(
    item: dict,
    resolve_only: bool = False,
    trigger_type: TriggerType = TriggerType.SCHEDULED_FOLLOWUP,
) -> dict | None:
    """
    Runs the full Trigger 2 check for ONE action item: retrieval,
    LLM verdict, and the deterministic resolve/nudge/escalate/no-op
    state machine (Section 8.4):

        verdict == 'done'                       -> mark_resolved
        verdict in (in_progress, no_evidence)
            AND nudge_count == 0                -> send_nudge
            AND nudge_count >= 1 AND not escalated -> escalate_to_manager
            AND already escalated                -> no further action

    Returns None for the "already escalated, still not resolved"
    no-op case — otherwise this item would spawn a fresh, identical
    no-op agent_run every single scheduled cycle forever, flooding
    the Dashboard/Evaluation page with duplicate 'completed' runs
    that represent nothing actually happening.

    The evidence search + LLM verdict still run EVERY cycle
    regardless of this — so an already-escalated item can still
    auto-resolve later if real evidence eventually appears. Only the
    run/decision LOGGING is skipped when there's genuinely nothing
    new to record.

    Error handling: a failure BEFORE a run exists (search or LLM call,
    e.g. an outage) creates no records and simply propagates — the item
    is retried next cycle, and an outage cannot flood the escalation
    queue. A failure AFTER the run starts closes that run as
    'escalated' (never left 'running') and then propagates.

    resolve_only=True is used when new evidence has just arrived
    (see check_owner_for_new_evidence): the only outcome acted on is
    "done" -> mark_resolved. Any other verdict returns None without
    logging a run, so a burst of unrelated evidence cannot trigger
    nudges or escalations ahead of the normal schedule.
    """
    action_item_id = str(item["id"])
    owner = item["owner"]
    description = item["description"]

    evidence = _search_owner_activity(owner, description, item["created_at"])
    verdict_result = _judge_verdict(description, evidence)

    if resolve_only and verdict_result["verdict"] != "done":
        return None

    is_noop = (
        verdict_result["verdict"] != "done"
        and item["nudge_count"] >= 1
        and item["escalated"]
    )
    if is_noop:
        print(
            f"[scheduled followup] item {action_item_id} (owner={owner}): "
            f"still not done, already escalated — skipping (no run logged)"
        )
        return None

    run_id = start_run(
        vertical="meeting_action_items",
        trigger_type=trigger_type.value,
    )
    try:
        return _record_and_act(run_id, item, evidence, verdict_result)
    except Exception as exc:
        _mark_run_failed(run_id, exc)
        raise


def _fetch_open_items_for_owner(owner: str) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM action_items WHERE status = 'open' AND owner = %s "
                "ORDER BY created_at;",
                (owner,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def check_owner_for_new_evidence(owner: str) -> list[dict]:
    """
    Event-driven counterpart to the scheduled check: called right
    after a new piece of owner_activity evidence is stored (e.g. from
    a GitHub webhook). Re-checks that owner's open items — due or not
    yet due, since finishing early is still finishing — but ONLY to
    see whether any is now done (resolve_only). Nudging and escalation
    remain the scheduled job's business.

    Returns one entry per item resolved: {action_item_id,
    description, run_id}. One item failing never stops the others.
    """
    resolved = []
    for item in _fetch_open_items_for_owner(owner):
        try:
            output = check_and_act_on_item(
                item, resolve_only=True, trigger_type=TriggerType.INTEGRATION_EVENT
            )
        except Exception:
            logger.exception("Event-driven check failed for action item %s", item.get("id"))
            continue
        if output is not None:
            resolved.append(
                {
                    "action_item_id": str(item["id"]),
                    "description": item["description"],
                    "run_id": output["run_id"],
                }
            )
    return resolved


def run_followup_check() -> dict:
    """
    Entry point for the scheduled job (registered in main.py). Checks
    every open, overdue action item independently — one item's failure
    is logged and counted but never stops the remaining items from being
    checked. Not registered via vertical_registry/AgentRunInput —
    nothing submits this through the API, it's purely scheduler-driven.
    """
    items = _fetch_overdue_open_items()
    results = []
    skipped_noop_count = 0
    failed_count = 0
    for item in items:
        try:
            result = check_and_act_on_item(item)
        except Exception:
            failed_count += 1
            logger.exception("Follow-up check failed for action item %s", item.get("id"))
            continue
        if result is None:
            skipped_noop_count += 1
        else:
            results.append(result)
    return {
        "checked": len(items),
        "processed": len(results),
        "skipped_noop": skipped_noop_count,
        "failed": failed_count,
        "results": results,
    }