"""
Post-Incident Knowledge Synthesis (Vertical 1) — local seed data
loader, wired into backend/seed.py.

Mirrors the EXACT runtime persistence path from graph.py:
  1. extract_text() for file reading (handles .txt/.pdf/.docx)
  2. _parse_header_metadata() for structured header extraction
  3. _content_hash() for deduplication
  4. INSERT INTO incidents for relational data
  5. section_chunker() for section-aware chunk boundaries
  6. upsert_embedding() per chunk with source_id = incident_id

This ensures seeded data has identical chunk boundaries and
source_id linkage to data ingested via real submissions through
the graph — the KB can't distinguish seeded from live-submitted
postmortems, which is the point.

Other verticals follow the same pattern:
  - V2 internal_mobility: seed_local.py populates employees +
    upsert_embedding with source_id
  - V3 contract_tracking: seed_local.py runs the real extraction
    pipeline
  - V4 meeting_action_items: seed_local.py runs Trigger 1
"""

from pathlib import Path

from app.core.db import get_connection
from app.core.embeddings import upsert_embedding
from app.core.ingestion import extract_text

from app.verticals.post_incident.chunker import section_chunker
from app.verticals.post_incident.graph import _parse_header_metadata, _content_hash

SEED_DATA_ROOT = Path(__file__).resolve().parents[3] / "seed_data" / "post_incident"


def _insert_incident_if_new(metadata: dict, content_hash: str) -> str | None:
    """Inserts a new incident row if one with this content_hash doesn't
    already exist. Returns the incident ID (new or existing), or None
    on error."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Check for existing
            cur.execute(
                "SELECT id FROM incidents WHERE content_hash = %s LIMIT 1;",
                (content_hash,),
            )
            existing = cur.fetchone()
            if existing:
                return str(existing["id"])

            # Insert new
            cur.execute(
                """
                INSERT INTO incidents (title, root_cause_tag, service, date, doc_id, content_hash)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    metadata["title"] or "Untitled Incident",
                    metadata["root_cause_tag"] or None,
                    metadata["service"] or None,
                    metadata["date"] or None,
                    None,  # no doc_id for seeded files
                    content_hash,
                ),
            )
            incident_id = str(cur.fetchone()["id"])
        conn.commit()
        return incident_id
    finally:
        conn.close()


def seed_post_incident() -> None:
    """Seeds the post-incident KB using the same persistence path as
    graph.py's runtime: parse header → insert incident → section_chunker
    → upsert_embedding with source_id."""

    if not SEED_DATA_ROOT.exists():
        print(f"  No seed data found at {SEED_DATA_ROOT}, skipping.")
        return

    new_incidents = 0
    total_chunks = 0
    skipped = 0

    for file_path in sorted(SEED_DATA_ROOT.iterdir()):
        if not file_path.is_file() or file_path.name.startswith("."):
            continue

        text = extract_text(file_path)
        metadata = _parse_header_metadata(text)
        c_hash = _content_hash(text)

        # Insert or retrieve incident
        incident_id = _insert_incident_if_new(metadata, c_hash)
        if incident_id is None:
            print(f"  WARN: failed to insert/find incident for {file_path.name}")
            continue

        # Check if embeddings already exist for this incident (skip if re-run)
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM embeddings WHERE source_id = %s AND vertical = 'post_incident';",
                    (incident_id,),
                )
                existing_count = cur.fetchone()["count"]
        finally:
            conn.close()

        if existing_count > 0:
            skipped += 1
            continue

        # Section-aware chunking (same as graph.py runtime)
        chunks = section_chunker(text)
        for chunk in chunks:
            upsert_embedding(
                vertical="post_incident",
                source_type="postmortem",
                chunk_text=chunk,
                source_id=incident_id,
                metadata={
                    "service": metadata["service"],
                    "severity": metadata["severity"],
                    "title": metadata["title"],
                },
            )
            total_chunks += 1

        new_incidents += 1

    print(f"  Processed {new_incidents} new incident(s), {total_chunks} chunks embedded, {skipped} skipped (already seeded).")
