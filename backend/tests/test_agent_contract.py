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