"""
End-to-end integration tests for the contract_tracking vertical
(Vertical 3, Section 8.3). Mirrors test_dummy_vertical_integration.py's
pattern: real database, real embeddings, only the LLM is mocked.

Two distinct call_llm call sites need mocking here (unlike dummy,
which has one): chunking.call_llm (clause splitting) and
graph.call_llm (per-clause extraction) — both are patched per test.
"""

import json

import pytest

from app.core.db import get_connection
from app.schemas.agent_contract import AgentRunInput, TriggerType
from app.verticals.contract_tracking.graph import (run_contract_tracking_vertical, ACTION_THRESHOLD, _resolve_obligation_date)


def _chunk_response(*clauses: tuple[str, str, str], effective_date: str | None = None):
    """clauses: (clause_number, title, text) tuples.
    Returns the new object shape {effective_date, clauses} that
    split_contract_into_clauses now expects from the LLM.
    """
    payload = {
        "effective_date": effective_date,
        "clauses": [
            {"clause_number": c, "title": t, "text": x} for c, t, x in clauses
        ],
    }
    return lambda **kwargs: {"content": json.dumps(payload), "tool_calls": []}


def _extraction_sequence(*responses: dict):
    """Returns a stateful fake call_llm that yields one response dict
    per call, in order — used for graph.call_llm, which is called
    once per clause (or twice, if a cross-reference re-extraction
    happens)."""
    calls = iter(responses)

    def _fake(**kwargs):
        parsed = next(calls)
        return {"content": json.dumps(parsed), "tool_calls": []}

    return _fake


def test_single_confident_obligation_creates_reminder(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _chunk_response(("1", "Renewal", "This agreement renews annually unless notice is given.")),
    )
    _extr_1 = iter([{"has_obligation": True, "description": "Give notice to avoid auto-renewal",
        "raw_date_or_condition": "annually", "references_other_section": "",
        "unusual_wording": False, "confidence": 0.95}])
    _date_1 = iter([{"obligation_date": "2027-01-01", "date_status": "computed",
        "reasoning": "annual renewal"}])
    def _route_1(**kwargs):
        s = (kwargs.get("messages") or [{}])[0].get("content", "")
        return {"content": json.dumps(next(_date_1) if "date analyst" in s else next(_extr_1)), "tool_calls": []}
    monkeypatch.setattr("app.verticals.contract_tracking.graph.call_llm", _route_1)

    agent_input = AgentRunInput(
        vertical="contract_tracking",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "1. Renewal. This agreement renews annually unless notice is given."},
    )
    output = run_contract_tracking_vertical(agent_input)

    assert output.status == "completed"
    assert output.escalated is False
    assert len(output.actions_taken) == 1
    assert output.actions_taken[0].action_name == "create_calendar_reminder"

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reminder_created, description FROM obligations WHERE description = %s;",
                ("Give notice to avoid auto-renewal",),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row["reminder_created"] is True


def test_single_low_confidence_obligation_escalates(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _chunk_response(("1", "Penalty", "Vague penalty clause with ambiguous wording.")),
    )
    monkeypatch.setattr(
        "app.verticals.contract_tracking.graph.call_llm",
        _extraction_sequence({
            "has_obligation": True,
            "description": "Unclear penalty obligation",
            "raw_date_or_condition": "",
            "references_other_section": "",
            "unusual_wording": True,
            "confidence": 0.3,
        }),
    )

    agent_input = AgentRunInput(
        vertical="contract_tracking",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "1. Penalty. Vague penalty clause with ambiguous wording."},
    )
    output = run_contract_tracking_vertical(agent_input)

    assert output.status == "escalated"
    assert output.escalated is True
    assert output.escalation_reason is not None
    assert "0.30" in output.escalation_reason or "below threshold" in output.escalation_reason

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, reason FROM escalations WHERE reason LIKE %s;",
                ("%clause 1%",),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "open"


