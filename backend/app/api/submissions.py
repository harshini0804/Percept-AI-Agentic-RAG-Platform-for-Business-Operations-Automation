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

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from app.core.vertical_registry import run_vertical
from app.core.documents import create_document
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


@router.post("/upload", response_model=AgentRunOutput)
async def submit_run_with_file(vertical: str = Form(...), file: UploadFile = File(...)):
    """
    File-upload variant of submit_run — a separate endpoint rather
    than a shared one, since FastAPI can't parse both a JSON body and
    multipart/form-data on the same route. Extracts the file's text
    immediately (Section 6's extraction logic, reused via
    app.core.documents), creates its `documents` tracking row, and
    passes input_document_id through the same shared contract as the
    text-only path above.
    """
    raw_bytes = await file.read()

    try:
        document_id = create_document(
            vertical=vertical,
            filename=file.filename or "uploaded_file",
            raw_bytes=raw_bytes,
            source="upload",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    agent_input = AgentRunInput(
        vertical=vertical,
        trigger_type=TriggerType.UPLOAD,
        input_document_id=document_id,
    )

    try:
        return run_vertical(vertical, agent_input)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))