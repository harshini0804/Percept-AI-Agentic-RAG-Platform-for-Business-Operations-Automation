"""
Ingestion Service (Section 6) — shared staging-folder scanner,
hash-based change detection, and embedding pipeline. Reachable via
three pathways per Section 6.4: scheduled job, manual resync, and
first-time seed — all three call ingest_staging_folder().

Chunking is vertical-specific (Section 8.1 chunks postmortems by
section, Section 8.3 chunks contracts by clause) and is passed in as
a function; text extraction by file type is common, not vertical-
specific, since it only depends on the file's extension.
"""

import os
import hashlib
from pathlib import Path
from typing import Callable

import pdfplumber
from docx import Document as DocxDocument

from app.core.db import get_connection
from app.core.embeddings import upsert_embedding

STAGING_ROOT = Path(os.getenv("STAGING_ROOT", "/app/uploads/staging"))


# ---------------------------------------------------------------
# File-type text extraction (common — varies by extension, not vertical)
# ---------------------------------------------------------------

def _extract_text(file_path: Path) -> str:
    suffix = file_path.suffix.lower()

    if suffix == ".txt":
        return file_path.read_text(encoding="utf-8", errors="ignore")

    if suffix == ".pdf":
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n\n".join(text_parts)

    if suffix == ".docx":
        doc = DocxDocument(file_path)
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())

    raise ValueError(f"Unsupported file type: {suffix}")


# ---------------------------------------------------------------
# Default chunker (fallback for verticals with no special chunking)
# ---------------------------------------------------------------

def default_paragraph_chunker(text: str) -> list[str]:
    """Splits on blank lines. Used unless a vertical passes its own chunk_fn."""
    paragraphs = [p.strip() for p in text.split("\n\n")]
    return [p for p in paragraphs if p]


# ---------------------------------------------------------------
# Hash-based change detection (Section 6.3)
# ---------------------------------------------------------------

def _compute_hash(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def _get_last_synced_hash(vertical: str, file_path: str) -> str | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT content_hash FROM kb_sync_state
                WHERE vertical = %s AND file_path = %s;
                """,
                (vertical, file_path),
            )
            row = cur.fetchone()
            return row["content_hash"] if row else None
    finally:
        conn.close()


def _update_sync_state(vertical: str, file_path: str, content_hash: str) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_sync_state (vertical, file_path, content_hash, last_synced_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (vertical, file_path)
                DO UPDATE SET content_hash = EXCLUDED.content_hash, last_synced_at = now();
                """,
                (vertical, file_path, content_hash),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------
# Main entry point — called by scheduler, admin resync, and seed.py
# ---------------------------------------------------------------

def ingest_staging_folder(
    vertical: str,
    source_type: str,
    chunk_fn: Callable[[str], list[str]] = default_paragraph_chunker,
) -> dict:
    """
    Scans /uploads/staging/<vertical>/, skips unchanged files (by
    content hash), and embeds+stores new/changed files' chunks.

    Returns a summary dict: {"processed": [...], "skipped": [...], "errors": [...]}.
    """
    folder = STAGING_ROOT / vertical
    summary = {"processed": [], "skipped": [], "errors": []}

    if not folder.exists():
        summary["errors"].append(f"Staging folder does not exist: {folder}")
        return summary

    for file_path in sorted(folder.iterdir()):
        if not file_path.is_file():
            continue

        relative_path = str(file_path.relative_to(STAGING_ROOT))

        try:
            current_hash = _compute_hash(file_path)
            last_hash = _get_last_synced_hash(vertical, relative_path)

            if current_hash == last_hash:
                summary["skipped"].append(relative_path)
                continue

            text = _extract_text(file_path)
            chunks = chunk_fn(text)

            for chunk in chunks:
                upsert_embedding(
                    vertical=vertical,
                    source_type=source_type,
                    chunk_text=chunk,
                    metadata={"file_path": relative_path},
                )

            _update_sync_state(vertical, relative_path, current_hash)
            summary["processed"].append(relative_path)

        except Exception as e:
            summary["errors"].append(f"{relative_path}: {str(e)}")

    return summary