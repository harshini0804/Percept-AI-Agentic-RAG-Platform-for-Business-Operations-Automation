"""
Documents (Section 4.1) — shared helpers for tracking directly
submitted files (via the Submission page's file upload), as opposed
to staging-folder ingestion (Section 6), which tracks its own state
via kb_sync_state and never creates a `documents` row per file.

The `documents` table itself has no content column (Section 4.1:
id, vertical, filename, source, content_hash, uploaded_at) — it's a
tracking/audit row, not storage. The actual extracted text for a
submitted document is saved alongside it, under SUBMITTED_ROOT, so a
vertical can resolve an AgentRunInput.input_document_id into real
text without re-parsing the original file every time.
"""

import os
import hashlib
import tempfile
from pathlib import Path

from app.core.db import get_connection
from app.core.ingestion import extract_text, STAGING_ROOT

# Sibling of the staging folder (STAGING_ROOT is .../uploads/staging,
# this is .../uploads/submitted) — both live under the same mounted
# /uploads volume (Section 6.2's convention, extended for direct
# submissions rather than staged files).
SUBMITTED_ROOT = STAGING_ROOT.parent / "submitted"


def create_document(
    vertical: str,
    filename: str,
    raw_bytes: bytes,
    source: str = "upload",
) -> str:
    """
    Extracts text from an uploaded file's raw bytes and creates its
    tracking row in `documents`. Returns the new document's id.

    Extraction reuses the same extract_text() ingestion already uses
    for staged files — raw_bytes are written to a short-lived temp
    file (matching the original filename's extension, so the
    extractor can detect the file type) purely so the existing,
    already-tested Path-based extractor can be reused unchanged.
    """
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    suffix = Path(filename).suffix

    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(raw_bytes)
        tmp.flush()
        extracted_text = extract_text(Path(tmp.name))

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (vertical, filename, source, content_hash)
                VALUES (%s, %s, %s, %s)
                RETURNING id;
                """,
                (vertical, filename, source, content_hash),
            )
            document_id = cur.fetchone()["id"]
        conn.commit()
    finally:
        conn.close()

    SUBMITTED_ROOT.mkdir(parents=True, exist_ok=True)
    (SUBMITTED_ROOT / f"{document_id}.txt").write_text(extracted_text, encoding="utf-8")

    return str(document_id)


def resolve_document_text(document_id: str) -> str:
    """
    Reads back the extracted text for a document created via
    create_document(). Raises FileNotFoundError if the document_id
    doesn't correspond to a previously submitted document — this
    surfaces as a clear error rather than silently returning empty
    text if a vertical is given a bad/stale document_id.
    """
    path = SUBMITTED_ROOT / f"{document_id}.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"No extracted text found for document '{document_id}'. "
            f"Was it created via create_document()?"
        )
    return path.read_text(encoding="utf-8")