"""
Tests for seed.py's contract_tracking seeding
(app.verticals.contract_tracking.seed_local.seed_contract_tracking).

Two layers, matching the internal_mobility/meeting_action_items seed
test convention:
  1. Pure file sanity checks — no DB, catches an accidentally emptied
     or missing synthetic contract file.
  2. Functional check that seed_contract_tracking() copies files into
     the staging folder and invokes the real extraction pipeline
     (run_scheduled_contract_ingestion is mocked here — it's already
     covered end to end by test_contract_tracking_scheduler.py and
     test_contract_tracking_graph.py, so re-running the real LLM
     pipeline in this test would be slow and redundant).
"""

from pathlib import Path

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import seed  # noqa: E402
from app.verticals.contract_tracking.seed_local import seed_contract_tracking

CONTRACT_SEED_DIR = seed.SEED_DATA_ROOT / "contract_tracking"

# The five deliberately distinct scenarios (Section 12.2: "specific
# scenarios to plant in the synthetic dataset to guarantee a good
# demo case") — a clean confident obligation, a vague/ambiguous one,
# an explicit cross-reference, unusual wording, and no obligations
# at all.
EXPECTED_FILES = {
    "vendor_northbridge_storage.txt",
    "msa_quilyx_analytics.txt",
    "license_halcyon_systems.txt",
    "supply_ferrovax_components.txt",
    "nda_vireo_labs.txt",
}


def test_contract_tracking_seed_dir_exists_and_has_expected_files():
    assert CONTRACT_SEED_DIR.exists()
    actual_files = {f.name for f in CONTRACT_SEED_DIR.iterdir() if f.is_file()}
    assert EXPECTED_FILES <= actual_files


def test_every_seed_contract_is_non_trivial_text():
    for filename in EXPECTED_FILES:
        text = (CONTRACT_SEED_DIR / filename).read_text(encoding="utf-8")
        # A real contract has multiple numbered clauses, not just a
        # title line — guards against an accidentally truncated file.
        assert len(text) > 200
        assert text.count("\n\n") >= 2


def test_cross_reference_contract_actually_contains_a_cross_reference():
    """Sanity-checks the ONE property this specific file exists to
    exercise — that it genuinely references another numbered
    section, not just that the file is non-empty."""
    text = (CONTRACT_SEED_DIR / "license_halcyon_systems.txt").read_text(encoding="utf-8")
    assert "Section 1" in text


def test_unusual_wording_contract_avoids_standard_termination_phrasing():
    """Sanity-checks this file uses atypical language rather than the
    standard 'terminate upon written notice' phrasing used elsewhere
    in the corpus — otherwise it wouldn't actually exercise the
    unusual_wording path differently from the other contracts."""
    text = (CONTRACT_SEED_DIR / "supply_ferrovax_components.txt").read_text(encoding="utf-8")
    assert "sunset" in text.lower()
    assert "evergreen" in text.lower()


def test_no_obligation_contract_has_no_date_bound_language():
    """Sanity-checks the deliberately obligation-free NDA doesn't
    accidentally contain renewal/notice/deadline language that would
    defeat its purpose as the empty-extraction test case."""
    text = (CONTRACT_SEED_DIR / "nda_vireo_labs.txt").read_text(encoding="utf-8")
    lowered = text.lower()
    for term in ("renew", "notice period", "deadline", "expir"):
        assert term not in lowered


def test_seed_contract_tracking_copies_files_and_runs_pipeline(monkeypatch, tmp_path):
    monkeypatch.setattr("app.verticals.contract_tracking.seed_local.STAGING_ROOT", tmp_path)

    calls = []

    def fake_run_scheduled_contract_ingestion():
        calls.append(True)
        staged_dir = tmp_path / "contract_tracking"
        staged_files = {f.name for f in staged_dir.iterdir()} if staged_dir.exists() else set()
        return {"processed": sorted(staged_files), "skipped": [], "errors": []}

    monkeypatch.setattr(
        "app.verticals.contract_tracking.seed_local.run_scheduled_contract_ingestion",
        fake_run_scheduled_contract_ingestion,
    )

    seed_contract_tracking()

    assert len(calls) == 1
    staged_dir = tmp_path / "contract_tracking"
    assert staged_dir.exists()
    staged_files = {f.name for f in staged_dir.iterdir() if f.is_file()}
    assert EXPECTED_FILES <= staged_files


def test_seed_contract_tracking_skips_gracefully_when_source_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.verticals.contract_tracking.seed_local.SEED_DATA_ROOT", tmp_path / "nonexistent_seed_data"
    )
    monkeypatch.setattr("app.verticals.contract_tracking.seed_local.STAGING_ROOT", tmp_path)

    called = []
    monkeypatch.setattr(
        "app.verticals.contract_tracking.seed_local.run_scheduled_contract_ingestion",
        lambda: called.append(True),
    )

    seed_contract_tracking()  # must not raise

    assert called == []
