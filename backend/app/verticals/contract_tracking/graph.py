"""
Contract Obligation & Renewal Tracking Agent — Graph (Vertical 3,
Section 8.3).

Does NOT reuse app.core.orchestration's retrieve_node/action_gate_node
as-is. Two structural reasons:

1. Retrieval here is not a single similarity search over retrieved
   context (Section 3.3's generic shape) — it's clause-level
   structured extraction with two distinct, narrower tool-driven
   lookups (get_surrounding_clauses: direct, same-document;
   search_similar_contracts: genuine semantic search, cross-
   document). See tools.py's module docstring for the full
   rationale.

2. action_gate_node gates ONCE per run. Section 8.3 requires gating
   PER OBLIGATION within a single run — one contract can have some
   obligations auto-reminded and others flagged simultaneously. So
   this module calls execute_tool/create_escalation/log_decision
   directly, in a loop, and calls complete_agent_run exactly once
   at the end (Stage 5 for the whole run, per Section 3.3), after
   every obligation has been individually gated.

Confidence thresholds are environment-configurable (Section 12.2:
"exact confidence threshold values... tune during Weeks 8-10"), not
hardcoded, so they can be adjusted without a code change/redeploy.
"""

import json
import os
from datetime import date

from app.core.llm_gateway import call_llm
from app.core.logging_service import log_decision, complete_agent_run, create_escalation
from app.core.tool_registry import execute_tool
from app.core.db import get_connection
from app.core.documents import resolve_document_text
from app.core.embeddings import upsert_embedding
from app.schemas.agent_contract import AgentRunInput, AgentRunOutput, build_agent_run_output
from app.core.orchestration import start_run

from app.verticals.contract_tracking.chunking import split_contract_into_clauses

# Ensure this vertical's tools are registered (import triggers the
# @tool decorators) — mirrors dummy/graph.py's own import of
# dummy/tools.py.
import app.verticals.contract_tracking.tools  # noqa: F401
from app.verticals.contract_tracking.tools import get_surrounding_clauses, search_similar_contracts


# Section 12.2: tunable without a code change.
# ACTION_THRESHOLD gates create_calendar_reminder vs flag_for_manual_review
# (Section 8.3, Agentic Decision Points).
# Default raised from 0.75 to 0.85 based on threshold experiment results
# (Section 12.2 tuning): real LLM confidence clusters at 0.85-0.98 for
# well-defined obligations; 0.75 was too permissive, auto-actioning
# ambiguous clauses like "reasonable advance notice" with no concrete date.
ACTION_THRESHOLD = float(os.getenv("CONTRACT_TRACKING_ACTION_THRESHOLD", "0.85"))
# PRECEDENT_CHECK_THRESHOLD is the code-gated floor that triggers
# search_similar_contracts even when the LLM itself didn't flag the
# clause as unusual_wording (see graph.py's module-level design
# discussion: hybrid trigger — LLM judgment first-class, confidence
# floor as a safety net).
PRECEDENT_CHECK_THRESHOLD = float(os.getenv("CONTRACT_TRACKING_PRECEDENT_THRESHOLD", "0.6"))
# Date-clarity confidence penalties (Approach B, Section 12.2 tuning):
# vague/unresolvable timing reduces effective confidence used for the
# action gate, since an obligation with no concrete deadline is harder
# to auto-action. Both are env-configurable for further tuning.
VAGUE_DATE_CONFIDENCE_PENALTY = float(os.getenv("CONTRACT_TRACKING_VAGUE_DATE_PENALTY", "0.12"))
EVENT_TRIGGERED_CONFIDENCE_PENALTY = float(os.getenv("CONTRACT_TRACKING_EVENT_TRIGGERED_PENALTY", "0.05"))


