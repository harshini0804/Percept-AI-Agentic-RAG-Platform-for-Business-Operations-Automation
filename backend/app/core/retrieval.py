"""
RAG Infrastructure — Retrieval (Section 3.1, 3.3 Stages 2 & 3)

Provides:
- search_embeddings: plain semantic search, filtered by vertical
  and source_type, ordered by cosine similarity (Section 4.2/4.3).
- search_with_retry: the code-gated, one-retry pattern used across
  Verticals 1 and 2 (Section 8.1, 8.2 Agentic Decision Points).
  Query reformulation is the LLM's job, not this function's — it
  only accepts a pre-reformulated query for the retry attempt.
"""

from typing import Callable, Optional
from app.core.embeddings import embed_text
from app.core.db import get_connection


def search_embeddings(
    query_text: str,
    vertical: str,
    source_type: str,
    top_k: int = 5,
    extra_filter_sql: str = "",
    extra_filter_params: tuple = (),
) -> list[dict]:
    query_vector = embed_text(query_text)  # numpy array

    sql = f"""
        SELECT
            id, vertical, source_type, source_id, chunk_text, metadata, created_at,
            1 - (embedding <=> %s) AS similarity
        FROM embeddings
        WHERE vertical = %s AND source_type = %s
        {extra_filter_sql}
        ORDER BY embedding <=> %s
        LIMIT %s;
    """
    params = (query_vector, vertical, source_type, *extra_filter_params, query_vector, top_k)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def search_with_retry(
    query_text: str,
    vertical: str,
    source_type: str,
    confidence_threshold: float,
    reformulate_query_fn: Optional[Callable[[str, list[dict]], str]] = None,
    top_k: int = 5,
    extra_filter_sql: str = "",
    extra_filter_params: tuple = (),
) -> tuple[list[dict], bool]:
    """
    Section 3.3 Stage 3 — confidence-gate on retrieval, capped at one retry.

    1. Run search_embeddings with the original query.
    2. If the top result's similarity < confidence_threshold, and a
       reformulate_query_fn is provided, call it (this is where the
       vertical's LLM broadens the query — Section 8.1/8.2), then
       retry exactly once with the new query.
    3. Never retries more than once, regardless of the second
       attempt's result — matches "capped at one retry" exactly.

    Returns (results, was_retried).
    """
    results = search_embeddings(
        query_text, vertical, source_type, top_k, extra_filter_sql, extra_filter_params
    )

    top_score = results[0]["similarity"] if results else 0.0

    if top_score < confidence_threshold and reformulate_query_fn is not None:
        reformulated_query = reformulate_query_fn(query_text, results)
        retried_results = search_embeddings(
            reformulated_query, vertical, source_type, top_k, extra_filter_sql, extra_filter_params
        )
        return retried_results, True

    return results, False