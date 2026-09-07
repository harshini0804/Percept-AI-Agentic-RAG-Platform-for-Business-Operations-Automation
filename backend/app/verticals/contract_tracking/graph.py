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

import os

from app.core.llm_gateway import call_llm
from app.core.logging_service import log_decision, complete_agent_run
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
ACTION_THRESHOLD = float(os.getenv("CONTRACT_TRACKING_ACTION_THRESHOLD", "0.75"))
# PRECEDENT_CHECK_THRESHOLD is the code-gated floor that triggers
# search_similar_contracts even when the LLM itself didn't flag the
# clause as unusual_wording (see graph.py's module-level design
# discussion: hybrid trigger — LLM judgment first-class, confidence
# floor as a safety net).
PRECEDENT_CHECK_THRESHOLD = float(os.getenv("CONTRACT_TRACKING_PRECEDENT_THRESHOLD", "0.6"))


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


def _extract_clause(clause_text: str, extra_context: str | None = None) -> dict:
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


def _insert_obligation(contract_id: str, extraction: dict) -> str:
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
                    None,  # obligation_date: raw_date_or_condition is often not a
                           # clean SQL DATE (e.g. "within 90 days of expiry"); left
                           # for a future date-parsing pass rather than guessed here.
                    "obligation",
                    extraction["confidence"],
                ),
            )
            obligation_id = cur.fetchone()["id"]
        conn.commit()
        return str(obligation_id)
    finally:
        conn.close()


def _insert_contract(vendor_name: str | None, doc_id: str | None) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO contracts (doc_id, vendor_name) VALUES (%s, %s) RETURNING id;",
                (doc_id, vendor_name),
            )
            contract_id = cur.fetchone()["id"]
        conn.commit()
        return str(contract_id)
    finally:
        conn.close()


def _process_clause(run_id: str, contract_id: str, clause: dict) -> dict:
    """
    Handles one clause end to end: extraction, optional cross-
    reference re-extraction, optional precedent check, obligation
    insertion (if any), per-obligation confidence gate, and clause
    persistence into the KB. Returns a dict describing what
    happened, for the caller to fold into the run's final state.
    """
    extraction = _extract_clause(clause["text"])
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
            extraction = _extract_clause(clause["text"], extra_context=surrounding["text"])
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
    }

    if extraction["has_obligation"]:
        obligation_id = _insert_obligation(contract_id, extraction)
        result["obligation_id"] = obligation_id

        if extraction["confidence"] >= ACTION_THRESHOLD:
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
            reason = (
                f"Extraction confidence {extraction['confidence']:.2f} below "
                f"threshold {ACTION_THRESHOLD:.2f} for clause {clause['clause_number']}."
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

    contract_id = _insert_contract(vendor_name=None, doc_id=doc_id)

    clauses = split_contract_into_clauses(contract_text)
    log_decision(run_id, "retrieval", {"clause_count": len(clauses)})

    clause_results = [_process_clause(run_id, contract_id, clause) for clause in clauses]

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

    obligation_confidences = [
        r for r in clause_results if r["has_obligation"]
    ]
    # Run-level confidence: average across obligations found: a
    # simple, defensible aggregate for the evaluation dashboard
    # (Section 5, Section 11.1's "confidence distribution" metric).
    # A contract with no obligations at all reports full confidence
    # (nothing was uncertain, because nothing was extracted).
    if obligation_confidences:
        run_confidence = sum(
            1.0 if r["action"] == "reminder" else 0.0 for r in obligation_confidences
        ) / len(obligation_confidences)
    else:
        run_confidence = 1.0

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
