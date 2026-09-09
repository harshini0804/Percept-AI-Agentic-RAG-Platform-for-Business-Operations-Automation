"""
Meeting Action-Item Enforcement (Vertical 4) — local seed data
loader, wired into backend/seed.py.

Two distinct seeding mechanisms, since this vertical's KB content
isn't produced the same way as the dummy vertical's:

- Transcripts (source_type='action_item'): run through the REAL
  Trigger 1 pipeline (run_meeting_action_items), not raw file
  ingestion — action_items rows and their embeddings only exist as a
  byproduct of real LLM extraction, so seeding must actually invoke
  that pipeline, not just chunk-and-embed raw text. Requires a real
  GROQ_API_KEY, same baseline assumption as normal app operation.
- Owner-activity records (source_type='owner_activity'): no natural
  "run" produces these — they're seeded directly via upsert_embedding.

Two deliberate seeding-only adjustments, needed because this data is
committed as static text but consumed by a live, date-aware system:

1. Owner-activity dates are computed RELATIVE TO SEEDING TIME (not
   read from the files), since Trigger 2's filter requires
   date > item.created_at, and item.created_at is only known once
   seeding actually runs.
2. Seeded action items get a REALISTIC SPREAD of deadlines, not one
   fixed date. Reliably getting a real LLM to extract a consistently
   past deadline from natural transcript language isn't guaranteed —
   so deadlines are assigned programmatically after extraction, based
   on which transcript an item came from (DEADLINE_OFFSET_PATTERN_DAYS
   below). Earlier revision forced EVERY seeded item to the same
   fixed "yesterday" — unrealistic, since a real organization's open
   items would never all share one deadline, and Trigger 2 should
   only ever process a SUBSET of open items on any given day, not all
   of them at once. This ONLY happens during seeding — real
   submissions through the UI keep whatever deadline the LLM actually
   extracts (see graph.py's _validate_deadline).
"""

from datetime import date, timedelta
from pathlib import Path

from app.core.db import get_connection
from app.core.embeddings import upsert_embedding
from app.schemas.agent_contract import AgentRunInput, TriggerType
from app.verticals.meeting_action_items.graph import run_meeting_action_items

SEED_DATA_ROOT = Path(__file__).resolve().parents[3] / "seed_data" / "meeting_action_items"
TRANSCRIPTS_DIR = SEED_DATA_ROOT / "transcripts"
OWNER_ACTIVITY_DIR = SEED_DATA_ROOT / "owner_activity"

# Deadline offsets (in days from seeding day), one per transcript,
# cycled via modulo if there are ever more transcripts than entries
# here (so this keeps working automatically as seed data scales up
# toward the ~10-15 transcripts recommended in kb_data_strategy.md).
# Deliberately mixes already-overdue, due-today, and future dates.
DEADLINE_OFFSET_PATTERN_DAYS = [-3, -1, 0, 5]


def parse_owner_activity_file(file_path: Path) -> tuple[str, str]:
    """
    Parses the simple 'owner: X\\n---\\ncontent' format used by
    owner_activity seed files. Returns (owner, content).
    """
    text = file_path.read_text(encoding="utf-8")
    header, _, content = text.partition("---")
    owner = None
    for line in header.splitlines():
        if line.strip().lower().startswith("owner:"):
            owner = line.split(":", 1)[1].strip().lower()
            break
    if not owner:
        raise ValueError(f"{file_path.name}: missing 'owner: <name>' header line.")
    return owner, content.strip()


def _seed_transcripts() -> dict[str, int]:
    """
    Runs each committed transcript through the real Trigger 1
    pipeline. Returns {meeting_id: transcript_index}, indexed by
    sorted file order — so the deadline-assignment step below can
    give each transcript's items a different point in the deadline
    spread, rather than treating every seeded item identically.
    """
    if not TRANSCRIPTS_DIR.exists():
        print(f"  No transcripts found at {TRANSCRIPTS_DIR}, skipping.")
        return {}

    meeting_id_to_index: dict[str, int] = {}
    index = 0
    for file_path in sorted(TRANSCRIPTS_DIR.iterdir()):
        if not file_path.is_file():
            continue

        text = file_path.read_text(encoding="utf-8")
        agent_input = AgentRunInput(
            vertical="meeting_action_items",
            trigger_type=TriggerType.UPLOAD,
            input_payload={"text": text},
        )
        output = run_meeting_action_items(agent_input)
        print(f"  {file_path.name}: {len(output.actions_taken)} action item(s) extracted")

        item_ids = [a.target_id for a in output.actions_taken if a.target_id]
        if item_ids:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT DISTINCT meeting_id FROM action_items WHERE id = ANY(%s::uuid[]);",
                        (item_ids,),
                    )
                    for r in cur.fetchall():
                        meeting_id_to_index[str(r["meeting_id"])] = index
            finally:
                conn.close()

        index += 1

    return meeting_id_to_index


def _assign_realistic_deadlines(meeting_id_to_index: dict[str, int]) -> None:
    """
    Seeding-only override — see module docstring. Assigns each
    seeded meeting's items a deadline based on DEADLINE_OFFSET_PATTERN_DAYS,
    cycled by the meeting's transcript index, giving a realistic mix
    of overdue / due-today / future deadlines instead of forcing
    every item to the same fixed date.
    """
    if not meeting_id_to_index:
        return

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for meeting_id, index in meeting_id_to_index.items():
                offset_days = DEADLINE_OFFSET_PATTERN_DAYS[index % len(DEADLINE_OFFSET_PATTERN_DAYS)]
                deadline = (date.today() + timedelta(days=offset_days)).isoformat()
                cur.execute(
                    "UPDATE action_items SET deadline = %s WHERE meeting_id = %s;",
                    (deadline, meeting_id),
                )
        conn.commit()
    finally:
        conn.close()


def _seed_owner_activity() -> int:
    if not OWNER_ACTIVITY_DIR.exists():
        print(f"  No owner_activity data found at {OWNER_ACTIVITY_DIR}, skipping.")
        return 0

    # Relative to seeding time (see module docstring) — must be after
    # every action item's created_at, which is set to "now" during
    # this same seeding run.
    activity_date = (date.today() + timedelta(days=1)).isoformat()

    count = 0
    for file_path in sorted(OWNER_ACTIVITY_DIR.iterdir()):
        if not file_path.is_file():
            continue
        owner, content = parse_owner_activity_file(file_path)
        upsert_embedding(
            vertical="meeting_action_items",
            source_type="owner_activity",
            chunk_text=content,
            metadata={"owner": owner, "date": activity_date},
        )
        count += 1

    return count


def seed_meeting_action_items() -> None:
    print("  Seeding transcripts through the real Trigger 1 pipeline...")
    meeting_id_to_index = _seed_transcripts()

    print("  Assigning a realistic deadline spread (seeding-only override)...")
    _assign_realistic_deadlines(meeting_id_to_index)

    print("  Seeding owner-activity records...")
    activity_count = _seed_owner_activity()
    print(f"  Seeded {activity_count} owner-activity record(s).")