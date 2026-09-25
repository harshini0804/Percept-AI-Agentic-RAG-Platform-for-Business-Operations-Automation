"""
Tests for app.api.documents — the "view original document" endpoint
behind the Report viewer's new document panel (Phase, UI feature 4).
Pure DB + isolated filesystem, no embedding model needed.
"""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.documents import create_document
import app.core.documents as documents_module

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_submitted_root(monkeypatch, tmp_path):
    # Same isolation pattern as test_documents.py — required, not
    # optional, since the real default only exists inside Docker.
    monkeypatch.setattr(documents_module, "SUBMITTED_ROOT", tmp_path / "submitted")


def test_get_document_returns_content_and_metadata():
    doc_id = create_document(
        vertical="dummy",
        filename="incident_report.txt",
        raw_bytes=b"The server crashed due to a memory leak.",
    )

    response = client.get(f"/documents/{doc_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == doc_id
    assert body["filename"] == "incident_report.txt"
    assert body["vertical"] == "dummy"
    assert body["content"] == "The server crashed due to a memory leak."
    assert "uploaded_at" in body


def test_get_document_returns_404_for_nonexistent_id():
    import uuid

    response = client.get(f"/documents/{uuid.uuid4()}")
    assert response.status_code == 404


def test_get_document_returns_404_when_text_file_missing(tmp_path, monkeypatch):
    """
    A documents row exists (e.g. from a real submission) but its
    extracted-text file is somehow missing on disk — should surface
    as a clear 404, not an unhandled 500.
    """
    doc_id = create_document(
        vertical="dummy",
        filename="will_be_deleted.txt",
        raw_bytes=b"temporary content",
    )

    # Delete the extracted text file out from under the documents row.
    (documents_module.SUBMITTED_ROOT / f"{doc_id}.txt").unlink()

    response = client.get(f"/documents/{doc_id}")
    assert response.status_code == 404