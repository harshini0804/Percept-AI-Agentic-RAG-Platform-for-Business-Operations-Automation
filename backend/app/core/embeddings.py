"""
RAG Infrastructure — Embedding (Section 3.1, 3.3 Stage 1, Section 6.4)
"""

import numpy as np
from sentence_transformers import SentenceTransformer
from psycopg2.extras import Json
from app.core.db import get_connection

_model = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def embed_text(text: str) -> np.ndarray:
    """Embed a single string. Returns a 384-dim numpy array."""
    model = _get_model()
    return model.encode(text, convert_to_numpy=True)


def embed_batch(texts: list[str]) -> list[np.ndarray]:
    """Embed multiple strings at once."""
    model = _get_model()
    vectors = model.encode(texts, convert_to_numpy=True)
    return [v for v in vectors]


def upsert_embedding(
    vertical: str,
    source_type: str,
    chunk_text: str,
    source_id: str | None = None,
    metadata: dict | None = None,
) -> str:
    vector = embed_text(chunk_text)  # numpy array — register_vector() handles this type

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO embeddings
                    (vertical, source_type, source_id, chunk_text, embedding, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    vertical,
                    source_type,
                    source_id,
                    chunk_text,
                    vector,
                    Json(metadata) if metadata else None,
                ),
            )
            new_id = cur.fetchone()["id"]
        conn.commit()
        return str(new_id)
    finally:
        conn.close()