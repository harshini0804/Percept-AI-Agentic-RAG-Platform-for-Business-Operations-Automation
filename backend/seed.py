"""
Database Seed Script (Section 9.2 Step 17)

Populates the PostgreSQL relational tables and pgvector embeddings from
committed synthetic data across verticals.

Usage:
    python seed.py
    python seed.py --vertical all
    python seed.py --vertical dummy
    python seed.py --vertical post_incident
"""

import os
import sys
import shutil
import argparse
from pathlib import Path

# Ensure TF warnings and Protobuf collisions are suppressed
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("USE_TORCH", "1")

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

from app.core.db import get_connection
from app.core.embeddings import upsert_embedding
from app.core.ingestion import (
    ingest_staging_folder,
    STAGING_ROOT,
    _compute_hash,
    _update_sync_state,
)
from app.verticals.post_incident.chunker import section_chunker, _extract_header

SEED_DATA_ROOT = Path(__file__).parent / "seed_data"

VERTICALS_TO_SEED = [
    {"vertical": "dummy", "source_type": "postmortem"},
]


def get_staging_root() -> Path:
    env_root = os.getenv("STAGING_ROOT")
    if env_root and Path(env_root).exists():
        return Path(env_root)

    candidates = [
        Path("/app/uploads/staging"),
        backend_dir / ".." / "uploads" / "staging",
        backend_dir / "uploads" / "staging",
    ]
    for c in candidates:
        if c.resolve().exists():
            return c.resolve()

    default_path = (backend_dir / ".." / "uploads" / "staging").resolve()
    default_path.mkdir(parents=True, exist_ok=True)
    return default_path


def seed_dummy_vertical(vertical: str, source_type: str) -> None:
    source_dir = SEED_DATA_ROOT / vertical
    target_dir = STAGING_ROOT / vertical

    if not source_dir.exists():
        print(f"  No seed data found for '{vertical}' at {source_dir}, skipping.")
        return

    target_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for file in sorted(source_dir.iterdir()):
        if file.is_file():
            shutil.copy(file, target_dir / file.name)
            copied += 1
    print(f"  Copied {copied} file(s) into {target_dir}")

    summary = ingest_staging_folder(vertical=vertical, source_type=source_type)
    print(
        f"  Ingested: processed={len(summary['processed'])}, "
        f"skipped={len(summary['skipped'])}, errors={summary['errors']}"
    )


def parse_postmortem_metadata(text: str, filename: str) -> dict:
    """Extracts structured fields from postmortem header."""
    header, _ = _extract_header(text)

    title = filename.replace(".txt", "").replace("-", " ").title()
    service = "unknown"
    date_val = None
    severity = "P2"
    root_cause_tag = filename.replace(".txt", "")

    # Clean up root_cause_tag if prefixed with date (e.g. 2024-03-15-payments-db-pool-exhaustion)
    parts = root_cause_tag.split("-")
    if len(parts) > 3 and len(parts[0]) == 4:
        root_cause_tag = "_".join(parts[3:])
    else:
        root_cause_tag = root_cause_tag.replace("-", "_")

    for line in header.splitlines():
        line_clean = line.strip()
        if line_clean.startswith("# Incident:"):
            title = line_clean.replace("# Incident:", "").strip()
        elif line_clean.lower().startswith("service:"):
            service = line_clean.split(":", 1)[1].strip()
        elif line_clean.lower().startswith("date:"):
            date_val = line_clean.split(":", 1)[1].strip()
        elif line_clean.lower().startswith("severity:"):
            severity = line_clean.split(":", 1)[1].strip()

    return {
        "title": title,
        "service": service,
        "date": date_val,
        "severity": severity,
        "root_cause_tag": root_cause_tag,
    }


def seed_post_incident(staging_root: Path) -> dict:
    """Seeds Vertical 1 postmortems into 'incidents' and 'embeddings'."""
    folder = staging_root / "post_incident"
    summary = {"inserted_incidents": 0, "embedded_chunks": 0, "errors": []}

    if not folder.exists():
        summary["errors"].append(f"Folder not found: {folder}")
        return summary

    files = sorted(f for f in folder.iterdir() if f.is_file() and f.suffix == ".txt")
    print(f"[*] Found {len(files)} postmortem files in {folder}")

    conn = get_connection()
    try:
        for file_path in files:
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                meta = parse_postmortem_metadata(text, file_path.name)
                rel_path = f"post_incident/{file_path.name}"
                content_hash = _compute_hash(file_path)

                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM incidents WHERE title = %s LIMIT 1;",
                        (meta["title"],),
                    )
                    existing = cur.fetchone()
                    if existing:
                        incident_id = existing["id"]
                    else:
                        cur.execute(
                            """
                            INSERT INTO incidents (title, root_cause_tag, service, date)
                            VALUES (%s, %s, %s, %s)
                            RETURNING id;
                            """,
                            (
                                meta["title"],
                                meta["root_cause_tag"],
                                meta["service"],
                                meta["date"],
                            ),
                        )
                        incident_id = cur.fetchone()["id"]
                        summary["inserted_incidents"] += 1

                conn.commit()

                # Chunk document
                chunks = section_chunker(text)
                for chunk in chunks:
                    upsert_embedding(
                        vertical="post_incident",
                        source_type="postmortem",
                        chunk_text=chunk,
                        source_id=str(incident_id),
                        metadata={
                            "file_path": rel_path,
                            "service": meta["service"],
                            "title": meta["title"],
                            "root_cause_tag": meta["root_cause_tag"],
                        },
                    )
                    summary["embedded_chunks"] += 1

                _update_sync_state("post_incident", rel_path, content_hash)
                print(f"  + Seeded: {meta['title']} ({len(chunks)} chunks, service={meta['service']})")

            except Exception as e:
                summary["errors"].append(f"{file_path.name}: {e}")
                print(f"  ! Error seeding {file_path.name}: {e}")

    finally:
        conn.close()

    return summary


def main():
    parser = argparse.ArgumentParser(description="Seed the RAG platform knowledge base.")
    parser.add_argument(
        "--vertical",
        default="all",
        choices=["all", "dummy", "post_incident"],
        help="Vertical to seed (default: all)",
    )
    args = parser.parse_args()

    staging_root = get_staging_root()
    print(f"=== Seeding Knowledge Base (Staging Root: {staging_root}) ===\n")

    if args.vertical in ("all", "dummy"):
        print("[Vertical: Dummy]")
        for entry in VERTICALS_TO_SEED:
            seed_dummy_vertical(entry["vertical"], entry["source_type"])

    if args.vertical in ("all", "post_incident"):
        print("\n[Vertical 1: Post-Incident Knowledge Synthesis]")
        res = seed_post_incident(staging_root)
        print(f"Summary: {res['inserted_incidents']} incidents inserted, {res['embedded_chunks']} chunks embedded.")
        if res["errors"]:
            print(f"Errors: {res['errors']}")

    print("\n=== Seeding Finished ===")


if __name__ == "__main__":
    main()
