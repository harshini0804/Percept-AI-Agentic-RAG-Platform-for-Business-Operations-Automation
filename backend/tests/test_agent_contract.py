"""
Tests for app.schemas.agent_contract (Section 3.2) — both the
Pydantic validation rules themselves, and build_agent_run_output's
conversion from a vertical's raw AgentState into a validated
AgentRunOutput (Phase B: reconciling the contract with real usage).
"""

import pytest
from pydantic import ValidationError

from app.schemas.agent_contract import (
    Vertical,
    TriggerType,
    AgentRunInput,
    AgentRunOutput,
    ActionTaken,
    build_agent_run_output,
)


# ---------------------------------------------------------------
# AgentRunInput validation
# ---------------------------------------------------------------

def test_agent_run_input_accepts_input_payload_only():
    agent_input = AgentRunInput(
        vertical=Vertical.DUMMY,
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "hello"},
    )
    assert agent_input.input_document_id is None
    assert agent_input.input_payload == {"text": "hello"}


def test_agent_run_input_accepts_input_document_id_only():
    agent_input = AgentRunInput(
        vertical=Vertical.POST_INCIDENT,
        trigger_type=TriggerType.UPLOAD,
        input_document_id="doc-123",
    )
    assert agent_input.input_document_id == "doc-123"
    assert agent_input.input_payload is None


def test_agent_run_input_rejects_neither_source_set():
    with pytest.raises(ValidationError, match="Exactly one"):
        AgentRunInput(vertical=Vertical.DUMMY, trigger_type=TriggerType.UPLOAD)


def test_agent_run_input_rejects_both_sources_set():
    with pytest.raises(ValidationError, match="Exactly one"):
        AgentRunInput(
            vertical=Vertical.DUMMY,
            trigger_type=TriggerType.UPLOAD,
            input_document_id="doc-123",
            input_payload={"text": "hello"},
        )


def test_agent_run_input_accepts_any_vertical_string():
    """
    Deliberate design choice: AgentRunInput.vertical is NOT validated
    against a fixed enum — the vertical_registry's own KeyError is
    the single source of truth for "does this vertical exist" (see
    agent_contract.py's comment on this field). This test documents
    that choice so it isn't accidentally "fixed" back later.
    """
    agent_input = AgentRunInput(
        vertical="some_future_vertical_not_yet_known_about",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "x"},
    )
    assert agent_input.vertical == "some_future_vertical_not_yet_known_about"


# ---------------------------------------------------------------
# AgentRunOutput validation
# ---------------------------------------------------------------

def test_agent_run_output_requires_run_id():
    """
    Explicit guard: run_id must be a required field, not optional —
    every real AgentRunOutput corresponds to an actual persisted run,
    so a missing run_id should fail loudly at construction time.
    """
    with pytest.raises(ValidationError, match="run_id"):
        AgentRunOutput(status="completed", confidence=0.9, escalated=False)


def test_agent_run_output_rejects_escalated_without_reason():
    with pytest.raises(ValidationError, match="escalation_reason is required"):
        AgentRunOutput(run_id="r1", status="escalated", confidence=0.5, escalated=True)


def test_agent_run_output_rejects_reason_when_not_escalated():
    with pytest.raises(ValidationError, match="must be null"):
        AgentRunOutput(
            run_id="r1", status="completed", confidence=0.9, escalated=False, escalation_reason="x"
        )


def test_agent_run_output_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        AgentRunOutput(run_id="r1", status="completed", confidence=1.5, escalated=False)


def test_agent_run_output_defaults_actions_taken_to_empty_list():
    output = AgentRunOutput(run_id="r1", status="completed", confidence=0.9, escalated=False)
    assert output.actions_taken == []


# ---------------------------------------------------------------
# build_agent_run_output — the actual AgentState -> AgentRunOutput bridge
# ---------------------------------------------------------------

def test_build_agent_run_output_for_completed_run_with_action():
    state = {
        "run_id": "r1",
        "escalated": False,
        "confidence": 0.92,
        "action_taken": {
            "action_name": "log_dummy_action",
            "result": {"logged": True, "note": "x"},
        },
        "escalation_reason": None,
    }

    output = build_agent_run_output(state)

    assert output.run_id == "r1"
    assert output.status == "completed"
    assert output.confidence == pytest.approx(0.92)
    assert output.escalated is False
    assert output.escalation_reason is None
    assert len(output.actions_taken) == 1
    assert output.actions_taken[0].action_name == "log_dummy_action"
    assert output.actions_taken[0].detail == {"logged": True, "note": "x"}


def test_build_agent_run_output_for_escalated_run_with_no_action():
    state = {
        "run_id": "r1",
        "escalated": True,
        "confidence": 0.4,
        "action_taken": None,
        "escalation_reason": "Confidence below threshold.",
    }

    output = build_agent_run_output(state)

    assert output.status == "escalated"
    assert output.escalated is True
    assert output.escalation_reason == "Confidence below threshold."
    assert output.actions_taken == []


