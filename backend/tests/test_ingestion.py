"""
Tests for app.core.ingestion (Section 6). This module predates the
pytest suite (built manually, verified by hand at the time) — this
file gives it real, permanent regression coverage.
"""

import pytest
from app.core.ingestion import (
    extract_text,
    default_paragraph_chunker,
    ingest_staging_folder,
)


# ---------------------------------------------------------------
# extract_text
# ---------------------------------------------------------------

def test_extract_text_reads_txt_file(tmp_path):
    file_path = tmp_path / "note.txt"
    file_path.write_text("Some plain text content.", encoding="utf-8")

    assert extract_text(file_path) == "Some plain text content."


def test_extract_text_raises_for_unsupported_extension(tmp_path):
    file_path = tmp_path / "image.png"
    file_path.write_bytes(b"\x89PNG...")

    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_text(file_path)


def test_extract_text_raises_for_dotfile_with_no_extension(tmp_path):
    """
    .gitkeep and similar have an empty suffix (Path('.gitkeep').suffix
    == ''), which previously fell through to the generic 'unsupported
    file type' error — this is exactly the bug ingest_staging_folder
    now avoids by skipping dotfiles before ever calling extract_text.
    This test documents extract_text's own behavior in isolation.
    """
    file_path = tmp_path / ".gitkeep"
    file_path.write_text("")

    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_text(file_path)


# ---------------------------------------------------------------
# default_paragraph_chunker
# ---------------------------------------------------------------

def test_default_paragraph_chunker_splits_on_blank_lines():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = default_paragraph_chunker(text)
    assert chunks == ["First paragraph.", "Second paragraph.", "Third paragraph."]


def test_default_paragraph_chunker_drops_empty_paragraphs():
    text = "First.\n\n\n\nSecond.\n\n   \n\nThird."
    chunks = default_paragraph_chunker(text)
    assert chunks == ["First.", "Second.", "Third."]


def test_default_paragraph_chunker_strips_whitespace():
    text = "  Padded paragraph.  \n\nAnother.  "
    chunks = default_paragraph_chunker(text)
    assert chunks == ["Padded paragraph.", "Another."]


# ---------------------------------------------------------------
# ingest_staging_folder — filesystem edge cases (no DB/model needed,
# since these never get past the dotfile-skip or missing-folder check)
# ---------------------------------------------------------------

def test_ingest_staging_folder_reports_error_for_missing_folder(monkeypatch, tmp_path):
    monkeypatch.setattr("app.core.ingestion.STAGING_ROOT", tmp_path)

    summary = ingest_staging_folder(vertical="nonexistent_vertical", source_type="postmortem")

    assert summary["processed"] == []
    assert summary["skipped"] == []
    assert len(summary["errors"]) == 1
    assert "does not exist" in summary["errors"][0]


def test_ingest_staging_folder_skips_dotfiles_without_error(monkeypatch, tmp_path):
    """
    Regression test for the real bug hit in production: a staging
    folder containing only a .gitkeep placeholder (the normal state
    for a real vertical's folder before any real files arrive)
    should produce zero errors, not an 'unsupported file type' error
    for the .gitkeep itself.
    """
    vertical_folder = tmp_path / "some_vertical"
    vertical_folder.mkdir()
    (vertical_folder / ".gitkeep").write_text("")

    monkeypatch.setattr("app.core.ingestion.STAGING_ROOT", tmp_path)

    summary = ingest_staging_folder(vertical="some_vertical", source_type="postmortem")

    assert summary == {"processed": [], "skipped": [], "errors": []}


def test_ingest_staging_folder_reports_unsupported_file_as_error_not_crash(
    monkeypatch, tmp_path
):
    """
    A genuinely unsupported file (not a dotfile) should be reported
    in the errors list, without stopping the function or raising —
    matching ingest_staging_folder's own try/except-per-file design.
    """
    vertical_folder = tmp_path / "some_vertical"
    vertical_folder.mkdir()
    (vertical_folder / "image.png").write_bytes(b"\x89PNG...")

    monkeypatch.setattr("app.core.ingestion.STAGING_ROOT", tmp_path)

    summary = ingest_staging_folder(vertical="some_vertical", source_type="postmortem")

    assert summary["processed"] == []
    assert len(summary["errors"]) == 1
    assert "Unsupported file type" in summary["errors"][0]


# ---------------------------------------------------------------
# ingest_staging_folder — full pipeline (needs the real embedding
# model, same as test_dummy_vertical_integration.py — network access
# to huggingface.co required on first run in a fresh environment)
# ---------------------------------------------------------------

def test_ingest_staging_folder_processes_new_file_end_to_end(monkeypatch, tmp_path):
    from app.core.db import get_connection

    vertical_folder = tmp_path / "some_vertical"
    vertical_folder.mkdir()
    (vertical_folder / "note.txt").write_text(
        "Summary: A test incident.\n\nRoot Cause: A test cause."
    )

    monkeypatch.setattr("app.core.ingestion.STAGING_ROOT", tmp_path)

    summary = ingest_staging_folder(vertical="some_vertical", source_type="postmortem")

    assert summary["errors"] == []
    assert summary["processed"] == ["some_vertical/note.txt"]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT chunk_text FROM embeddings WHERE vertical = 'some_vertical' "
                "ORDER BY chunk_text;"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    assert len(rows) == 2
    assert rows[0]["chunk_text"] == "Root Cause: A test cause."
    assert rows[1]["chunk_text"] == "Summary: A test incident."


def test_ingest_staging_folder_skips_unchanged_file_on_second_run(monkeypatch, tmp_path):
    vertical_folder = tmp_path / "some_vertical"
    vertical_folder.mkdir()
    (vertical_folder / "note.txt").write_text("Some content.")

    monkeypatch.setattr("app.core.ingestion.STAGING_ROOT", tmp_path)

    first_summary = ingest_staging_folder(vertical="some_vertical", source_type="postmortem")
    assert first_summary["processed"] == ["some_vertical/note.txt"]

    second_summary = ingest_staging_folder(vertical="some_vertical", source_type="postmortem")
    assert second_summary["processed"] == []
    assert second_summary["skipped"] == ["some_vertical/note.txt"]


def test_ingest_staging_folder_reprocesses_changed_file(monkeypatch, tmp_path):
    vertical_folder = tmp_path / "some_vertical"
    vertical_folder.mkdir()
    file_path = vertical_folder / "note.txt"
    file_path.write_text("Original content.")

    monkeypatch.setattr("app.core.ingestion.STAGING_ROOT", tmp_path)

    ingest_staging_folder(vertical="some_vertical", source_type="postmortem")

    file_path.write_text("Changed content.")
    summary = ingest_staging_folder(vertical="some_vertical", source_type="postmortem")

    assert summary["processed"] == ["some_vertical/note.txt"]
    assert summary["skipped"] == []