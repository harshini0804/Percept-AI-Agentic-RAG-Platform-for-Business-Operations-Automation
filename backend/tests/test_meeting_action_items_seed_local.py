"""
Tests for app.verticals.meeting_action_items.seed_local (Vertical 4,
PR 4).

parse_owner_activity_file and _assign_realistic_deadlines are pure/
DB-only and fully testable. _seed_owner_activity is tested with
upsert_embedding itself mocked (verifying it's called correctly,
without needing a real embedding model). _seed_transcripts is tested
with run_meeting_action_items mocked (verifying the orchestration —
meeting_id/transcript-index extraction from actions_taken — without
needing a real Groq key or embedding model at all).

The actual END-TO-END seeding (real LLM extraction quality on the
committed transcripts, real embeddings) can only be verified by
actually running `python seed.py` in an environment with a real
GROQ_API_KEY and network access — not reproducible in this test
suite, same category as every other real-LLM-dependent piece.
"""

import pytest
from datetime import date, timedelta
from app.core.db import get_connection
from app.schemas.agent_contract import AgentRunOutput, ActionTaken
import app.verticals.meeting_action_items.seed_local as seed_local
from app.verticals.meeting_action_items.seed_local import (
    parse_owner_activity_file,
    _assign_realistic_deadlines,
    _seed_owner_activity,
    _seed_transcripts,
    DEADLINE_OFFSET_PATTERN_DAYS,
)


def _create_meeting() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO meetings (meeting_date) VALUES (now()) RETURNING id;")
            meeting_id = str(cur.fetchone()["id"])
        conn.commit()
        return meeting_id
    finally:
        conn.close()


def _insert_action_item(meeting_id: str, owner: str = "alex") -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO action_items (meeting_id, description, owner) "
                "VALUES (%s, %s, %s) RETURNING id;",
                (meeting_id, "Some task", owner),
            )
            item_id = str(cur.fetchone()["id"])
        conn.commit()
        return item_id
    finally:
        conn.close()


def _fetch_deadline(action_item_id: str):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT deadline FROM action_items WHERE id = %s;", (action_item_id,))
            return cur.fetchone()["deadline"]
    finally:
        conn.close()


# ---------------------------------------------------------------
# parse_owner_activity_file (pure, no dependencies)
# ---------------------------------------------------------------