_EXTRACTION_SYSTEM_PROMPT = """You extract date-bound obligations from a single \
contract clause: renewal deadlines, notice periods, penalty triggers, and \
similar time- or condition-bound commitments.

Respond with ONLY strict JSON in this exact shape, no other text:
{
  "has_obligation": <true/false>,
  "description": "<short description of the obligation, or empty string if none>",
  "raw_date_or_condition": "<the date or triggering condition as written, or empty string>",
  "references_other_section": "<clause number referenced, or empty string if none>",
  "unusual_wording": <true/false — true if this clause's phrasing is unusual, \
non-standard, or hard to interpret with confidence>,
  "confidence": <float 0.0-1.0 — your confidence in this extraction>
}

If has_obligation is false, the other fields may be empty/default but must \
still be present. Only set references_other_section if the clause explicitly \
points to another numbered section (e.g. "as defined in Section 4.2")."""


def _classify_error(exc: Exception) -> str:
    """
    Converts a raw exception into a short, human-readable category
    string safe to display in the escalations UI. Raw exception
    messages are never surfaced directly — they can contain sensitive
    API details (org IDs, billing URLs, token counts) that are not
    useful to a human reviewer and should not appear in the UI.
    """
    msg = str(exc).lower()
    if "429" in msg or "rate_limit" in msg or "rate limit" in msg:
        return "LLM service temporarily unavailable (rate limit)"
    if "401" in msg or "403" in msg or "auth" in msg or "api_key" in msg:
        return "LLM service authentication error"
    if "timeout" in msg or "timed out" in msg:
        return "LLM service timed out"
    if "connection" in msg or "network" in msg:
        return "LLM service unreachable"
    return "Unexpected error during clause extraction"


_DATE_RESOLUTION_SYSTEM_PROMPT = """You are a legal date analyst. Given an
obligation's timing description and the contract's Effective Date, determine
whether a concrete calendar deadline can be computed.

You will receive:
- effective_date: the contract's start date in YYYY-MM-DD format (or null)
- raw_date_or_condition: the timing description exactly as written in the contract
- term_months: the contract's duration in months if inferable (or null)

Classify the timing and compute the date if possible.

Respond with ONLY strict JSON, no other text:
{
  "obligation_date": "YYYY-MM-DD or null",
  "date_status": "<one of: computed | vague | event_triggered | no_timing | empty>",
  "reasoning": "<one short sentence explaining your classification>"
}

date_status values:
- computed: you successfully computed a concrete calendar date from the
  effective_date and the timing description
- vague: the timing is genuinely ambiguous ("within a reasonable time",
  "from time to time", "as agreed", "periodically", "in a timely manner",
  "at the appropriate time", "reasonable advance notice")
- event_triggered: the date depends on a future event that has not yet
  occurred ("30 days after invoice", "14 days following written notice of
  breach", "within 48 hours of discovery", "upon termination")
- no_timing: the obligation has no temporal component at all (no date,
  no duration, no condition — just a permanent duty)
- empty: no timing information was provided

Only return computed if you are genuinely confident in the date.
When in doubt between computed and vague/event_triggered, choose the
more conservative status."""


