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
    POST_INCIDENT = "post_incident"
    INTERNAL_MOBILITY = "internal_mobility"
    CONTRACT_TRACKING = "contract_tracking"
    MEETING_ACTION_ITEMS = "meeting_action_items"


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
    vertical: Vertical
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