"""
Contract Obligation & Renewal Tracking Agent — Clause Chunking
(Vertical 3, Section 8.3: "KB holds contract clauses chunked at
clause level (not by section or whole document)").

Contracts in the wild (and in our synthetic corpus) are not
consistently numbered or formatted, so clause boundaries are
detected by the LLM rather than by regex on heading patterns. This
costs one LLM call per contract but is far more robust than pattern
matching against inconsistent legal-document formatting.

This module is intentionally decoupled from app.core.ingestion's
generic chunk_fn: Callable[[str], list[str]] contract. That signature
only returns chunk *text*, but Vertical 3 needs to retain each
clause's number/title as structured metadata (Section 4.3: contract
clause metadata is { contract_id, clause_number }) so that
get_surrounding_clauses() can later do a direct lookup by clause
number rather than a similarity search (Section 8.3, Retrieval
Logic). Returning richer dicts here, and handling embedding storage
separately in ingest.py, keeps that metadata intact end to end.
"""

import json

from app.core.llm_gateway import call_llm

_SYSTEM_PROMPT = """You split contract text into individual clauses.

A clause is a single distinct obligation, right, or provision — \
typically (but not always) marked by a number or heading in the \
source text. Preserve the original wording of each clause exactly; \
do not summarize or rewrite it.

Respond with ONLY a JSON array, no other text, in this exact shape:
[
  {"clause_number": "1", "title": "Term and Termination", "text": "..."},
  {"clause_number": "2", "title": "Confidentiality", "text": "..."}
]

If the source text has no explicit numbering, assign sequential \
clause_number values ("1", "2", "3", ...) yourself based on \
paragraph/topic boundaries. If a clause has no clear title, use a \
short title you infer from its content. Every word of the original \
text should end up in exactly one clause — do not skip or duplicate \
content."""


def split_contract_into_clauses(text: str) -> list[dict]:
    """
    Splits raw contract text into clauses via a single LLM call.

    Returns a list of dicts: [{"clause_number": str, "title": str,
    "text": str}, ...], in document order.

    Raises ValueError if the LLM response isn't valid JSON in the
    expected shape — this is treated as a hard failure rather than a
    silent fallback, since a mis-chunked contract would corrupt every
    downstream extraction and cross-reference lookup for that
    document.
    """
    response = call_llm(
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.0,
    )

    raw = (response.get("content") or "").strip()
    raw = _strip_code_fences(raw)

    try:
        clauses = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Clause chunker returned invalid JSON: {e}\nRaw response: {raw[:500]}"
        ) from e

    if not isinstance(clauses, list) or not clauses:
        raise ValueError(
            f"Clause chunker expected a non-empty JSON array, got: {raw[:500]}"
        )

    normalized = []
    for i, clause in enumerate(clauses):
        if not isinstance(clause, dict) or "text" not in clause:
            raise ValueError(f"Clause {i} missing required 'text' field: {clause}")
        normalized.append(
            {
                "clause_number": str(clause.get("clause_number", i + 1)),
                "title": clause.get("title", "").strip(),
                "text": clause["text"].strip(),
            }
        )

    return normalized


def _strip_code_fences(raw: str) -> str:
    """LLMs sometimes wrap JSON in ```json ... ``` despite instructions not to."""
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines)
    return raw.strip()
