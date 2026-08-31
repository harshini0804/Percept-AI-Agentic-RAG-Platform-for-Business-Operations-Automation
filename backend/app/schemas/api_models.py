"""
API response/request models — separate from agent_contract.py,
which defines the internal agent run contract (Section 3.2). These
describe what the API actually sends/receives over HTTP.
"""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel


class AgentRunSummary(BaseModel):
    id: str
    vertical: str
    trigger_type: str
    status: str
    confidence: Optional[float]
    created_at: datetime


class AgentDecisionDetail(BaseModel):
    id: str
    step_type: str
    detail: Optional[dict[str, Any]]
    created_at: datetime


class AgentRunDetail(AgentRunSummary):
    decisions: list[AgentDecisionDetail]


class EscalationSummary(BaseModel):
    id: str
    run_id: str
    vertical: str
    reason: Optional[str]
    assigned_to: Optional[str]
    status: str
    pending_action: Optional[dict[str, Any]]
    created_at: datetime


class ResolveEscalationRequest(BaseModel):
    approve: bool
    resolved_by: Optional[str] = None


class NotificationSummary(BaseModel):
    id: str
    run_id: str
    recipient: str
    message: str
    read: bool
    created_at: datetime