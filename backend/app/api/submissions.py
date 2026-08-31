"""
Submissions API (Category B) — powers the Submission page shared UI
screen (Section 5): "one reusable form component, parametrized per
vertical." This single endpoint works for any registered vertical,
looked up via vertical_registry rather than hardcoding per-vertical
routes.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.vertical_registry import run_vertical

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])


class SubmissionRequest(BaseModel):
    vertical: str
    input_text: str


class SubmissionResponse(BaseModel):
    run_id: str
    status: str
    confidence: float
    escalated: bool


@router.post("", response_model=SubmissionResponse)
def submit_run(body: SubmissionRequest):
    """
    Triggers a vertical's run end to end (embed -> retrieve -> reason
    -> confidence gate), via whatever run function that vertical
    registered.
    """
    try:
        final_state = run_vertical(body.vertical, body.input_text)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return SubmissionResponse(
        run_id=final_state["run_id"],
        status="escalated" if final_state["escalated"] else "completed",
        confidence=final_state["confidence"],
        escalated=final_state["escalated"],
    )