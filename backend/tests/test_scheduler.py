"""
Tests for the scheduled ingestion job in app.main (Section 6.3).
Tests the job's logic function directly rather than waiting on a
real APScheduler timer, which would be slow and flaky in a test
suite — the scheduling mechanism itself (interval, job registration)
is a thin, well-tested third-party library (APScheduler), not
something this project needs to re-verify.

"contract_tracking" is deliberately excluded from the generic
ingest_staging_folder loop (Section 6.4's exception — see
app.verticals.contract_tracking.scheduler for why) and routed to its
own function instead, so it's tested separately below rather than
folded into VERTICAL_SOURCE_TYPES-driven assertions.
"""

from app.main import run_scheduled_ingestion, VERTICAL_SOURCE_TYPES

_GENERIC_VERTICALS = {
    v: s for v, s in VERTICAL_SOURCE_TYPES.items() if v != "contract_tracking"
}


def test_run_scheduled_ingestion_calls_ingest_for_every_generic_vertical(monkeypatch):
    calls = []

    def fake_ingest(vertical, source_type):
        calls.append((vertical, source_type))
        return {"processed": [], "skipped": [], "errors": []}

    monkeypatch.setattr("app.main.ingest_staging_folder", fake_ingest)
    monkeypatch.setattr(
        "app.main.run_scheduled_contract_ingestion",
        lambda: {"processed": [], "skipped": [], "errors": []},
    )

    run_scheduled_ingestion()

    assert set(calls) == set(_GENERIC_VERTICALS.items())
    assert len(calls) == len(_GENERIC_VERTICALS)
    # contract_tracking must NOT go through the generic path.
    assert "contract_tracking" not in [c[0] for c in calls]


def test_run_scheduled_ingestion_routes_contract_tracking_to_its_own_function(monkeypatch):
    generic_calls = []
    contract_calls = []

    monkeypatch.setattr(
        "app.main.ingest_staging_folder",
        lambda vertical, source_type: generic_calls.append(vertical)
        or {"processed": [], "skipped": [], "errors": []},
    )

    def fake_contract_ingestion():
        contract_calls.append(True)
        return {"processed": [], "skipped": [], "errors": []}

    monkeypatch.setattr("app.main.run_scheduled_contract_ingestion", fake_contract_ingestion)

    run_scheduled_ingestion()

    assert "contract_tracking" not in generic_calls
    assert len(contract_calls) == 1


def test_run_scheduled_ingestion_one_generic_vertical_failing_does_not_stop_others(monkeypatch, capsys):
    calls = []

    def fake_ingest(vertical, source_type):
        calls.append(vertical)
        if vertical == "post_incident":
            raise RuntimeError("simulated failure")
        return {"processed": [], "skipped": [], "errors": []}

    monkeypatch.setattr("app.main.ingest_staging_folder", fake_ingest)
    monkeypatch.setattr(
        "app.main.run_scheduled_contract_ingestion",
        lambda: {"processed": [], "skipped": [], "errors": []},
    )

    run_scheduled_ingestion()

    # Every GENERIC vertical should still have been attempted, even
    # though one raised an exception partway through.
    assert set(calls) == set(_GENERIC_VERTICALS.keys())

    captured = capsys.readouterr()
    assert "ERROR for vertical 'post_incident'" in captured.out


def test_run_scheduled_ingestion_contract_tracking_failure_does_not_stop_generic_verticals(monkeypatch, capsys):
    """The reverse of the above: if contract_tracking's OWN scheduled
    function raises, every generic vertical must still have been
    attempted — the two paths must not be able to take each other
    down."""
    calls = []

    monkeypatch.setattr(
        "app.main.ingest_staging_folder",
        lambda vertical, source_type: calls.append(vertical)
        or {"processed": [], "skipped": [], "errors": []},
    )

    def failing_contract_ingestion():
        raise RuntimeError("simulated contract_tracking failure")

    monkeypatch.setattr("app.main.run_scheduled_contract_ingestion", failing_contract_ingestion)

    run_scheduled_ingestion()

    assert set(calls) == set(_GENERIC_VERTICALS.keys())

    captured = capsys.readouterr()
    assert "ERROR for vertical 'contract_tracking'" in captured.out


def test_run_scheduled_ingestion_logs_when_generic_vertical_files_were_processed(monkeypatch, capsys):
    def fake_ingest(vertical, source_type):
        if vertical == "post_incident":
            return {"processed": ["file1.txt"], "skipped": [], "errors": []}
        return {"processed": [], "skipped": ["unchanged.txt"], "errors": []}

    monkeypatch.setattr("app.main.ingest_staging_folder", fake_ingest)
    monkeypatch.setattr(
        "app.main.run_scheduled_contract_ingestion",
        lambda: {"processed": [], "skipped": [], "errors": []},
    )

    run_scheduled_ingestion()

    captured = capsys.readouterr()
    assert "post_incident" in captured.out
    assert "file1.txt" in captured.out


def test_run_scheduled_ingestion_logs_when_contract_tracking_files_were_processed(monkeypatch, capsys):
    monkeypatch.setattr(
        "app.main.ingest_staging_folder",
        lambda vertical, source_type: {"processed": [], "skipped": [], "errors": []},
    )
    monkeypatch.setattr(
        "app.main.run_scheduled_contract_ingestion",
        lambda: {"processed": ["contract_tracking/vendor_x.pdf"], "skipped": [], "errors": []},
    )

    run_scheduled_ingestion()

    captured = capsys.readouterr()
    assert "contract_tracking" in captured.out
    assert "vendor_x.pdf" in captured.out