def test_build_agent_run_output_is_a_real_validated_agent_run_output():
    """Confirms the helper actually returns the Pydantic type, not
    just a plain dict shaped like one — proving genuine contract
    validation happens at this boundary."""
    state = {
        "run_id": "r1",
        "escalated": False,
        "confidence": 0.9,
        "action_taken": None,
        "escalation_reason": None,
    }
    output = build_agent_run_output(state)
    assert isinstance(output, AgentRunOutput)
    assert output.run_id == "r1"


# ---------------------------------------------------------------
# Multi-item state (Section 8.3/8.4 shape): plural actions_taken /
# escalations keys, used by verticals where one run can produce
# several independent actions and/or escalations.
# ---------------------------------------------------------------

def test_build_agent_run_output_merges_plural_actions_taken():
    state = {
        "run_id": "r1",
        "confidence": 0.8,
        "actions_taken": [
            {"action_name": "create_calendar_reminder", "target_id": "ob1", "result": {"reminder_created": True}},
        ],
    }
    output = build_agent_run_output(state)
    assert output.escalated is False
    assert len(output.actions_taken) == 1
    assert output.actions_taken[0].action_name == "create_calendar_reminder"
    assert output.actions_taken[0].target_id == "ob1"


def test_build_agent_run_output_aggregates_multiple_escalation_reasons():
    state = {
        "run_id": "r1",
        "confidence": 0.5,
        "escalations": [{"reason": "Low confidence on clause 4"}, {"reason": "Ambiguous date"}],
    }
    output = build_agent_run_output(state)
    assert output.escalated is True
    assert output.status == "escalated"
    assert "2 items flagged for manual review" in output.escalation_reason
    assert "Low confidence on clause 4" in output.escalation_reason
    assert "Ambiguous date" in output.escalation_reason


def test_build_agent_run_output_single_plural_escalation_reason_is_verbatim():
    """Exactly one escalation in the plural list should NOT get the
    '1 items flagged...' aggregate wording — should read identically
    to the singular escalation_reason path."""
    state = {
        "run_id": "r1",
        "confidence": 0.5,
        "escalations": [{"reason": "Low confidence on clause 4"}],
    }
    output = build_agent_run_output(state)
    assert output.escalation_reason == "Low confidence on clause 4"


def test_build_agent_run_output_defaults_reason_when_escalation_has_none(monkeypatch=None):
    """
    Edge case flagged in review: state["escalations"] can be a
    non-empty list where NO item actually carries a usable "reason"
    (missing key, or an empty string) — any_escalated is still True
    (the list itself is non-empty/escalated=True), but naively
    `reasons` stays empty, so escalation_reason would be None.
    AgentRunOutput.escalation_reason_required_if_escalated then
    raises ValueError whenever escalated=True and escalation_reason
    is falsy — this must NOT propagate as an unhandled exception out
    of build_agent_run_output.
    """
    state = {
        "run_id": "r1",
        "confidence": 0.5,
        "escalations": [{"reason": ""}],
    }
    output = build_agent_run_output(state)  # must not raise
    assert output.escalated is True
    assert output.status == "escalated"
    assert output.escalation_reason == "Escalated (no reason provided)."


def test_build_agent_run_output_defaults_reason_when_escalation_item_missing_reason_key():
    """Same edge case, but the dict is missing the 'reason' key
    entirely rather than having an empty string."""
    state = {
        "run_id": "r1",
        "confidence": 0.5,
        "escalations": [{}],
    }
    output = build_agent_run_output(state)  # must not raise
    assert output.escalated is True
    assert output.escalation_reason == "Escalated (no reason provided)."


def test_build_agent_run_output_defaults_reason_for_singular_escalated_with_no_reason():
    """Same defensiveness, but via the ORIGINAL singular path:
    escalated=True with no escalation_reason and no plural
    escalations list at all — a vertical author simply forgetting to
    set escalation_reason. Must not raise."""
    state = {
        "run_id": "r1",
        "confidence": 0.5,
        "escalated": True,
    }
    output = build_agent_run_output(state)  # must not raise
    assert output.escalated is True
    assert output.escalation_reason == "Escalated (no reason provided)."


def test_build_agent_run_output_mixed_plural_actions_and_reasonless_escalation():
    """A run with one real action AND one reasonless escalation should
    still surface the action, still escalate, and still get a safe
    default reason rather than crashing — this is the shape a future
    vertical (or a Trigger-2-style rewrite) could plausibly produce."""
    state = {
        "run_id": "r1",
        "confidence": 0.6,
        "actions_taken": [{"action_name": "create_calendar_reminder", "target_id": "ob1"}],
        "escalations": [{}],
    }
    output = build_agent_run_output(state)
    assert output.escalated is True
    assert len(output.actions_taken) == 1
    assert output.escalation_reason == "Escalated (no reason provided)."