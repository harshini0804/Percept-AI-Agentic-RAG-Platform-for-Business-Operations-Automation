"""
Tests for the scheduled ingestion job in app.main (Section 6.3).
Tests the job's logic function directly rather than waiting on a
real APScheduler timer, which would be slow and flaky in a test
suite — the scheduling mechanism itself (interval, job registration)
is a thin, well-tested third-party library (APScheduler), not
something this project needs to re-verify.
"""

from app.main import run_scheduled_ingestion, VERTICAL_SOURCE_TYPES


def test_run_scheduled_ingestion_calls_ingest_for_every_real_vertical(monkeypatch):
    calls = []

    def fake_ingest(vertical, source_type):
        calls.append((vertical, source_type))
        return {"processed": [], "skipped": [], "errors": []}

    monkeypatch.setattr("app.main.ingest_staging_folder", fake_ingest)

    run_scheduled_ingestion()

    assert set(calls) == set(VERTICAL_SOURCE_TYPES.items())
    assert len(calls) == len(VERTICAL_SOURCE_TYPES)


def test_run_scheduled_ingestion_one_vertical_failing_does_not_stop_others(monkeypatch, capsys):
    calls = []

    def fake_ingest(vertical, source_type):
        calls.append(vertical)
        if vertical == "post_incident":
            raise RuntimeError("simulated failure")
        return {"processed": [], "skipped": [], "errors": []}

    monkeypatch.setattr("app.main.ingest_staging_folder", fake_ingest)

    run_scheduled_ingestion()

    # Every vertical should still have been attempted, even though
    # one raised an exception partway through.
    assert set(calls) == set(VERTICAL_SOURCE_TYPES.keys())

    captured = capsys.readouterr()
    assert "ERROR for vertical 'post_incident'" in captured.out


def test_run_scheduled_ingestion_logs_when_files_were_processed(monkeypatch, capsys):
    def fake_ingest(vertical, source_type):
        if vertical == "post_incident":
            return {"processed": ["file1.txt"], "skipped": [], "errors": []}
        return {"processed": [], "skipped": ["unchanged.txt"], "errors": []}

    monkeypatch.setattr("app.main.ingest_staging_folder", fake_ingest)

    run_scheduled_ingestion()

    captured = capsys.readouterr()
    assert "post_incident" in captured.out
    assert "file1.txt" in captured.out