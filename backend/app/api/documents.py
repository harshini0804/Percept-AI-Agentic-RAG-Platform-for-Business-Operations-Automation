"""
Documents API — lets the Report viewer show the original file a run
was submitted from. input_document_id has always been stored on
agent_runs and resolve_document_text() has always existed (Phase C),
but nothing previously exposed either over HTTP — this closes that
gap, entirely shared-core, no vertical-specific code needed.
"""

from fastapi import APIRouter, HTTPException
from app.core.db import get_connection
from app.core.documents import resolve_document_text
from app.schemas.api_models import DocumentContent

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/{document_id}", response_model=DocumentContent)
def get_document(document_id: str):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, filename, vertical, uploaded_at FROM documents WHERE id = %s;",
                (document_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        content = resolve_document_text(document_id)
    except FileNotFoundError:
        # Row exists but the extracted text file is missing — a real,
        # if unlikely, inconsistency worth surfacing clearly rather
        # than crashing with an unhandled exception.
        raise HTTPException(
            status_code=404,
            detail="This document's original text could not be found on disk.",
        )

    return DocumentContent(
        id=str(row["id"]),
        filename=row["filename"],
        vertical=row["vertical"],
        uploaded_at=row["uploaded_at"],
        content=content,
    )