def test_mixed_contract_some_reminded_some_escalated_in_same_run(monkeypatch):
    """The core Section 8.3 requirement: one contract, some obligations
    auto-actioned, others escalated, in the SAME run."""
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _chunk_response(
            ("1", "Renewal", "Clear renewal clause."),
            ("2", "Termination", "Ambiguous termination clause."),
        ),
    )
    extraction_responses = iter([
        {
            "has_obligation": True, "description": "Renew on time",
            "raw_date_or_condition": "yearly", "references_other_section": "",
            "unusual_wording": False, "confidence": 0.9,
        },
        {
            "has_obligation": True, "description": "Unclear termination window",
            "raw_date_or_condition": "", "references_other_section": "",
            "unusual_wording": False, "confidence": 0.4,
        },
    ])
    date_responses = iter([
        # clause 1 has raw_date_or_condition="yearly" -> date resolution fires
        # Use "computed" so no penalty -> 0.9 stays above 0.85 -> auto-reminds
        {"obligation_date": "2027-01-01", "date_status": "computed", "reasoning": "Annual renewal"},
        # clause 2 has empty raw_date_or_condition -> returns early, no LLM call
    ])

    def fake_graph_llm(**kwargs):
        system = (kwargs.get("messages") or [{}])[0].get("content", "")
        if "date analyst" in system:
            return {"content": json.dumps(next(date_responses)), "tool_calls": []}
        return {"content": json.dumps(next(extraction_responses)), "tool_calls": []}

    monkeypatch.setattr("app.verticals.contract_tracking.graph.call_llm", fake_graph_llm)

    agent_input = AgentRunInput(
        vertical="contract_tracking",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "1. Renewal...\n2. Termination..."},
    )
    output = run_contract_tracking_vertical(agent_input)

    # Run-level: escalated=True because AT LEAST ONE item escalated,
    # but the confident obligation's action still shows up too —
    # this is the core Section 8.3 requirement being verified.
    assert output.escalated is True
    assert len(output.actions_taken) == 1
    assert output.actions_taken[0].action_name == "create_calendar_reminder"
    assert output.escalation_reason is not None
    assert "clause 2" in output.escalation_reason

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT description, reminder_created FROM obligations ORDER BY description;"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    # Both obligations were inserted regardless of outcome; only the
    # confident one got reminder_created=True.
    by_description = {r["description"]: r["reminder_created"] for r in rows}
    assert by_description["Renew on time"] is True
    assert by_description["Unclear termination window"] is False


def test_no_obligation_clause_creates_no_action_and_no_escalation(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _chunk_response(("1", "Recitals", "This agreement is between the parties.")),
    )
    monkeypatch.setattr(
        "app.verticals.contract_tracking.graph.call_llm",
        _extraction_sequence({
            "has_obligation": False,
            "description": "", "raw_date_or_condition": "",
            "references_other_section": "", "unusual_wording": False,
            "confidence": 1.0,
        }),
    )

    agent_input = AgentRunInput(
        vertical="contract_tracking",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "1. Recitals. This agreement is between the parties."},
    )
    output = run_contract_tracking_vertical(agent_input)

    assert output.status == "completed"
    assert output.escalated is False
    assert output.actions_taken == []


def test_cross_reference_triggers_get_surrounding_clauses_and_reextraction(monkeypatch):
    """Clause 2 references clause 1. Extraction should be called
    TWICE for clause 2: once initially, once after resolving the
    cross-reference — and the second call's result should win."""
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _chunk_response(
            ("1", "Definitions", "Notice period means 30 days."),
            ("2", "Termination", "Either party may terminate per the notice period in Section 1."),
        ),
    )

    _extr_cross = iter([
        # Clause 1 extraction (no obligation, it's a definition)
        {"has_obligation": False, "description": "", "raw_date_or_condition": "",
         "references_other_section": "", "unusual_wording": False, "confidence": 1.0},
        # Clause 2 first extraction: flags the cross-reference
        {"has_obligation": True, "description": "Termination requires notice",
         "raw_date_or_condition": "", "references_other_section": "1",
         "unusual_wording": False, "confidence": 0.5},
        # Clause 2 RE-extraction after cross-reference resolved
        {"has_obligation": True, "description": "Termination requires 30 days notice",
         "raw_date_or_condition": "30 days", "references_other_section": "1",
         "unusual_wording": False, "confidence": 0.92},
    ])
    # Date resolution: event_triggered (0.92 - 0.05 = 0.87 >= 0.85 -> still reminds)
    _date_cross = iter([
        {"obligation_date": None, "date_status": "event_triggered",
         "reasoning": "30 days from unspecified trigger"},
    ])

    def fake_call_llm(**kwargs):
        s = (kwargs.get("messages") or [{}])[0].get("content", "")
        if "date analyst" in s:
            return {"content": json.dumps(next(_date_cross)), "tool_calls": []}
        return {"content": json.dumps(next(_extr_cross)), "tool_calls": []}

    monkeypatch.setattr("app.verticals.contract_tracking.graph.call_llm", fake_call_llm)

    agent_input = AgentRunInput(
        vertical="contract_tracking",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "1. Definitions...\n2. Termination..."},
    )
    output = run_contract_tracking_vertical(agent_input)

    # Final confidence (0.92) cleared ACTION_THRESHOLD, so this
    # proves the RE-extraction's result was used, not the first
    # (0.5) pass.
    assert output.escalated is False
    assert len(output.actions_taken) == 1

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT description, confidence FROM obligations WHERE description LIKE %s;",
                ("%30 days notice%",),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["confidence"] == pytest.approx(0.92)


