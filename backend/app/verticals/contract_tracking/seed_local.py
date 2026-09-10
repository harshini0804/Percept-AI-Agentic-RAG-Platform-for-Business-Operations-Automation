"""
Contract Obligation & Renewal Tracking (Vertical 3) — local seed data
loader, wired into backend/seed.py.

Unlike "dummy" (raw chunk-and-embed via the generic staging-folder
path), this vertical's KB content is a byproduct of the REAL
extraction pipeline (Section 8.3, Section 6.4: scheduled ingestion
IS the analysis trigger) — clause-level chunks with
{clause_number, title} metadata only exist because
run_contract_tracking_vertical() produced them, not because a
generic chunker split some text. So seeding copies the committed
synthetic contracts into the staging folder and then runs the SAME
function the real scheduler uses (run_scheduled_contract_ingestion),
rather than a separate bespoke seeding path — there is no reason to
duplicate that logic, since it already does exactly "copy new/changed
files, then analyze them."

Requires a real GROQ_API_KEY, same baseline assumption as
meeting_action_items' and internal_mobility's seeding (Section 8.3's
extraction is a real LLM call, not something that can be seeded
without one).
"""

import shutil
from pathlib import Path

from app.core.ingestion import STAGING_ROOT
from app.verticals.contract_tracking.scheduler import run_scheduled_contract_ingestion

SEED_DATA_ROOT = Path(__file__).resolve().parents[3] / "seed_data" / "contract_tracking"


def seed_contract_tracking() -> None:
    source_dir = SEED_DATA_ROOT
    target_dir = STAGING_ROOT / "contract_tracking"

    if not source_dir.exists():
        print(f"  No seed data found for 'contract_tracking' at {source_dir}, skipping.")
        return

    target_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for file in sorted(source_dir.iterdir()):
        if file.is_file():
            shutil.copy(file, target_dir / file.name)
            copied += 1
    print(f"  Copied {copied} file(s) into {target_dir}")

    print("  Running the real extraction pipeline (Section 6.4: ingestion IS the trigger)...")
    summary = run_scheduled_contract_ingestion()
    print(f"  Processed: processed={len(summary['processed'])}, "
          f"skipped={len(summary['skipped'])}, errors={summary['errors']}")
