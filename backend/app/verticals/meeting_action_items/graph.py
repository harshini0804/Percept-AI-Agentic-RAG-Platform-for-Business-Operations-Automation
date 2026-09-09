"""
Meeting Action-Item Enforcement (Vertical 4, Section 8.4) — Trigger 1
(creation): a meeting transcript is uploaded or pasted, candidate
action items are extracted, checked for recurrence against open items
for the same owner, and persisted.

This does NOT follow the standard embed -> retrieve -> reason -> gate
shape the shared orchestration nodes implement — Section 8.4 itself
calls this vertical "the most structurally distinct." One run
produces MULTIPLE items, each with its own recurrence check, and
nothing here gates or escalates (per Section 8.4's own workflow:
"Run ends here — no notification yet"). Confidence-gated action
firing is entirely Trigger 2's job (followup.py, PR 3).

AgentRunOutput here: confidence=1.0 and escalated=False always —
there's no single measured confidence for "did extraction succeed,"
only a record of what was extracted. actions_taken has one entry per
action item created (not per LLM tool call, since this vertical's
tools — send_nudge, escalate_to_manager — are never invoked from
this trigger at all).
"""

import json
from datetime import date

from app.core.db import get_connection
from app.core.orchestration import start_run
from app.core.logging_service import log_decision, complete_agent_run
from app.core.llm_gateway import call_llm
from app.core.retrieval import search_with_retry
from app.core.embeddings import upsert_embedding
from app.core.documents import resolve_document_text
from app.core.vertical_registry import register_vertical
from app.schemas.agent_contract import AgentRunInput, AgentRunOutput, ActionTaken
from app.verticals.meeting_action_items.prompts import build_extraction_prompt

# Section 8.4 doesn't specify an exact number ("a strong match") —
# starting default per Section 12.2, tunable per-vertical without
# touching shared code. No retry-on-weak-match for this search,
# unlike V1/V2 — Section 8.4's Retrieval Logic paragraph doesn't call
# for one here.
RECURRENCE_MATCH_THRESHOLD = 0.75


def _create_meeting(doc_id: str | None) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meetings (doc_id, meeting_date) VALUES (%s, now()) RETURNING id;",
                (doc_id,),
            )
            meeting_id = cur.fetchone()["id"]
        conn.commit()
        return str(meeting_id)
    finally:
        conn.close()


def _validate_deadline(raw_deadline) -> str | None:
    """
    Validates a deadline value returned by the LLM. Returns a real
    ISO date string, or None if the value is missing, not a string,
    or not a genuinely parseable date — this is what prevents an
    invented or malformed deadline from ever reaching the database,
    now that the extraction prompt asks the LLM to attempt real date
    resolution rather than always leaving it null.
    """
    if not raw_deadline or not isinstance(raw_deadline, str):
        return None
    try:
        date.fromisoformat(raw_deadline)
        return raw_deadline
    except ValueError:
        return None


def _extract_candidate_items(transcript_text: str) -> list[dict]:
    """
    Calls the LLM to extract candidate action items. Fails SAFE on
    malformed output (returns an empty list rather than crashing the
    run) — matching this project's established pattern (see the
    dummy vertical's finalize_node) for handling an LLM not returning
    valid JSON. Each item's deadline is separately validated via
    _validate_deadline — a plausible-looking but unparseable date
    string degrades to None rather than being stored as-is.
    """
    response = call_llm(
        messages=[
            {"role": "system", "content": build_extraction_prompt(date.today())},
            {"role": "user", "content": transcript_text},
        ]
    )

    try:
        items = json.loads(response["content"])
        if not isinstance(items, list):
            return []

        valid_items = []
        for item in items:
            if not (isinstance(item, dict) and "description" in item and "owner" in item):
                continue
            item["deadline"] = _validate_deadline(item.get("deadline"))
            valid_items.append(item)
        return valid_items
    except (json.JSONDecodeError, TypeError):
        return []