def test_malformed_extraction_output_fails_safe_to_no_obligation(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _chunk_response(("1", "Odd", "Some clause text.")),
    )
    monkeypatch.setattr(
        "app.verticals.contract_tracking.graph.call_llm",
        lambda **kwargs: {"content": "not valid json", "tool_calls": []},
    )

    agent_input = AgentRunInput(
        vertical="contract_tracking",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "1. Odd. Some clause text."},
    )
    output = run_contract_tracking_vertical(agent_input)

    # Fails safe: no obligation extracted, no crash, run completes cleanly.
    assert output.status == "completed"
    assert output.escalated is False
    assert output.actions_taken == []


def test_clauses_are_persisted_into_kb_with_metadata(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _chunk_response(("7", "Confidentiality", "A unique_marker_zzy91 clause about secrecy.")),
    )
    monkeypatch.setattr(
        "app.verticals.contract_tracking.graph.call_llm",
        _extraction_sequence({
            "has_obligation": False, "description": "", "raw_date_or_condition": "",
            "references_other_section": "", "unusual_wording": False, "confidence": 1.0,
        }),
    )

    agent_input = AgentRunInput(
        vertical="contract_tracking",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "7. Confidentiality. A unique_marker_zzy91 clause about secrecy."},
    )
    run_contract_tracking_vertical(agent_input)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT metadata, source_type FROM embeddings WHERE chunk_text LIKE %s;",
                ("%unique_marker_zzy91%",),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["source_type"] == "contract_clause"
    assert row["metadata"]["clause_number"] == "7"
    assert row["metadata"]["title"] == "Confidentiality"


def test_scheduled_ingestion_trigger_type_is_logged_on_the_run(monkeypatch):
    """Section 6.4: Vertical 3's scheduled ingestion IS the analysis
    trigger. Confirms the run correctly records that trigger type."""
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _chunk_response(("1", "X", "Some clause.")),
    )
    monkeypatch.setattr(
        "app.verticals.contract_tracking.graph.call_llm",
        _extraction_sequence({
            "has_obligation": False, "description": "", "raw_date_or_condition": "",
            "references_other_section": "", "unusual_wording": False, "confidence": 1.0,
        }),
    )

    agent_input = AgentRunInput(
        vertical="contract_tracking",
        trigger_type=TriggerType.SCHEDULED_INGESTION,
        input_payload={"text": "1. X. Some clause."},
    )
    output = run_contract_tracking_vertical(agent_input)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trigger_type FROM agent_runs WHERE id = %s;", (output.run_id,)
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row["trigger_type"] == "scheduled_ingestion"


