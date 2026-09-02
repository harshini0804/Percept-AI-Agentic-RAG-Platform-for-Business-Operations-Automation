"""
Tests for seed.py (Section 9.2, point 17).
"""

import sys
import pathlib

# seed.py lives at the backend root, not under app/ — add it to the
# path so it can be imported like any other module in this test.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import seed  # noqa: E402


def test_seed_data_dummy_directory_exists_and_has_files():
    """
    Sanity check on the committed synthetic data itself — catches an
    accidentally emptied or misplaced seed_data folder, which
    wouldn't otherwise fail loudly (seed_vertical just skips a
    missing directory rather than erroring).
    """
    dummy_dir = seed.SEED_DATA_ROOT / "dummy"
    assert dummy_dir.exists()
    files = list(dummy_dir.glob("*.txt"))
    assert len(files) >= 3


def test_seed_vertical_copies_files_and_ingests(monkeypatch, tmp_path):
    # Redirect both SEED_DATA_ROOT and STAGING_ROOT to isolated temp
    # dirs, so this test never touches the real seed_data/ or the
    # real staging folder.
    fake_seed_root = tmp_path / "seed_data"
    fake_staging_root = tmp_path / "staging"
    (fake_seed_root / "dummy").mkdir(parents=True)
    (fake_seed_root / "dummy" / "one.txt").write_text("Some postmortem text.")
    (fake_seed_root / "dummy" / "two.txt").write_text("Another postmortem.")

    monkeypatch.setattr(seed, "SEED_DATA_ROOT", fake_seed_root)
    monkeypatch.setattr(seed, "STAGING_ROOT", fake_staging_root)

    ingest_calls = []

    def fake_ingest(vertical, source_type):
        ingest_calls.append((vertical, source_type))
        return {"processed": ["one.txt", "two.txt"], "skipped": [], "errors": []}

    monkeypatch.setattr(seed, "ingest_staging_folder", fake_ingest)

    seed.seed_vertical("dummy", "postmortem")

    copied_files = list((fake_staging_root / "dummy").iterdir())
    assert len(copied_files) == 2
    assert ingest_calls == [("dummy", "postmortem")]


def test_seed_vertical_skips_missing_source_directory(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(seed, "SEED_DATA_ROOT", tmp_path / "nonexistent_seed_data")
    monkeypatch.setattr(seed, "STAGING_ROOT", tmp_path / "staging")

    called = []
    monkeypatch.setattr(
        seed, "ingest_staging_folder", lambda **kwargs: called.append(kwargs)
    )

    seed.seed_vertical("post_incident", "postmortem")

    assert called == []
    captured = capsys.readouterr()
    assert "No seed data found" in captured.out