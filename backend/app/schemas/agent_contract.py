"""
Shared Agent Contract (Section 3.2, Final Implementation Plan)

Every vertical's LangGraph node sequence must consume AgentRunInput
and produce AgentRunOutput in exactly this shape. This is what makes
the orchestration engine, escalation queue, and evaluation harness
vertical-agnostic.
"""

from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, Field, model_validator


class Vertical(str, Enum):
    """
    Documents Section 4.3's canonical vertical names. NOT used to
    validate AgentRunInput.vertical (see that field's comment) —
    kept here as a single documented reference for the four real
    vertical names, usable by anything that wants strict validation
    later (e.g. a future /verticals metadata endpoint).
    """
    POST_INCIDENT = "post_incident"
    INTERNAL_MOBILITY = "internal_mobility"
    CONTRACT_TRACKING = "contract_tracking"
    MEETING_ACTION_ITEMS = "meeting_action_items"
    # Not a real vertical — the reference/test implementation
    # (backend/app/verticals/dummy/) used to prove the shared core
    # composes into a working end-to-end pipeline (Section 10, Phase
    # 1 checkpoint).
    DUMMY = "dummy"


class TriggerType(str, Enum):
    # Vertical 1, 2, 3 (upload) and Vertical 4 (trigger 1)
    UPLOAD = "upload"
    # Vertical 3's ingestion+analysis collapsed event (Section 6.4)
    SCHEDULED_INGESTION = "scheduled_ingestion"
    # Vertical 4's trigger 2 (Section 8.4)
    SCHEDULED_FOLLOWUP = "scheduled_followup"
    # Admin panel manual resync (Section 6.4) — ingestion only, not
    # normally a full agent run, but included for completeness
    MANUAL_RESYNC = "manual_resync"


class ActionTaken(BaseModel):
    """
    One entry per write-tool invocation during a run
    (e.g. create_engineering_ticket, notify_candidate,
    create_calendar_reminder, send_nudge, escalate_to_manager).
    """
    action_name: str
    target_id: Optional[str] = None   # e.g. ticket id, reminder id
    detail: Optional[dict[str, Any]] = None


class AgentRunInput(BaseModel):
    # Deliberately a plain str, not the Vertical enum above — the
    # single source of truth for "which verticals actually exist" is
    # the vertical_registry (a KeyError there already produces the
    # same 404 an enum ValidationError would), not a second,
    # separately-maintained list here. Avoids every new vertical
    # needing an update in agent_contract.py just to avoid a
    # confusing validation error with no behavioral difference.
    vertical: str
    trigger_type: TriggerType
    input_document_id: Optional[str] = None
    input_payload: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def exactly_one_input_source(self) -> "AgentRunInput":
        # Section 3.2 specifies input_document_id | input_payload —
        # exactly one must be set, never both, never neither.
        has_doc = self.input_document_id is not None
        has_payload = self.input_payload is not None
        if has_doc == has_payload:
            raise ValueError(
                "Exactly one of input_document_id or input_payload must be set."
            )
        return self


class AgentRunOutput(BaseModel):
    run_id: str
    status: str  # e.g. "completed", "failed", "escalated"
    confidence: float = Field(ge=0.0, le=1.0)
    actions_taken: list[ActionTaken] = Field(default_factory=list)
    escalated: bool
    escalation_reason: Optional[str] = None

    @model_validator(mode="after")
    def escalation_reason_required_if_escalated(self) -> "AgentRunOutput":
        if self.escalated and not self.escalation_reason:
            raise ValueError("escalation_reason is required when escalated=True.")
        if not self.escalated and self.escalation_reason:
            raise ValueError("escalation_reason must be null when escalated=False.")
        return self


def build_agent_run_output(state: dict) -> AgentRunOutput:
    """
    Builds and validates an AgentRunOutput from a vertical's final
    LangGraph state (the AgentState shape defined in
    app/core/orchestration.py, per Section 3.3's pipeline).

    This is the one place every vertical's graph should route its
    final result through before returning it to the vertical
    registry — satisfying Section 7.2's requirement that each
    vertical "implement the shared agent contract" at the actual
    boundary between a vertical's graph and the rest of the system,
    while leaving the internal AgentState (embedding, retrieval
    results, etc.) free to carry whatever intermediate working data
    a vertical's graph needs.

    Wraps AgentState's singular `action_taken` into the contract's
    `actions_taken` list — every vertical built so far fires at most
    one action per run, but the contract itself allows for more.
    """
    action_taken = state.get("action_taken")
    actions_taken = (
        [
            ActionTaken(
                action_name=action_taken["action_name"],
                detail=action_taken.get("result"),
            )
        ]
        if action_taken
        else []
    )

    return AgentRunOutput(
        run_id=state["run_id"],
        status="escalated" if state["escalated"] else "completed",
        confidence=state["confidence"],
        actions_taken=actions_taken,
        escalated=state["escalated"],
        escalation_reason=state.get("escalation_reason"),
    )