def test_mid_contract_failure_closes_out_run_as_escalated_not_stuck_running(monkeypatch):
    """
    Reproduces a real failure observed during seeding: clause 3 of 3
    raises (e.g. a rate-limited LLM call) after clauses 1-2 already
    succeeded and fired real actions. The run must close out as
    escalated — crediting the real work already done — rather than
    propagating the exception and leaving agent_runs permanently
    stuck at status='running' with no record of what happened.
    """
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _chunk_response(
            ("1", "Renewal", "Clause one, a clean renewal obligation."),
            ("2", "Termination", "Clause two, a clean termination obligation."),
            ("3", "Confidentiality", "Clause three, never gets extracted."),
        ),
    )

    extraction_count = {"n": 0}
    def fake_call_llm(**kwargs):
        system = (kwargs.get("messages") or [{}])[0].get("content", "")
        if "date analyst" in system:
            # Return computed so no penalty — we want the 2 successful
            # clauses to auto-remind, not escalate due to vague date penalty.
            return {"content": json.dumps(
                {"obligation_date": "2027-01-01", "date_status": "computed",
                 "reasoning": "30 days from effective date"}
            ), "tool_calls": []}
        # Extraction call
        extraction_count["n"] += 1
        if extraction_count["n"] <= 2:
            return {"content": json.dumps({
                "has_obligation": True,
                "description": f"Obligation {extraction_count['n']}",
                "raw_date_or_condition": "30 days",
                "references_other_section": "",
                "unusual_wording": False,
                "confidence": 0.95,
            }), "tool_calls": []}
        raise RuntimeError("Error code: 429 - rate_limit_exceeded")

    monkeypatch.setattr("app.verticals.contract_tracking.graph.call_llm", fake_call_llm)

    agent_input = AgentRunInput(
        vertical="contract_tracking",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "1. Renewal...\n2. Termination...\n3. Confidentiality..."},
    )
    output = run_contract_tracking_vertical(agent_input)  # must NOT raise

    # The run closed out — status is a real terminal state, not stuck.
    assert output.status == "escalated"
    assert output.escalated is True

    # The two obligations that succeeded before the failure are still
    # credited as real actions — not discarded.
    assert len(output.actions_taken) == 2
    assert all(a.action_name == "create_calendar_reminder" for a in output.actions_taken)

    # The escalation reason names the failed clause and flags clause
    # 3 as unprocessed, so a human reviewing it knows exactly what's
    # missing.
    assert any("clause 3" in e for e in [output.escalation_reason or ""])

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM agent_runs WHERE id = %s;", (output.run_id,))
            run_row = cur.fetchone()
            cur.execute(
                "SELECT reason FROM escalations WHERE run_id = %s AND pending_action IS NULL;",
                (output.run_id,),
            )
            escalation_row = cur.fetchone()
    finally:
        conn.close()

    # Confirms the DB row itself closed out — this is the exact bug
    # being fixed: previously this row would be permanently stuck at
    # status='running'.
    assert run_row["status"] == "escalated"
    assert escalation_row is not None
    # Error message is now sanitized via _classify_error() — raw API
    # details (org IDs, billing URLs) are never stored in escalation reason.
    assert "rate limit" in escalation_row["reason"].lower() or "temporarily unavailable" in escalation_row["reason"].lower()



# ---------------------------------------------------------------
# _resolve_obligation_date tests — five date_status branches
# ---------------------------------------------------------------

def _fake_date_llm(obligation_date, date_status, reasoning="test"):
    """Returns a fake call_llm that responds with the given date resolution."""
    import json as _json
    payload = {
        "obligation_date": obligation_date,
        "date_status": date_status,
        "reasoning": reasoning,
    }
    return lambda **kwargs: {"content": _json.dumps(payload), "tool_calls": []}


def test_resolve_obligation_date_empty_input_returns_empty_status(monkeypatch):
    """Empty raw_date_or_condition returns empty status without an LLM call."""
    called = []
    monkeypatch.setattr(
        "app.verticals.contract_tracking.graph.call_llm",
        lambda **kwargs: called.append(True) or {"content": "{}", "tool_calls": []},
    )

    result = _resolve_obligation_date("", effective_date="2026-01-15")

    assert result["date_status"] == "empty"
    assert result["obligation_date"] is None
    assert called == []  # no LLM call made


def test_resolve_obligation_date_computed_returns_date_object(monkeypatch):
    """When LLM returns a computed date, it's parsed into a date object."""
    from datetime import date as _date
    monkeypatch.setattr(
        "app.verticals.contract_tracking.graph.call_llm",
        _fake_date_llm("2026-11-16", "computed", "60 days before annual renewal"),
    )

    result = _resolve_obligation_date(
        "at least 60 days before the renewal date",
        effective_date="2026-01-15",
    )

    assert result["date_status"] == "computed"
    assert result["obligation_date"] == _date(2026, 11, 16)


def test_resolve_obligation_date_vague_returns_none(monkeypatch):
    """Vague timing returns None for obligation_date."""
    monkeypatch.setattr(
        "app.verticals.contract_tracking.graph.call_llm",
        _fake_date_llm(None, "vague", "No concrete deadline possible"),
    )

    result = _resolve_obligation_date(
        "within a reasonable time",
        effective_date="2026-01-15",
    )

    assert result["date_status"] == "vague"
    assert result["obligation_date"] is None


