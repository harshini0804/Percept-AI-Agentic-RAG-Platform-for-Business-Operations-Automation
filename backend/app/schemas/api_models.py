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
    # Vertical 2 (internal_mobility, Section 8.2): the ranked candidate
    # leaderboard for this run. Empty for non-internal-mobility runs.
    role_matches: list["RoleMatchSummary"] = []

class AgentRunStats(BaseModel):
    """
    Real aggregate counts across ALL runs needed for the Dashboard's summary cards. 
    """
    total: int
    completed: int
    escalated: int
    running: int
    rejected: int


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
    vertical: str
    recipient: str
    message: str
    read: bool
    created_at: datetime


class RoleMatchSummary(BaseModel):
    """One ranked internal candidate for a role (Vertical 2, Section 8.2),
    joined with the employee's name/department and the dynamic capacity
    badge from employee_workload."""
    id: str
    rank: int
    employee_name: Optional[str] = None
    department: Optional[str] = None
    rationale: str
    confidence: float
    notified: bool
    utilization_pct: Optional[int] = None


AgentRunDetail.model_rebuild()
