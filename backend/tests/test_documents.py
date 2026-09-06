"""
Tests for app.core.documents (Section 4.1, Phase C) — tracking
directly-submitted files and resolving them back to extracted text.

SUBMITTED_ROOT is redirected to an isolated tmp_path for every test
(matching how test_ingestion.py isolates STAGING_ROOT) — this is
required, not optional: the real default (a sibling of STAGING_ROOT,
normally /app/uploads/submitted) only exists inside the Docker
container, and unprivileged environments (including GitHub Actions'
runner user) correctly get PermissionError trying to create /app at
the filesystem root outside a container.
"""

import pytest
from app.core.db import get_connection
import app.core.documents as documents_module
from app.core.documents import create_document, resolve_document_text


@pytest.fixture(autouse=True)
def _isolated_submitted_root(monkeypatch, tmp_path):
    monkeypatch.setattr(documents_module, "SUBMITTED_ROOT", tmp_path / "submitted")


def _fetch_document(document_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM documents WHERE id = %s;", (document_id,))
            return cur.fetchone()
    finally:
        conn.close()


def test_create_document_extracts_text_and_creates_row():
    raw_bytes = b"Summary: A test incident.\n\nRoot Cause: A test cause."
    document_id = create_document(
        vertical="post_incident", filename="test.txt", raw_bytes=raw_bytes
    )

    row = _fetch_document(document_id)
    assert row is not None
    assert row["vertical"] == "post_incident"
    assert row["filename"] == "test.txt"
    assert row["source"] == "upload"
    assert row["content_hash"] == __import__("hashlib").sha256(raw_bytes).hexdigest()


def test_create_document_persists_extracted_text_for_later_resolution():
    raw_bytes = b"This is the extracted content of the file."
    document_id = create_document(
        vertical="post_incident", filename="notes.txt", raw_bytes=raw_bytes
    )

    resolved_text = resolve_document_text(document_id)
    assert resolved_text == "This is the extracted content of the file."


def test_create_document_uses_custom_source():
    raw_bytes = b"content"
    document_id = create_document(
        vertical="contract_tracking",
        filename="contract.txt",
        raw_bytes=raw_bytes,
        source="scheduled_ingestion",
    )
    row = _fetch_document(document_id)
    assert row["source"] == "scheduled_ingestion"


def test_resolve_document_text_raises_for_unknown_document_id():
    with pytest.raises(FileNotFoundError, match="No extracted text found"):
        resolve_document_text("00000000-0000-0000-0000-000000000000")


def test_create_document_rejects_unsupported_file_type():
    with pytest.raises(ValueError, match="Unsupported file type"):
        create_document(
            vertical="post_incident", filename="image.png", raw_bytes=b"\x89PNG..."
        )