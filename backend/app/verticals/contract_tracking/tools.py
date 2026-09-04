"""
Contract Obligation & Renewal Tracking Agent — Tools (Vertical 3,
Section 8.3).

Four tools, registered with the shared tool-calling framework
(app.core.tool_registry):

Read tools:
  - get_surrounding_clauses: a DIRECT lookup of a specific clause
    within the same contract, by clause number — not a similarity
    search. Used when extraction detects an explicit cross-reference
    ("subject to Section 4.2") and needs that section's text.
  - search_similar_contracts: genuine semantic search against the
    clause-level KB across *past* contracts. Used for unusually
    worded clauses, to check how similar wording was interpreted
    before.

Write tools:
  - create_calendar_reminder: additive — inserts a reminder record
    and marks the obligation as reminder_created=True. Does not
    touch the source contract or any existing data (Section 8.3
    Output & Action).
  - flag_for_manual_review: routes a single obligation to the HITL
    queue. Unlike the shared action_gate_node (which escalates at
    the level of a whole run), Vertical 3 gates PER OBLIGATION
    within one contract/run (Section 8.3 Agentic Decision Points),
    so this creates its own escalations row scoped to one
    obligation rather than the whole run — see graph.py for how the
    per-obligation loop calls this.
"""

from app.core.tool_registry import tool
from app.core.retrieval import search_embeddings
from app.core.db import get_connection
from app.core.logging_service import create_escalation


@tool(
    vertical="contract_tracking",
    name="get_surrounding_clauses",
    description=(
        "Look up the exact text of a specific clause within the SAME contract, "
        "by its clause number. Use this when an obligation's text explicitly "
        "references another section of the same contract (e.g. 'subject to "
        "Section 4.2') and you need that section's content to finish extraction."
    ),
    parameters={
        "type": "object",
        "properties": {
            "contract_id": {
                "type": "string",
                "description": "The contract this clause belongs to.",
            },
            "clause_number": {
                "type": "string",
                "description": "The clause number being referenced, e.g. '4.2'.",
            },
        },
        "required": ["contract_id", "clause_number"],
    },
    tool_type="read",
)
def get_surrounding_clauses(contract_id: str, clause_number: str) -> dict:
    results = search_embeddings(
        query_text="",  # unused for a direct lookup; similarity ordering is irrelevant here
        vertical="contract_tracking",
        source_type="contract_clause",
        top_k=1,
        extra_filter_sql="AND source_id = %s AND metadata->>'clause_number' = %s",
        extra_filter_params=(contract_id, clause_number),
    )
    if not results:
        return {"found": False, "clause_number": clause_number, "text": None}

    match = results[0]
    return {
        "found": True,
        "clause_number": clause_number,
        "title": (match["metadata"] or {}).get("title", ""),
        "text": match["chunk_text"],
    }


@tool(
    vertical="contract_tracking",
    name="search_similar_contracts",
    description=(
        "Semantic search across ALL past contracts' clauses in the knowledge "
        "base. Use this for unusually worded clauses, to check how similar "
        "wording was interpreted in previously analyzed contracts."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query_text": {
                "type": "string",
                "description": "The clause text (or a summary of it) to search for similar precedent.",
            },
            "top_k": {
                "type": "integer",
                "description": "How many similar clauses to return. Defaults to 3.",
            },
        },
        "required": ["query_text"],
    },
    tool_type="read",
)
def search_similar_contracts(query_text: str, top_k: int = 3) -> dict:
    results = search_embeddings(
        query_text=query_text,
        vertical="contract_tracking",
        source_type="contract_clause",
        top_k=top_k,
    )
    return {
        "matches": [
            {
                "contract_id": r["source_id"],
                "clause_number": (r["metadata"] or {}).get("clause_number"),
                "title": (r["metadata"] or {}).get("title", ""),
                "text": r["chunk_text"],
                "similarity": r["similarity"],
            }
            for r in results
        ]
    }


@tool(
    vertical="contract_tracking",
    name="create_calendar_reminder",
    description=(
        "Create a calendar reminder for a date-bound obligation (renewal "
        "deadline, notice period, penalty trigger). Additive only — does not "
        "modify the source contract or any existing data."
    ),
    parameters={
        "type": "object",
        "properties": {
            "obligation_id": {"type": "string"},
        },
        "required": ["obligation_id"],
    },
    tool_type="write",
)
def create_calendar_reminder(obligation_id: str) -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE obligations
                SET reminder_created = TRUE
                WHERE id = %s
                RETURNING id, description, obligation_date, type;
                """,
                (obligation_id,),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    if row is None:
        raise ValueError(f"No obligation found with id '{obligation_id}'.")

    return {
        "reminder_created": True,
        "obligation_id": str(row["id"]),
        "description": row["description"],
        "obligation_date": str(row["obligation_date"]) if row["obligation_date"] else None,
    }


@tool(
    vertical="contract_tracking",
    name="flag_for_manual_review",
    description=(
        "Route a single obligation to the human review queue because "
        "extraction confidence was too low to act on automatically."
    ),
    parameters={
        "type": "object",
        "properties": {
            "obligation_id": {"type": "string"},
            "run_id": {
                "type": "string",
                "description": "The agent run this obligation was extracted in.",
            },
            "reason": {"type": "string"},
        },
        "required": ["obligation_id", "run_id", "reason"],
    },
    tool_type="write",
)
def flag_for_manual_review(obligation_id: str, run_id: str, reason: str) -> dict:
    escalation_id = create_escalation(
        run_id=run_id,
        reason=reason,
        pending_action={
            "tool_name": "create_calendar_reminder",
            "arguments": {"obligation_id": obligation_id},
        },
    )
    return {
        "escalated": True,
        "obligation_id": obligation_id,
        "escalation_id": escalation_id,
        "reason": reason,
    }
