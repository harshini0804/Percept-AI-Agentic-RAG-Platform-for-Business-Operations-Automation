"""
Submissions API (Category B) — powers the Submission page shared UI
screen (Section 5): "one reusable form component, parametrized per
vertical." This single endpoint works for any registered vertical,
looked up via vertical_registry rather than hardcoding per-vertical
routes.

Builds a real AgentRunInput and calls the vertical; run_vertical's
KeyError (unregistered vertical) is the single source of truth for
"is this vertical actually available" — there's no separate enum
check here, so a nonsense vertical name and a real-but-not-yet-built
vertical both fail the same way, through the same code path.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.vertical_registry import run_vertical
from app.schemas.agent_contract import AgentRunInput, AgentRunOutput, TriggerType

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])


class SubmissionRequest(BaseModel):
    vertical: str
    input_text: str


@router.post("", response_model=AgentRunOutput)
def submit_run(body: SubmissionRequest):
    """
    Triggers a vertical's run end to end (embed -> retrieve -> reason
    -> confidence gate), via whatever run function that vertical
    registered. The Submission page always represents a direct
    upload/paste trigger (Section 8.1-8.4's "user uploads or pastes"),
    hence trigger_type is fixed to UPLOAD here.
    """
    agent_input = AgentRunInput(
        vertical=body.vertical,
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": body.input_text},
    )

    try:
        return run_vertical(body.vertical, agent_input)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))