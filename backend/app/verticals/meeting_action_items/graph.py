"""
Meeting Action-Item Enforcement (Vertical 4, Section 8.4) — Trigger 1
(creation): a meeting transcript is uploaded or pasted, candidate
action items are extracted, checked for recurrence against open items
for the same owner, and persisted.

This does NOT follow the standard embed -> retrieve -> reason -> gate
shape the shared orchestration nodes implement — Section 8.4 itself
calls this vertical "the most structurally distinct." One run
produces MULTIPLE items, each with its own recurrence check. Per
Section 8.4's own workflow ("Run ends here — no notification yet"),
confidence-gated action firing is Trigger 2's job (followup.py).

Run outcomes:
- Items extracted and stored -> status "completed", confidence None.
  There is no measured probability that "extraction succeeded", only
  a record of what was extracted, so no confidence is reported
  (previously a fixed 1.0, which read as a certainty it never had).
- Nothing extracted (e.g. a contract uploaded by mistake) or the
  model's output could not be read -> escalated to a human with a
  specific reason, rather than reported as a successful run.
- Any exception while processing -> everything this run created is
  removed, the run is escalated, and it never stays "running". Removing
  the partial work lets the same transcript be resubmitted cleanly.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date

from app.core.db import get_connection
from app.core.orchestration import start_run
from app.core.logging_service import (
    log_decision,
    complete_agent_run,
    create_escalation,
)
from app.core.llm_gateway import call_llm
from app.core.retrieval import search_with_retry
from app.core.embeddings import upsert_embedding
from app.core.documents import resolve_document_text
from app.core.vertical_registry import register_vertical
from app.schemas.agent_contract import AgentRunInput, AgentRunOutput, ActionTaken
from app.verticals.meeting_action_items.prompts import build_extraction_prompt

logger = logging.getLogger(__name__)

# Section 8.4 doesn't specify an exact number ("a strong match") —
# starting default per Section 12.2, tunable per-vertical without
# touching shared code. No retry-on-weak-match for this search,
# unlike V1/V2 — Section 8.4's Retrieval Logic paragraph doesn't call
# for one here.
RECURRENCE_MATCH_THRESHOLD = 0.75

NO_ITEMS_REASON = (
    "No action items were found in this document. It may not be a meeting "
    "transcript, or the meeting may have had no assigned tasks."
)
UNREADABLE_OUTPUT_REASON = (
    "The model's extraction output could not be read, so no action items "
    "were created. Please review the document and resubmit."
)


class ExtractionOutputError(Exception):
    """The model's reply could not be read as a list of action items.

    Distinct from a reply that was read successfully and was simply
    empty — the first is a failure, the second is a legitimate result
    (e.g. a document that contains no action items at all)."""


@dataclass
class _CreatedRecords:
    """Everything a run has written so far, so a failure can undo it."""
    meeting_id: str | None = None
    action_item_ids: list[str] = field(default_factory=list)


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


def _strip_code_fences(text: str) -> str:
    """Models sometimes wrap JSON in ```json fences even when told not
    to. Without this, such a reply would be treated as unreadable and
    escalated even though its content is perfectly valid."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _is_valid_candidate(item) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("description"), str)
        and item["description"].strip() != ""
        and isinstance(item.get("owner"), str)
        and item["owner"].strip() != ""
    )


def _extract_candidate_items(transcript_text: str) -> list[dict]:
    """
    Calls the LLM to extract candidate action items.

    Returns a list (possibly empty) when the reply was read
    successfully — an empty list is a legitimate "nothing found".
    Raises ExtractionOutputError when the reply could not be read at
    all (empty, not JSON, not a list, or a non-empty list in which no
    entry is usable) — callers must treat that as a failure, not as
    "nothing found". Each item's deadline is separately validated via
    _validate_deadline — a plausible-looking but unparseable date
    string degrades to None rather than being stored as-is.
    """
    response = call_llm(
        messages=[
            {"role": "system", "content": build_extraction_prompt(date.today())},
            {"role": "user", "content": transcript_text},
        ]
    )

    raw = response.get("content")
    if not isinstance(raw, str) or not raw.strip():
        raise ExtractionOutputError("The model returned no content.")

    try:
        items = json.loads(_strip_code_fences(raw))
    except json.JSONDecodeError as exc:
        raise ExtractionOutputError("The model's reply was not valid JSON.") from exc

    if not isinstance(items, list):
        raise ExtractionOutputError("The model's reply was not a JSON list.")

    valid_items = []
    for item in items:
        if not _is_valid_candidate(item):
            continue
        item["deadline"] = _validate_deadline(item.get("deadline"))
        valid_items.append(item)

    if items and not valid_items:
        raise ExtractionOutputError("None of the returned entries were usable action items.")
    return valid_items


def _check_recurrence(
    vertical: str, owner: str, description: str
) -> tuple[bool, str | None, float | None, list[dict]]:
    """
    Searches for a strong match among this owner's other open action
    items (Section 8.4's recurrence check). Returns
    (is_recurring, matched_action_item_id_or_None, top_similarity_or_None,
    raw_results) — raw_results is returned too (not just the reduced
    top_score) so the caller's retrieval decision can log full
    per-chunk content for the Report viewer's clickable document list
    (UI feature 3), not just the summary score.
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
        return True, str(results[0]["source_id"]), top_score, results
    return False, None, top_score, results


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


def _rollback_created_records(created: _CreatedRecords) -> None:
    """Removes the meeting, action items and embeddings a failed run
    created, in one transaction. agent_decisions rows are kept — they
    are the audit trail of what the run attempted."""
    if not created.meeting_id and not created.action_item_ids:
        return

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if created.action_item_ids:
                cur.execute(
                    "DELETE FROM embeddings WHERE source_type = 'action_item' "
                    "AND source_id = ANY(%s::uuid[]);",
                    (created.action_item_ids,),
                )
                cur.execute(
                    "DELETE FROM action_items WHERE id = ANY(%s::uuid[]);",
                    (created.action_item_ids,),
                )
            if created.meeting_id:
                cur.execute("DELETE FROM meetings WHERE id = %s;", (created.meeting_id,))
        conn.commit()
    finally:
        conn.close()


def _escalate_run(run_id: str, reason: str) -> AgentRunOutput:
    """Routes the run to the human review queue — same three steps the
    shared action_gate_node performs when it escalates (escalation row,
    an 'escalation' decision for the Report viewer, run closed as
    'escalated'). There is no pending action to approve, so a reviewer
    can only reject (dismiss) these."""
    create_escalation(run_id, reason=reason)
    log_decision(run_id, "escalation", {"reason": reason, "pending_action": None})
    complete_agent_run(run_id, status="escalated", confidence=None)
    return AgentRunOutput(
        run_id=run_id,
        status="escalated",
        confidence=None,
        actions_taken=[],
        escalated=True,
        escalation_reason=reason,
    )


def _fail_run(run_id: str, created: _CreatedRecords, exc: Exception) -> AgentRunOutput:
    """Handles an unexpected exception: undo partial work, then escalate
    so the run is closed instead of left 'running' forever."""
    logger.exception("meeting_action_items Trigger 1 failed for run %s", run_id, exc_info=exc)

    rolled_back = True
    try:
        _rollback_created_records(created)
    except Exception:
        rolled_back = False
        logger.exception("Rollback failed for run %s", run_id)

    if rolled_back:
        outcome = "Any action items created during this run were removed. Please resubmit."
    else:
        outcome = "Some items created during this run may remain and need manual cleanup."
    reason = f"Processing failed ({type(exc).__name__}). {outcome}"
    return _escalate_run(run_id, reason)


def _persist_items(
    run_id: str, vertical: str, doc_id: str | None,
    candidate_items: list[dict], created: _CreatedRecords,
) -> list[ActionTaken]:
    """Creates the meeting (only now that there is at least one item,
    so a wrong document leaves no orphan meeting), then one action
    item + embedding per candidate. Everything written is recorded in
    `created` immediately so a mid-way failure can be undone."""
    created.meeting_id = _create_meeting(doc_id)
    actions_taken: list[ActionTaken] = []

    for item in candidate_items:
        owner = item["owner"].strip().lower()
        description = item["description"]
        deadline = item.get("deadline") or None

        is_recurring, recurring_from, top_score, recurrence_results = _check_recurrence(
            vertical, owner, description
        )
        log_decision(
            run_id,
            "retrieval",
            {
                "owner": owner,
                "is_recurring": is_recurring,
                "recurring_from": recurring_from,
                "top_score": top_score,
                "num_results": len(recurrence_results),
                "results": [
                    {"id": str(r["id"]), "chunk_text": r["chunk_text"], "similarity": r["similarity"]}
                    for r in recurrence_results
                ],
            },
        )

        action_item_id = _insert_action_item(
            created.meeting_id, description, owner, deadline, is_recurring, recurring_from
        )
        created.action_item_ids.append(action_item_id)

        # Every extracted item is embedded regardless of match outcome
        # (Section 8.4 Notes) — the KB must accumulate over time.
        upsert_embedding(
            vertical=vertical,
            source_type="action_item",
            chunk_text=description,
            source_id=action_item_id,
        )

        actions_taken.append(
            ActionTaken(
                action_name="create_action_item",
                target_id=action_item_id,
                detail={"owner": owner, "description": description, "is_recurring": is_recurring},
            )
        )

    return actions_taken


def run_meeting_action_items(agent_input: AgentRunInput) -> AgentRunOutput:
    """
    Entry point for Trigger 1. Registered as this vertical's run
    function via register_vertical() below.
    """
    if agent_input.input_document_id:
        transcript_text = resolve_document_text(agent_input.input_document_id)
        doc_id = agent_input.input_document_id
    elif agent_input.input_payload and "text" in agent_input.input_payload:
        transcript_text = agent_input.input_payload["text"]
        doc_id = None
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
    created = _CreatedRecords()

    try:
        candidate_items = _extract_candidate_items(transcript_text)
        log_decision(
            run_id,
            "llm_reasoning",
            {"extracted_count": len(candidate_items), "items": candidate_items},
        )
    except ExtractionOutputError:
        try:
            log_decision(
                run_id,
                "llm_reasoning",
                {"error": "The model's output could not be read as a list of action items."},
            )
        except Exception:
            logger.exception("Could not log the unreadable-output decision for run %s", run_id)
        return _escalate_run(run_id, UNREADABLE_OUTPUT_REASON)
    except Exception as exc:
        return _fail_run(run_id, created, exc)

    if not candidate_items:
        return _escalate_run(run_id, NO_ITEMS_REASON)

    try:
        actions_taken = _persist_items(
            run_id, agent_input.vertical, doc_id, candidate_items, created
        )
        # Action decisions are logged only once every item has been
        # stored: if anything fails above, the items are rolled back,
        # and the Report viewer must not show "actions taken" for
        # things that no longer exist.
        for action in actions_taken:
            log_decision(
                run_id,
                "action",
                {"action_name": action.action_name, "action_item_id": action.target_id, **action.detail},
            )
        complete_agent_run(run_id, status="completed", confidence=None)
    except Exception as exc:
        return _fail_run(run_id, created, exc)

    return AgentRunOutput(
        run_id=run_id,
        status="completed",
        confidence=None,
        actions_taken=actions_taken,
        escalated=False,
        escalation_reason=None,
    )


register_vertical("meeting_action_items", run_meeting_action_items)