def _check_recurrence(
    vertical: str, owner: str, description: str
) -> tuple[bool, str | None, float | None]:
    """
    Searches for a strong match among this owner's other open action
    items (Section 8.4's recurrence check). Returns
    (is_recurring, matched_action_item_id_or_None, top_similarity_or_None).
    """
    results, _ = search_with_retry(
        query_text=description,
        vertical=vertical,
        source_type="action_item",
        confidence_threshold=RECURRENCE_MATCH_THRESHOLD,
        reformulate_query_fn=None,  # no retry for this search, per design
        extra_filter_sql=" AND source_id IN (SELECT id FROM action_items WHERE owner = %s AND status = 'open')",
        extra_filter_params=(owner,),
    )

    top_score = results[0]["similarity"] if results else None
    if results and results[0]["similarity"] >= RECURRENCE_MATCH_THRESHOLD:
        return True, str(results[0]["source_id"]), top_score
    return False, None, top_score


def _insert_action_item(
    meeting_id: str, description: str, owner: str, deadline: str | None,
    is_recurring: bool, recurring_from: str | None,
) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO action_items
                    (meeting_id, description, owner, deadline, is_recurring, recurring_from)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (meeting_id, description, owner, deadline, is_recurring, recurring_from),
            )
            action_item_id = cur.fetchone()["id"]
        conn.commit()
        return str(action_item_id)
    finally:
        conn.close()


def run_meeting_action_items(agent_input: AgentRunInput) -> AgentRunOutput:
    """
    Entry point for Trigger 1. Registered as this vertical's run
    function via register_vertical() below.
    """
    if agent_input.input_document_id:
        transcript_text = resolve_document_text(agent_input.input_document_id)
    elif agent_input.input_payload and "text" in agent_input.input_payload:
        transcript_text = agent_input.input_payload["text"]
    else:
        raise ValueError(
            "meeting_action_items requires either input_document_id or "
            "input_payload={'text': ...}."
        )

    run_id = start_run(
        vertical=agent_input.vertical,
        trigger_type=agent_input.trigger_type.value,
        input_document_id=agent_input.input_document_id,
    )
    meeting_id = _create_meeting(agent_input.input_document_id)

    candidate_items = _extract_candidate_items(transcript_text)
    log_decision(
        run_id,
        "llm_reasoning",
        {"extracted_count": len(candidate_items), "items": candidate_items},
    )

    actions_taken: list[ActionTaken] = []

    for item in candidate_items:
        owner = item["owner"].strip().lower()
        description = item["description"]
        deadline = item.get("deadline") or None

        is_recurring, recurring_from, top_score = _check_recurrence(
            agent_input.vertical, owner, description
        )
        log_decision(
            run_id,
            "retrieval",
            {
                "owner": owner,
                "is_recurring": is_recurring,
                "recurring_from": recurring_from,
                "top_score": top_score,
                "num_results": 1 if top_score is not None else 0,
            },
        )

        action_item_id = _insert_action_item(
            meeting_id, description, owner, deadline, is_recurring, recurring_from
        )

        # Every extracted item is embedded regardless of match outcome
        # (Section 8.4 Notes) — the KB must accumulate over time.
        upsert_embedding(
            vertical=agent_input.vertical,
            source_type="action_item",
            chunk_text=description,
            source_id=action_item_id,
        )
        log_decision(
            run_id,
            "action",
            {
                "action_name": "create_action_item",
                "action_item_id": action_item_id,
                "owner": owner,
                "description": description,
                "is_recurring": is_recurring,
            },
        )

        actions_taken.append(
            ActionTaken(
                action_name="create_action_item",
                target_id=action_item_id,
                detail={"owner": owner, "description": description, "is_recurring": is_recurring},
            )
        )

    complete_agent_run(run_id, status="completed", confidence=1.0)

    return AgentRunOutput(
        run_id=run_id,
        status="completed",
        confidence=1.0,
        actions_taken=actions_taken,
        escalated=False,
        escalation_reason=None,
    )


register_vertical("meeting_action_items", run_meeting_action_items)