def test_resolve_obligation_date_event_triggered_returns_none(monkeypatch):
    """Event-triggered timing returns None for obligation_date."""
    monkeypatch.setattr(
        "app.verticals.contract_tracking.graph.call_llm",
        _fake_date_llm(None, "event_triggered", "Depends on invoice date"),
    )

    result = _resolve_obligation_date(
        "within 30 days of the invoice date",
        effective_date="2026-01-15",
    )

    assert result["date_status"] == "event_triggered"
    assert result["obligation_date"] is None


def test_resolve_obligation_date_no_timing_returns_none(monkeypatch):
    """No temporal component returns no_timing status."""
    monkeypatch.setattr(
        "app.verticals.contract_tracking.graph.call_llm",
        _fake_date_llm(None, "no_timing", "Standing duty with no deadline"),
    )

    result = _resolve_obligation_date(
        "Supplier shall maintain adequate insurance coverage",
        effective_date="2026-01-15",
    )

    assert result["date_status"] == "no_timing"
    assert result["obligation_date"] is None


def test_resolve_obligation_date_falls_back_gracefully_on_llm_error(monkeypatch):
    """LLM error during date resolution never breaks the pipeline."""
    def _raising_llm(**kwargs):
        raise RuntimeError("Simulated LLM failure")

    monkeypatch.setattr(
        "app.verticals.contract_tracking.graph.call_llm",
        _raising_llm,
    )

    result = _resolve_obligation_date(
        "within 60 days of the renewal date",
        effective_date="2026-01-15",
    )

    # Falls back gracefully — never raises
    assert result["date_status"] == "vague"
    assert result["obligation_date"] is None


def test_escalation_reason_contains_raw_timing_text(monkeypatch):
    """
    When an obligation is escalated, the reason contains the raw timing
    text from the contract — not just the generic confidence message.
    This is the key difference from the old behaviour.
    """
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _chunk_response(
            ("1", "Payment", "Client shall pay within a reasonable time."),
            effective_date="2026-01-15",
        ),
    )

    # Extraction: low confidence, has obligation, vague timing
    extraction_responses = iter([
        {
            "has_obligation": True,
            "description": "Client shall pay",
            "raw_date_or_condition": "within a reasonable time",
            "references_other_section": "",
            "unusual_wording": False,
            "confidence": 0.72,
        },
    ])

    # Date resolution: vague
    date_responses = iter([
        {"obligation_date": None, "date_status": "vague", "reasoning": "No concrete date"},
    ])

    def fake_call_llm(**kwargs):
        import json as _json
        system = kwargs.get("messages", [{}])[0].get("content", "")
        if "date analyst" in system:
            return {"content": _json.dumps(next(date_responses)), "tool_calls": []}
        return {"content": _json.dumps(next(extraction_responses)), "tool_calls": []}

    monkeypatch.setattr("app.verticals.contract_tracking.graph.call_llm", fake_call_llm)

    agent_input = AgentRunInput(
        vertical="contract_tracking",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "1. Payment. Client shall pay within a reasonable time."},
    )
    output = run_contract_tracking_vertical(agent_input)

    assert output.escalated is True
    # The reason must contain the raw timing text, not just the confidence number
    assert output.escalation_reason is not None
    assert "reasonable time" in output.escalation_reason.lower()
    assert "vague" in output.escalation_reason.lower()


