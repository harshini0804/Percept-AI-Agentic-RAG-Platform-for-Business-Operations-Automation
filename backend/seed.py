"""
Seed script (Section 9.2, point 17) — "copies each vertical's
committed synthetic data into its staging folder and runs the shared
ingestion function once, populating the KB and relational tables for
local development."

Usage (from inside the backend container, or any environment with
DATABASE_URL and STAGING_ROOT correctly configured):

    python seed.py

Safe to re-run: ingest_staging_folder()'s hash-based change detection
(Section 6.3) means files already seeded and unchanged are skipped,
not re-embedded, on subsequent runs.
"""

import shutil
from pathlib import Path

from app.core.ingestion import ingest_staging_folder, STAGING_ROOT

# Synthetic data committed to the repo under backend/seed_data/,
# copied into each vertical's staging folder before ingestion. Only
# "dummy" has real seed data right now (Section 6.1's synthetic
# corpora for the four real verticals get added by each vertical
# owner once their own vertical is built).
SEED_DATA_ROOT = Path(__file__).parent / "seed_data"

VERTICALS_TO_SEED = [
    {"vertical": "dummy", "source_type": "postmortem"},
]


def seed_vertical(vertical: str, source_type: str) -> None:
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
    print(f"  Ingested: processed={len(summary['processed'])}, "
          f"skipped={len(summary['skipped'])}, errors={summary['errors']}")


def main():
    print("Seeding knowledge base from committed synthetic data...")
    for entry in VERTICALS_TO_SEED:
        print(f"\n{entry['vertical']}:")
        seed_vertical(entry["vertical"], entry["source_type"])
    print("\nSeeding complete.")


if __name__ == "__main__":
    main()