"""
Integrations API — the automatic-intake doors for Vertical 4.

POST /integrations/zoom    receives "a meeting transcript is ready"
POST /integrations/github  receives "a pull request was merged"
GET  /integrations/events  the Live Feed's data

Each webhook follows the same order, cheapest and safest check first:
secret configured? -> signature valid? -> is it an event we care about?
-> is it from an allowed place / known person? -> have we seen this
exact delivery before? -> hand off to a background task and answer
immediately (providers expect a fast reply and re-send otherwise).
Anything refused or skipped is still recorded, so the Live Feed shows
the platform's decisions, not just its successes.

NOTE: this is the receiving side only. Registering these URLs in real
Zoom / GitHub apps (public HTTPS address, real secrets) is what a
production rollout would add; the demo's pretend senders call exactly
these endpoints with correctly signed, real-format messages.
"""

import json
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.integrations import event_log
from app.integrations.handlers import process_github_merge, process_zoom_transcript
from app.integrations.identity import (
    ALLOWED_GITHUB_REPOS,
    is_allowed_download_url,
    owner_for_github_login,
)
from app.integrations.parsing import parse_github_merge, parse_zoom_transcript_event
from app.integrations.security import (
    verify_github_signature,
    verify_zoom_signature,
    zoom_url_validation_response,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])


class IntegrationEventEntry(BaseModel):
    id: str
    source: str
    delivery_id: str
    event_type: str | None = None
    status: str
    summary: str | None = None
    owner: str | None = None
    run_id: str | None = None
    received_at: str


def _secret(env_name: str) -> str:
    secret = os.getenv(env_name, "")
    if not secret:
        # Fail closed: with no secret configured nothing can be verified,
        # so nothing is accepted.
        raise HTTPException(status_code=503, detail=f"{env_name} is not configured.")
    return secret


def _ignored(source: str, delivery_id: str, event_type: str | None, reason: str) -> JSONResponse:
    event_log.record_event(source, delivery_id, event_type, "ignored", summary=reason)
    return JSONResponse({"status": "ignored", "reason": reason}, status_code=200)


@router.post("/github")
async def github_webhook(request: Request, background: BackgroundTasks):
    secret = _secret("GITHUB_WEBHOOK_SECRET")
    raw_body = await request.body()
    event_name = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery")

    if not verify_github_signature(secret, raw_body, request.headers.get("X-Hub-Signature-256")):
        event_log.record_rejected("github", event_name or None, "Rejected: signature did not match.", delivery_id)
        raise HTTPException(status_code=401, detail="Invalid signature.")

    if event_name == "ping":
        return {"status": "pong"}
    if not delivery_id:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Delivery header.")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Body is not valid JSON.")

    merge = parse_github_merge(event_name, payload)
    if merge is None:
        return _ignored("github", delivery_id, event_name, "Not a merged pull request.")
    if merge["repo"] not in ALLOWED_GITHUB_REPOS:
        return _ignored("github", delivery_id, event_name, f'Repository "{merge["repo"]}" is not on the allowed list.')
    owner = owner_for_github_login(merge["author_login"])
    if owner is None:
        return _ignored("github", delivery_id, event_name, f'GitHub account "{merge["author_login"]}" is not mapped to a team member.')

    event_id, is_new = event_log.record_event(
        "github", delivery_id, event_name, "received",
        summary=f'Merged PR #{merge["number"]} "{merge["title"]}" by {owner} — processing.',
        owner=owner,
    )
    if not is_new:
        return {"status": "duplicate"}

    background.add_task(process_github_merge, event_id, owner, merge)
    return JSONResponse({"status": "accepted", "event_id": event_id}, status_code=202)


@router.post("/zoom")
async def zoom_webhook(request: Request, background: BackgroundTasks):
    secret = _secret("ZOOM_WEBHOOK_SECRET")
    raw_body = await request.body()

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Body is not valid JSON.")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object.")
    event_name = payload.get("event")

    # Zoom's one-time handshake when a webhook URL is registered. Answering
    # it correctly proves we hold the secret; it carries no data to act on.
    if event_name == "endpoint.url_validation":
        plain_token = (payload.get("payload") or {}).get("plainToken")
        if not isinstance(plain_token, str) or not plain_token:
            raise HTTPException(status_code=400, detail="Missing plainToken.")
        return zoom_url_validation_response(secret, plain_token)

    if not verify_zoom_signature(
        secret, raw_body,
        request.headers.get("x-zm-signature"),
        request.headers.get("x-zm-request-timestamp"),
    ):
        event_log.record_rejected("zoom", event_name, "Rejected: signature did not match (or message was too old).")
        raise HTTPException(status_code=401, detail="Invalid signature.")

    transcript = parse_zoom_transcript_event(payload)
    if transcript is None:
        meeting = (((payload.get("payload") or {}).get("object")) or {}).get("uuid") or "unknown"
        return _ignored("zoom", f"{meeting}:{event_name}", event_name, "Not a transcript-ready event.")

    delivery_id = f'{transcript["meeting_uuid"]}:{event_name}'
    if not is_allowed_download_url(transcript["download_url"]):
        return _ignored("zoom", delivery_id, event_name, "Transcript link points to a host that is not allowed.")

    event_id, is_new = event_log.record_event(
        "zoom", delivery_id, event_name, "received",
        summary=f'Transcript ready for "{transcript["topic"]}" — downloading.',
    )
    if not is_new:
        return {"status": "duplicate"}

    background.add_task(
        process_zoom_transcript, event_id, transcript["topic"],
        transcript["download_url"], payload.get("download_token"),
    )
    return JSONResponse({"status": "accepted", "event_id": event_id}, status_code=202)


@router.get("/events", response_model=list[IntegrationEventEntry])
def list_integration_events(
    limit: int = Query(default=50, ge=1, le=200),
    source: str | None = Query(default=None),
):
    rows = event_log.list_events(limit=limit, source=source)
    return [
        IntegrationEventEntry(
            id=str(r["id"]),
            source=r["source"],
            delivery_id=r["delivery_id"],
            event_type=r["event_type"],
            status=r["status"],
            summary=r["summary"],
            owner=r["owner"],
            run_id=str(r["run_id"]) if r["run_id"] else None,
            received_at=r["received_at"].isoformat(),
        )
        for r in rows
    ]