def test_vague_date_penalty_causes_escalation_despite_high_raw_confidence(monkeypatch):
    """
    Approach B: an obligation with high raw extraction confidence (0.92)
    but vague timing gets penalised by VAGUE_DATE_CONFIDENCE_PENALTY (0.12),
    giving effective_confidence = 0.80 < ACTION_THRESHOLD (0.85) -> escalated.
    Without the penalty it would have auto-reminded.
    """
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _chunk_response(
            ("1", "Deliverable", "Advisor shall submit within a reasonable time."),
            effective_date=None,
        ),
    )

    extraction_responses = iter([{
        "has_obligation": True,
        "description": "Submit deliverables",
        "raw_date_or_condition": "within a reasonable time",
        "references_other_section": "",
        "unusual_wording": False,
        "confidence": 0.92,  # above threshold raw, below after penalty
    }])
    date_responses = iter([{
        "obligation_date": None,
        "date_status": "vague",
        "reasoning": "No concrete deadline possible",
    }])

    def fake_call_llm(**kwargs):
        system = (kwargs.get("messages") or [{}])[0].get("content", "")
        if "date analyst" in system:
            return {"content": json.dumps(next(date_responses)), "tool_calls": []}
        return {"content": json.dumps(next(extraction_responses)), "tool_calls": []}

    monkeypatch.setattr("app.verticals.contract_tracking.graph.call_llm", fake_call_llm)

    agent_input = AgentRunInput(
        vertical="contract_tracking",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "1. Deliverable. Advisor shall submit within a reasonable time."},
    )
    output = run_contract_tracking_vertical(agent_input)

    # 0.92 - 0.12 = 0.80 < 0.85 -> must escalate
    assert output.escalated is True
    assert output.escalation_reason is not None
    # Reason shows both raw and effective confidence
    assert "0.92" in output.escalation_reason
    assert "0.80" in output.escalation_reason
    assert "vague" in output.escalation_reason.lower()


def test_event_triggered_penalty_causes_escalation(monkeypatch):
    """
    EVENT_TRIGGERED_CONFIDENCE_PENALTY (0.05) applied to an obligation
    with raw confidence 0.88 gives effective 0.83 < 0.85 -> escalated.
    """
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _chunk_response(
            ("1", "Payment", "Client shall pay within 30 days of invoice."),
            effective_date=None,
        ),
    )

    extraction_responses = iter([{
        "has_obligation": True,
        "description": "Pay within 30 days of invoice",
        "raw_date_or_condition": "within 30 days of invoice date",
        "references_other_section": "",
        "unusual_wording": False,
        "confidence": 0.88,
    }])
    date_responses = iter([{
        "obligation_date": None,
        "date_status": "event_triggered",
        "reasoning": "Depends on invoice date",
    }])

    def fake_call_llm(**kwargs):
        system = (kwargs.get("messages") or [{}])[0].get("content", "")
        if "date analyst" in system:
            return {"content": json.dumps(next(date_responses)), "tool_calls": []}
        return {"content": json.dumps(next(extraction_responses)), "tool_calls": []}

    monkeypatch.setattr("app.verticals.contract_tracking.graph.call_llm", fake_call_llm)

    agent_input = AgentRunInput(
        vertical="contract_tracking",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "1. Payment. Client shall pay within 30 days of invoice."},
    )
    output = run_contract_tracking_vertical(agent_input)

    # 0.88 - 0.05 = 0.83 < 0.85 -> must escalate
    assert output.escalated is True
    assert output.escalation_reason is not None
    assert "event" in output.escalation_reason.lower() or "future event" in output.escalation_reason.lower()


def test_computed_date_no_penalty_auto_reminds(monkeypatch):
    """
    No penalty for computed dates — obligation with raw confidence 0.90
    and a successfully computed date still auto-reminds.
    """
    from datetime import date as _date
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _chunk_response(
            ("1", "Renewal", "Give 60 days notice before renewal."),
            effective_date="2026-01-15",
        ),
    )

    extraction_responses = iter([{
        "has_obligation": True,
        "description": "Give 60 days notice before renewal",
        "raw_date_or_condition": "60 days before renewal date",
        "references_other_section": "",
        "unusual_wording": False,
        "confidence": 0.90,
    }])
    date_responses = iter([{
        "obligation_date": "2026-11-16",
        "date_status": "computed",
        "reasoning": "60 days before annual renewal on 2027-01-15",
    }])

    def fake_call_llm(**kwargs):
        system = (kwargs.get("messages") or [{}])[0].get("content", "")
        if "date analyst" in system:
            return {"content": json.dumps(next(date_responses)), "tool_calls": []}
        return {"content": json.dumps(next(extraction_responses)), "tool_calls": []}

    monkeypatch.setattr("app.verticals.contract_tracking.graph.call_llm", fake_call_llm)

    agent_input = AgentRunInput(
        vertical="contract_tracking",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "1. Renewal. Give 60 days notice before renewal."},
    )
    output = run_contract_tracking_vertical(agent_input)

    # 0.90, no penalty (computed) -> auto-reminds
    assert output.escalated is False
    assert len(output.actions_taken) == 1
    assert output.actions_taken[0].action_name == "create_calendar_reminder"