def _resolve_obligation_date(
    raw_date_or_condition: str,
    effective_date: str | None,
    key_index: int = 0,
) -> dict:
    """
    Classifies obligation timing and computes an absolute calendar date
    where possible (Approach 2+3: LLM-based resolution using the
    Effective Date extracted from the contract itself).

    Returns a dict:
      {
        "obligation_date": date | None,
        "date_status": "computed" | "vague" | "event_triggered" |
                       "no_timing" | "empty",
        "reasoning": str,   # one sentence, for escalation reason context
      }

    The five date_status values drive the escalation reason construction
    in _process_clause — each produces a different, contextual message
    rather than a generic "low confidence" fallback.

    Uses the same key_index as the extraction call for this clause,
    since alternating within a single clause provides no benefit
    (calls are sequential, not parallel).
    """
    if not raw_date_or_condition or not raw_date_or_condition.strip():
        return {
            "obligation_date": None,
            "date_status": "empty",
            "reasoning": "No timing information was provided.",
        }

    user_content = json.dumps({
        "effective_date": effective_date,
        "raw_date_or_condition": raw_date_or_condition.strip(),
    })

    try:
        response = call_llm(
            messages=[
                {"role": "system", "content": _DATE_RESOLUTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            key_index=key_index,
        )
        parsed = json.loads((response.get("content") or "").strip())
        obligation_date = None
        raw_od = parsed.get("obligation_date")
        if raw_od:
            try:
                obligation_date = date.fromisoformat(str(raw_od))
            except (ValueError, TypeError):
                obligation_date = None

        return {
            "obligation_date": obligation_date,
            "date_status": parsed.get("date_status", "vague"),
            "reasoning": parsed.get("reasoning", ""),
        }
    except Exception:
        # Date resolution is best-effort — never let it break the
        # main extraction pipeline. Fall back to vague on any error.
        return {
            "obligation_date": None,
            "date_status": "vague",
            "reasoning": "Date resolution could not be completed.",
        }


def _extract_clause(clause_text: str, extra_context: str | None = None, key_index: int = 0) -> dict:
    """One LLM call: extracts the structured obligation shape (Section
    8.3, workflow step 2) from a single clause. If extra_context is
    given (the resolved text of a cross-referenced clause), it's
    appended so the model can finish extraction with that context —
    this is workflow step 3's re-extraction, done as a second full
    call rather than a tool-calling loop, per design discussion."""
    user_content = clause_text
    if extra_context:
        user_content += f"\n\n---\nReferenced section content:\n{extra_context}"

    response = call_llm(
        messages=[
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.0,
        key_index=key_index,
    )

    import json

    try:
        parsed = json.loads((response.get("content") or "").strip())
        return {
            "has_obligation": bool(parsed.get("has_obligation", False)),
            "description": parsed.get("description", ""),
            "raw_date_or_condition": parsed.get("raw_date_or_condition", ""),
            "references_other_section": parsed.get("references_other_section", ""),
            "unusual_wording": bool(parsed.get("unusual_wording", False)),
            "confidence": float(parsed.get("confidence", 0.0)),
        }
    except (ValueError, TypeError, AttributeError, KeyError):
        # Malformed LLM output: fail safe to "no obligation found,
        # zero confidence" rather than crashing the whole contract's
        # run over one bad clause — matches the platform's existing
        # pattern of escalating rather than crashing on parse failure
        # (see dummy/graph.py's finalize_node).
        return {
            "has_obligation": False,
            "description": "",
            "raw_date_or_condition": "",
            "references_other_section": "",
            "unusual_wording": False,
            "confidence": 0.0,
        }


def _insert_obligation(
    contract_id: str,
    extraction: dict,
    obligation_date: date | None = None,
) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO obligations (contract_id, description, obligation_date, type, confidence)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    contract_id,
                    extraction["description"],
                    obligation_date,
                    "obligation",
                    extraction["confidence"],
                ),
            )
            obligation_id = cur.fetchone()["id"]
        conn.commit()
        return str(obligation_id)
    finally:
        conn.close()


def _insert_contract(
    vendor_name: str | None,
    doc_id: str | None,
    run_id: str,
    effective_date: str | None = None,
) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Parse effective_date string to a Python date if provided
            ed = None
            if effective_date:
                try:
                    ed = date.fromisoformat(effective_date)
                except (ValueError, TypeError):
                    ed = None
            cur.execute(
                """INSERT INTO contracts (doc_id, vendor_name, run_id, effective_date)
                   VALUES (%s, %s, %s, %s) RETURNING id;""",
                (doc_id, vendor_name, run_id, ed),
            )
            contract_id = cur.fetchone()["id"]
        conn.commit()
        return str(contract_id)
    finally:
        conn.close()


def _process_clause(run_id: str, contract_id: str, clause: dict, clause_index: int = 0, effective_date: str | None = None) -> dict:
    """
    Handles one clause end to end: extraction, optional cross-
    reference re-extraction, optional precedent check, obligation
    insertion (if any), per-obligation confidence gate, and clause
    persistence into the KB. Returns a dict describing what
    happened, for the caller to fold into the run's final state.
    """
    # Alternate API keys per clause to halve each key's token consumption
    # (Section 12.2 / two-key strategy). Chunking always uses key 0.
    key_index = clause_index % 2
    extraction = _extract_clause(clause["text"], key_index=key_index)
    log_decision(run_id, "llm_reasoning", {
        "clause_number": clause["clause_number"],
        "extraction": extraction,
    })

    if extraction["references_other_section"]:
        surrounding = get_surrounding_clauses(
            contract_id=contract_id,
            clause_number=extraction["references_other_section"],
        )
        log_decision(run_id, "tool_call", {
            "tool": "get_surrounding_clauses",
            "clause_number": clause["clause_number"],
            "referenced_clause": extraction["references_other_section"],
            "found": surrounding["found"],
        })
        if surrounding["found"]:
            extraction = _extract_clause(clause["text"], extra_context=surrounding["text"], key_index=key_index)
            log_decision(run_id, "llm_reasoning", {
                "clause_number": clause["clause_number"],
                "extraction": extraction,
                "note": "re-extracted with cross-referenced clause context",
            })

    precedent_matches = None
    if extraction["unusual_wording"] or extraction["confidence"] < PRECEDENT_CHECK_THRESHOLD:
        precedent = search_similar_contracts(query_text=clause["text"], top_k=3)
        precedent_matches = precedent["matches"]
        log_decision(run_id, "tool_call", {
            "tool": "search_similar_contracts",
            "clause_number": clause["clause_number"],
            "trigger": "llm_flagged_unusual" if extraction["unusual_wording"] else "low_confidence_floor",
            "match_count": len(precedent_matches),
        })

    result = {
        "clause_number": clause["clause_number"],
        "has_obligation": extraction["has_obligation"],
        "action": None,       # "reminder" | "escalated" | None
        "obligation_id": None,
        # Carry the real per-clause LLM confidence through so the
        # run-level aggregate uses actual extraction scores, not a
        # binary reminder/escalated ratio that always yields 1.00.
        "confidence": extraction["confidence"],
    }

    if extraction["has_obligation"]:
        # Resolve obligation date before inserting — best-effort,
        # never blocks the main extraction pipeline.
        date_result = _resolve_obligation_date(
            raw_date_or_condition=extraction["raw_date_or_condition"],
            effective_date=effective_date,
            key_index=key_index,
        )
        log_decision(run_id, "llm_reasoning", {
            "clause_number": clause["clause_number"],
            "date_status": date_result["date_status"],
            "obligation_date": str(date_result["obligation_date"]) if date_result["obligation_date"] else None,
            "date_reasoning": date_result["reasoning"],
        })

        obligation_id = _insert_obligation(
            contract_id,
            extraction,
            obligation_date=date_result["obligation_date"],
        )
        result["obligation_id"] = obligation_id

        # Approach B: adjust confidence used for the action gate based
        # on date clarity. Vague/event-triggered timing makes the
        # obligation harder to auto-action, so reduce effective
        # confidence to route these toward human review.
        date_status = date_result["date_status"]
        effective_confidence = extraction["confidence"]
        if date_status == "vague":
            effective_confidence -= VAGUE_DATE_CONFIDENCE_PENALTY
        elif date_status == "event_triggered":
            effective_confidence -= EVENT_TRIGGERED_CONFIDENCE_PENALTY
        effective_confidence = max(0.0, effective_confidence)

        if effective_confidence >= ACTION_THRESHOLD:
            tool_result = execute_tool(
                "contract_tracking", "create_calendar_reminder", {"obligation_id": obligation_id}
            )
            log_decision(run_id, "action", {
                "clause_number": clause["clause_number"],
                "obligation_id": obligation_id,
                "tool": "create_calendar_reminder",
                "result": tool_result,
            })
            result["action"] = "reminder"
            result["action_detail"] = tool_result
        else:
            # Build a contextual, informative escalation reason based on
            # what type of timing issue was found (generalised for all
            # obligation types, not just vague ones).
            raw_timing = extraction["raw_date_or_condition"].strip()
            description = extraction["description"]
            confidence = extraction["confidence"]
            clause_num = clause["clause_number"]
            # date_status and effective_confidence already computed above
            computed_date = date_result["obligation_date"]

            if date_status == "computed" and computed_date:
                reason = (
                    f"Extraction confidence {extraction['confidence']:.2f} (effective: {effective_confidence:.2f}) below threshold "
                    f"{ACTION_THRESHOLD:.2f} for clause {clause_num}. "
                    f"Obligation: '{description}'. "
                    f"Timing as written: '{raw_timing}' — computed deadline: "
                    f"{computed_date.isoformat()}. "
                    f"Reviewer should confirm this date and approve or adjust "
                    f"the calendar reminder."
                )
            elif date_status == "vague":
                reason = (
                    f"Extraction confidence {extraction['confidence']:.2f} (effective: {effective_confidence:.2f}) below threshold "
                    f"{ACTION_THRESHOLD:.2f} for clause {clause_num}. "
                    f"Obligation: '{description}'. "
                    f"Timing as written: '{raw_timing}' — vague or unresolvable "
                    f"(e.g. 'reasonable time', 'from time to time', 'as agreed'). "
                    f"Reviewer should determine the appropriate deadline."
                )
            elif date_status == "event_triggered":
                reason = (
                    f"Extraction confidence {extraction['confidence']:.2f} (effective: {effective_confidence:.2f}) below threshold "
                    f"{ACTION_THRESHOLD:.2f} for clause {clause_num}. "
                    f"Obligation: '{description}'. "
                    f"Timing as written: '{raw_timing}' — depends on a future "
                    f"event that has not yet occurred. "
                    f"Reviewer should monitor the triggering event and set a "
                    f"reminder when it happens."
                )
            elif date_status == "no_timing":
                reason = (
                    f"Extraction confidence {extraction['confidence']:.2f} (effective: {effective_confidence:.2f}) below threshold "
                    f"{ACTION_THRESHOLD:.2f} for clause {clause_num}. "
                    f"Obligation: '{description}'. "
                    f"No temporal component specified — this is a standing duty "
                    f"with no deadline. "
                    f"Reviewer should determine when and how to action this "
                    f"obligation."
                )
            else:
                # empty or fallback
                reason = (
                    f"Extraction confidence {extraction['confidence']:.2f} (effective: {effective_confidence:.2f}) below threshold "
                    f"{ACTION_THRESHOLD:.2f} for clause {clause_num}. "
                    f"Obligation: '{description}'. "
                    f"Reviewer should confirm the obligation details and set "
                    f"an appropriate reminder."
                )

            tool_result = execute_tool(
                "contract_tracking",
                "flag_for_manual_review",
                {"obligation_id": obligation_id, "run_id": run_id, "reason": reason},
            )
            log_decision(run_id, "escalation", {
                "clause_number": clause["clause_number"],
                "obligation_id": obligation_id,
                "reason": reason,
            })
            result["action"] = "escalated"
            result["reason"] = reason

    # Persist this clause into the KB now (not deferred to a
    # separate step), so later clauses in the SAME contract can
    # also match against it via search_similar_contracts — decided
    # deliberately over "persist only after the full loop finishes".
    upsert_embedding(
        vertical="contract_tracking",
        source_type="contract_clause",
        chunk_text=clause["text"],
        source_id=contract_id,
        metadata={"clause_number": clause["clause_number"], "title": clause["title"]},
    )

    return result


def run_contract_tracking_vertical(agent_input: AgentRunInput) -> AgentRunOutput:
    """
    Entry point (Section 3.2, 7.2). Handles both trigger shapes
    Section 8.3 lists: a direct upload (trigger_type=upload,
    input_document_id set) and the scheduled-ingestion-collapsed-
    with-trigger case (trigger_type=scheduled_ingestion, Section
    6.4) — both resolve to contract text the same way, the only
    difference is which TriggerType value gets logged on the run.
    """
    if agent_input.input_document_id:
        contract_text = resolve_document_text(agent_input.input_document_id)
        doc_id = agent_input.input_document_id
    elif agent_input.input_payload and "text" in agent_input.input_payload:
        contract_text = agent_input.input_payload["text"]
        doc_id = None
    else:
        raise ValueError(
            "The contract_tracking vertical requires either input_document_id "
            "or input_payload={'text': ...}."
        )

    run_id = start_run(
        vertical=agent_input.vertical,
        trigger_type=agent_input.trigger_type.value,
        input_document_id=agent_input.input_document_id,
    )

    clauses, effective_date = split_contract_into_clauses(contract_text)
    contract_id = _insert_contract(
        vendor_name=None,
        doc_id=doc_id,
        run_id=run_id,
        effective_date=effective_date,
    )
    log_decision(run_id, "retrieval", {
        "clause_count": len(clauses),
        "effective_date": effective_date,
    })

    # Explicit loop, not a list comprehension: if _process_clause
    # raises partway through (e.g. a rate-limited LLM call on clause
    # 4 of 7), clauses 0-3 may already have created real obligations
    # and fired real create_calendar_reminder actions — those are
    # genuine, correct work already done and must not be discarded.
    # But without this try/except, the exception would propagate out
    # of run_contract_tracking_vertical() entirely, skipping
    # complete_agent_run() and leaving the run permanently stuck at
    # status='running' with no record of why, even though real work
    # already happened. Caught here, the run instead closes out as
    # escalated, crediting whatever succeeded and flagging the
    # unprocessed remainder for a human — a partial failure becomes
    # a visible, actionable HITL item instead of a silent stuck run.
    clause_results = []
    partial_failure = None
    for i, clause in enumerate(clauses):
        try:
            clause_results.append(_process_clause(run_id, contract_id, clause, clause_index=i, effective_date=effective_date))
        except Exception as e:
            unprocessed = [c["clause_number"] for c in clauses[i:]]
            partial_failure = {
                "failed_clause_number": clause["clause_number"],
                "unprocessed_clause_numbers": unprocessed,
                # Raw error kept for log_decision debugging only —
                # never surfaced in the human-facing escalation reason.
                "error": str(e),
                "error_category": _classify_error(e),
            }
            log_decision(run_id, "escalation", {
                "reason": "processing_failed_partway_through_contract",
                **partial_failure,
            })
            break

    actions_taken = [
        {
            "action_name": "create_calendar_reminder",
            "target_id": r["obligation_id"],
            "result": r.get("action_detail"),
        }
        for r in clause_results
        if r["action"] == "reminder"
    ]
    escalations = [
        {"reason": r["reason"]}
        for r in clause_results
        if r["action"] == "escalated"
    ]

    if partial_failure:
        reason = (
            f"Processing failed at clause {partial_failure['failed_clause_number']} "
            f"({partial_failure['error_category']}). Clause(s) "
            f"{', '.join(partial_failure['unprocessed_clause_numbers'])} were never "
            f"processed and need manual review. Obligations already found in this "
            f"contract before the failure were still acted on normally."
        )
        create_escalation(run_id=run_id, reason=reason, pending_action=None)
        escalations.append({"reason": reason})

    obligation_confidences = [
        r for r in clause_results if r["has_obligation"]
    ]
    # Run-level confidence: average across obligations found: a
    # simple, defensible aggregate for the evaluation dashboard
    # (Section 5, Section 11.1's "confidence distribution" metric).
    # A contract with no obligations at all reports full confidence
    # (nothing was uncertain, because nothing was extracted).
    if obligation_confidences:
        # Average the real per-clause LLM extraction confidence
        # scores rather than a binary reminder/escalated ratio.
        # The binary formula always yields exactly 1.00 when all
        # obligations are auto-reminded, which is epistemically
        # wrong — no ML model should ever be 1.00 certain. Real
        # scores (e.g. 0.95, 0.97, 0.98) reflect genuine
        # extraction quality and look credible on the dashboard.
        run_confidence = sum(
            r["confidence"] for r in obligation_confidences
        ) / len(obligation_confidences)
    else:
        # No obligations found: report 1.0 — the model was fully
        # certain there was nothing to extract (e.g. a pure NDA).
        run_confidence = 0.99

    final_state = {
        "run_id": run_id,
        "confidence": run_confidence,
        "actions_taken": actions_taken,
        "escalations": escalations,
    }

    status = "escalated" if escalations else "completed"
    complete_agent_run(run_id, status=status, confidence=run_confidence)

    return build_agent_run_output(final_state)


from app.core.vertical_registry import register_vertical

register_vertical("contract_tracking", run_contract_tracking_vertical)