def test_parse_owner_activity_file_extracts_owner_and_content(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("owner: alex\n---\nAlex did some work.")

    owner, content = parse_owner_activity_file(file_path)

    assert owner == "alex"
    assert content == "Alex did some work."


def test_parse_owner_activity_file_lowercases_owner(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("owner: Alex\n---\ncontent")

    owner, _ = parse_owner_activity_file(file_path)
    assert owner == "alex"


def test_parse_owner_activity_file_raises_without_owner_header(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("no header here\n---\ncontent")

    with pytest.raises(ValueError, match="missing 'owner:"):
        parse_owner_activity_file(file_path)


# ---------------------------------------------------------------
# _assign_realistic_deadlines (pure DB, no embedding model needed)
# ---------------------------------------------------------------

def test_assign_realistic_deadlines_uses_the_offset_pattern():
    meeting_id = _create_meeting()
    action_item_id = _insert_action_item(meeting_id)

    _assign_realistic_deadlines({meeting_id: 0})

    expected = date.today() + timedelta(days=DEADLINE_OFFSET_PATTERN_DAYS[0])
    assert _fetch_deadline(action_item_id) == expected


def test_assign_realistic_deadlines_gives_different_meetings_different_dates():
    meeting_a = _create_meeting()
    meeting_b = _create_meeting()
    item_a = _insert_action_item(meeting_a)
    item_b = _insert_action_item(meeting_b)

    _assign_realistic_deadlines({meeting_a: 0, meeting_b: 2})

    deadline_a = _fetch_deadline(item_a)
    deadline_b = _fetch_deadline(item_b)
    assert deadline_a != deadline_b
    assert deadline_a == date.today() + timedelta(days=DEADLINE_OFFSET_PATTERN_DAYS[0])
    assert deadline_b == date.today() + timedelta(days=DEADLINE_OFFSET_PATTERN_DAYS[2])


def test_assign_realistic_deadlines_includes_at_least_one_overdue_and_one_future():
    """
    Confirms the pattern itself genuinely mixes overdue and future
    dates — the actual point of this redesign (replacing the old
    "force everything to yesterday" approach).
    """
    assert any(offset < 0 for offset in DEADLINE_OFFSET_PATTERN_DAYS)
    assert any(offset > 0 for offset in DEADLINE_OFFSET_PATTERN_DAYS)


def test_assign_realistic_deadlines_cycles_via_modulo_for_extra_transcripts():
    """
    If more transcripts exist than pattern entries, indices wrap
    around rather than raising an IndexError — keeps working
    automatically as seed data scales up.
    """
    meeting_id = _create_meeting()
    action_item_id = _insert_action_item(meeting_id)

    high_index = len(DEADLINE_OFFSET_PATTERN_DAYS) + 1  # wraps to index 1
    _assign_realistic_deadlines({meeting_id: high_index})

    expected_offset = DEADLINE_OFFSET_PATTERN_DAYS[high_index % len(DEADLINE_OFFSET_PATTERN_DAYS)]
    assert _fetch_deadline(action_item_id) == date.today() + timedelta(days=expected_offset)


def test_assign_realistic_deadlines_noop_for_empty_dict():
    meeting_id = _create_meeting()
    action_item_id = _insert_action_item(meeting_id)

    _assign_realistic_deadlines({})

    assert _fetch_deadline(action_item_id) is None


# ---------------------------------------------------------------
# _seed_owner_activity (upsert_embedding itself mocked — no real
# embedding model needed to verify this function's own logic)
# ---------------------------------------------------------------

def test_seed_owner_activity_calls_upsert_embedding_per_file(monkeypatch, tmp_path):
    activity_dir = tmp_path / "owner_activity"
    activity_dir.mkdir()
    (activity_dir / "one.txt").write_text("owner: alex\n---\nAlex's activity.")
    (activity_dir / "two.txt").write_text("owner: priya\n---\nPriya's activity.")

    monkeypatch.setattr(seed_local, "OWNER_ACTIVITY_DIR", activity_dir)

    calls = []
    monkeypatch.setattr(
        seed_local,
        "upsert_embedding",
        lambda **kwargs: calls.append(kwargs),
    )

    count = _seed_owner_activity()

    assert count == 2
    assert len(calls) == 2
    owners = {c["metadata"]["owner"] for c in calls}
    assert owners == {"alex", "priya"}
    for c in calls:
        assert c["vertical"] == "meeting_action_items"
        assert c["source_type"] == "owner_activity"
        assert c["metadata"]["date"] > date.today().isoformat()


def test_seed_owner_activity_returns_zero_for_missing_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(seed_local, "OWNER_ACTIVITY_DIR", tmp_path / "does_not_exist")
    assert _seed_owner_activity() == 0


# ---------------------------------------------------------------
# _seed_transcripts (run_meeting_action_items itself mocked — no
# real Groq key or embedding model needed to verify this function's
# own orchestration logic)
# ---------------------------------------------------------------

def test_seed_transcripts_maps_meeting_id_to_transcript_index(monkeypatch, tmp_path):
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    (transcripts_dir / "t1.txt").write_text("a fake transcript")

    monkeypatch.setattr(seed_local, "TRANSCRIPTS_DIR", transcripts_dir)

    meeting_id = _create_meeting()
    real_item_id = _insert_action_item(meeting_id)

    fake_output = AgentRunOutput(
        run_id="fake-run-id",
        status="completed",
        confidence=1.0,
        actions_taken=[
            ActionTaken(action_name="create_action_item", target_id=real_item_id, detail={})
        ],
        escalated=False,
    )
    monkeypatch.setattr(seed_local, "run_meeting_action_items", lambda agent_input: fake_output)

    result = _seed_transcripts()

    assert result == {meeting_id: 0}


def test_seed_transcripts_assigns_increasing_index_per_file(monkeypatch, tmp_path):
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    (transcripts_dir / "t1.txt").write_text("first")
    (transcripts_dir / "t2.txt").write_text("second")

    monkeypatch.setattr(seed_local, "TRANSCRIPTS_DIR", transcripts_dir)

    meeting_one = _create_meeting()
    item_one = _insert_action_item(meeting_one)
    meeting_two = _create_meeting()
    item_two = _insert_action_item(meeting_two)

    outputs = iter([
        AgentRunOutput(
            run_id="r1", status="completed", confidence=1.0, escalated=False,
            actions_taken=[ActionTaken(action_name="create_action_item", target_id=item_one, detail={})],
        ),
        AgentRunOutput(
            run_id="r2", status="completed", confidence=1.0, escalated=False,
            actions_taken=[ActionTaken(action_name="create_action_item", target_id=item_two, detail={})],
        ),
    ])
    monkeypatch.setattr(seed_local, "run_meeting_action_items", lambda agent_input: next(outputs))

    result = _seed_transcripts()

    assert result == {meeting_one: 0, meeting_two: 1}


def test_seed_transcripts_handles_zero_extracted_items(monkeypatch, tmp_path):
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    (transcripts_dir / "t1.txt").write_text("a transcript with nothing extractable")

    monkeypatch.setattr(seed_local, "TRANSCRIPTS_DIR", transcripts_dir)

    fake_output = AgentRunOutput(
        run_id="fake-run-id", status="completed", confidence=1.0,
        actions_taken=[], escalated=False,
    )
    monkeypatch.setattr(seed_local, "run_meeting_action_items", lambda agent_input: fake_output)

    assert _seed_transcripts() == {}


def test_seed_transcripts_returns_empty_dict_for_missing_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(seed_local, "TRANSCRIPTS_DIR", tmp_path / "does_not_exist")
    assert _seed_transcripts() == {}


# ---------------------------------------------------------------
# Sanity check on the actual committed seed data (catches an
# accidentally malformed or emptied seed_data folder)
# ---------------------------------------------------------------

def test_committed_transcripts_directory_has_files():
    assert seed_local.TRANSCRIPTS_DIR.exists()
    files = list(seed_local.TRANSCRIPTS_DIR.glob("*.txt"))
    assert len(files) >= 2


def test_committed_owner_activity_files_all_have_valid_owner_header():
    assert seed_local.OWNER_ACTIVITY_DIR.exists()
    files = list(seed_local.OWNER_ACTIVITY_DIR.glob("*.txt"))
    assert len(files) >= 1
    for f in files:
        owner, content = parse_owner_activity_file(f)
        assert owner
        assert content