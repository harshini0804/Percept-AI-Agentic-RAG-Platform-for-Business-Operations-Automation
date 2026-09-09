"""
Tests for app.verticals.contract_tracking.scheduler
(run_scheduled_contract_ingestion) — Section 6.4's exception: for
contract_tracking, scheduled ingestion IS the analysis trigger.

run_contract_tracking_vertical itself is mocked throughout (it's
already covered end to end by test_contract_tracking_graph.py) so
these tests stay focused on the scheduler-specific concerns: file
discovery, hash-based skip logic, dotfile handling, and per-file
error isolation — mirroring test_ingestion.py's own test shapes for
ingest_staging_folder, since this function deliberately parallels it.
"""

import pytest

from app.verticals.contract_tracking.scheduler import run_scheduled_contract_ingestion


def test_reports_error_for_missing_folder(monkeypatch, tmp_path):
    monkeypatch.setattr("app.core.ingestion.STAGING_ROOT", tmp_path)

    summary = run_scheduled_contract_ingestion()

    assert summary["processed"] == []
    assert summary["skipped"] == []
    assert len(summary["errors"]) == 1
    assert "does not exist" in summary["errors"][0]


def test_skips_dotfiles_without_error(monkeypatch, tmp_path):
    vertical_folder = tmp_path / "contract_tracking"
    vertical_folder.mkdir()
    (vertical_folder / ".gitkeep").write_text("")

    monkeypatch.setattr("app.core.ingestion.STAGING_ROOT", tmp_path)

    summary = run_scheduled_contract_ingestion()

    assert summary == {"processed": [], "skipped": [], "errors": []}


def test_processes_new_contract_and_calls_the_full_vertical(monkeypatch, tmp_path):
    vertical_folder = tmp_path / "contract_tracking"
    vertical_folder.mkdir()
    (vertical_folder / "vendor_x.txt").write_text("1. Renewal. This agreement renews annually.")

    monkeypatch.setattr("app.core.ingestion.STAGING_ROOT", tmp_path)

    calls = []

    def fake_run_vertical(agent_input):
        calls.append(agent_input)
        from app.schemas.agent_contract import AgentRunOutput
        return AgentRunOutput(run_id="r1", status="completed", confidence=1.0, escalated=False)

    monkeypatch.setattr(
        "app.verticals.contract_tracking.scheduler.run_contract_tracking_vertical",
        fake_run_vertical,
    )

    summary = run_scheduled_contract_ingestion()

    assert summary["processed"] == ["contract_tracking/vendor_x.txt"]
    assert summary["errors"] == []
    assert len(calls) == 1
    assert calls[0].trigger_type.value == "scheduled_ingestion"
    assert calls[0].input_payload == {"text": "1. Renewal. This agreement renews annually."}


def test_skips_unchanged_file_on_second_run(monkeypatch, tmp_path):
    vertical_folder = tmp_path / "contract_tracking"
    vertical_folder.mkdir()
    (vertical_folder / "vendor_x.txt").write_text("Some contract text.")

    monkeypatch.setattr("app.core.ingestion.STAGING_ROOT", tmp_path)
    monkeypatch.setattr(
        "app.verticals.contract_tracking.scheduler.run_contract_tracking_vertical",
        lambda agent_input: None,
    )

    first = run_scheduled_contract_ingestion()
    assert first["processed"] == ["contract_tracking/vendor_x.txt"]

    second = run_scheduled_contract_ingestion()
    assert second["processed"] == []
    assert second["skipped"] == ["contract_tracking/vendor_x.txt"]


def test_reprocesses_changed_file(monkeypatch, tmp_path):
    vertical_folder = tmp_path / "contract_tracking"
    vertical_folder.mkdir()
    file_path = vertical_folder / "vendor_x.txt"
    file_path.write_text("Original text.")

    monkeypatch.setattr("app.core.ingestion.STAGING_ROOT", tmp_path)
    monkeypatch.setattr(
        "app.verticals.contract_tracking.scheduler.run_contract_tracking_vertical",
        lambda agent_input: None,
    )

    run_scheduled_contract_ingestion()

    file_path.write_text("Changed text — new obligation added.")
    second = run_scheduled_contract_ingestion()

    assert second["processed"] == ["contract_tracking/vendor_x.txt"]
    assert second["skipped"] == []


def test_one_contract_failing_does_not_stop_others(monkeypatch, tmp_path):
    vertical_folder = tmp_path / "contract_tracking"
    vertical_folder.mkdir()
    (vertical_folder / "bad_contract.txt").write_text("Malformed contract.")
    (vertical_folder / "good_contract.txt").write_text("Fine contract.")

    monkeypatch.setattr("app.core.ingestion.STAGING_ROOT", tmp_path)

    def fake_run_vertical(agent_input):
        if "Malformed" in agent_input.input_payload["text"]:
            raise RuntimeError("simulated extraction failure")
        return None

    monkeypatch.setattr(
        "app.verticals.contract_tracking.scheduler.run_contract_tracking_vertical",
        fake_run_vertical,
    )

    summary = run_scheduled_contract_ingestion()

    assert summary["processed"] == ["contract_tracking/good_contract.txt"]
    assert len(summary["errors"]) == 1
    assert "bad_contract.txt" in summary["errors"][0]
    assert "simulated extraction failure" in summary["errors"][0]


def test_failed_contract_is_not_marked_as_synced(monkeypatch, tmp_path):
    """A contract that failed extraction should be retried on the
    NEXT tick, not silently marked as synced — its hash must not be
    recorded on failure."""
    vertical_folder = tmp_path / "contract_tracking"
    vertical_folder.mkdir()
    (vertical_folder / "bad_contract.txt").write_text("Malformed contract.")

    monkeypatch.setattr("app.core.ingestion.STAGING_ROOT", tmp_path)
    monkeypatch.setattr(
        "app.verticals.contract_tracking.scheduler.run_contract_tracking_vertical",
        lambda agent_input: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    first = run_scheduled_contract_ingestion()
    assert len(first["errors"]) == 1

    second = run_scheduled_contract_ingestion()
    # Still shows up as processed-attempt (still fails), NOT skipped —
    # proving its hash was never recorded as synced after the failure.
    assert second["skipped"] == []
    assert len(second["errors"]) == 1
