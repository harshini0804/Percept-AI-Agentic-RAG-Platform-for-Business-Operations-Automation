"""
What happens AFTER a message has been verified and recorded.

These run in the background (the webhook endpoint answers the sender
immediately, as providers require, and the slower work — downloading,
embedding, the LLM — happens here). Each handler finishes by updating
the message's integration_events row, so the Live Feed always shows
what the message caused, including failures.

Zoom transcript  -> Trigger 1 (the existing meeting pipeline).
GitHub merge     -> owner_activity evidence -> event-driven resolve check.
"""

import logging

import httpx

from app.core.documents import create_document
from app.core.embeddings import upsert_embedding
from app.core.vertical_registry import run_vertical
from app.integrations.event_log import update_event
from app.integrations.parsing import parse_vtt
from app.schemas.agent_contract import AgentRunInput, TriggerType
from app.verticals.meeting_action_items.followup import check_owner_for_new_evidence

logger = logging.getLogger(__name__)

VERTICAL = "meeting_action_items"
MAX_TRANSCRIPT_BYTES = 2_000_000
DOWNLOAD_TIMEOUT_SECONDS = 20


class TranscriptDownloadError(Exception):
    pass


def fetch_transcript(download_url: str, download_token: str | None) -> str:
    """Downloads the transcript file the Zoom message pointed to."""
    headers = {"Authorization": f"Bearer {download_token}"} if download_token else {}
    try:
        response = httpx.get(
            download_url, headers=headers, timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=False
        )
    except httpx.HTTPError as exc:
        raise TranscriptDownloadError(f"could not reach the file host ({type(exc).__name__})") from exc
    if response.status_code != 200:
        raise TranscriptDownloadError(f"file host answered {response.status_code}")
    if len(response.content) > MAX_TRANSCRIPT_BYTES:
        raise TranscriptDownloadError("transcript file is too large")
    return response.text


def process_zoom_transcript(event_id: str, topic: str, download_url: str, download_token: str | None) -> None:
    try:
        raw = fetch_transcript(download_url, download_token)
        transcript = parse_vtt(raw) if raw.lstrip().startswith("WEBVTT") else raw.strip()
        if not transcript:
            update_event(event_id, status="failed", summary="The transcript file was empty.")
            return

        safe_topic = "".join(c if c.isalnum() or c in " -_" else "" for c in topic).strip() or "meeting"
        document_id = create_document(
            vertical=VERTICAL,
            filename=f"zoom - {safe_topic}.txt",
            raw_bytes=f"{topic}\n\n{transcript}".encode("utf-8"),
            source="zoom",
        )
        output = run_vertical(
            VERTICAL,
            AgentRunInput(
                vertical=VERTICAL,
                trigger_type=TriggerType.INTEGRATION_EVENT,
                input_document_id=document_id,
            ),
        )
        run_id = output.run_id
        if output.escalated:
            summary = f'Transcript "{topic}" received but sent for review: {output.escalation_reason}'
            status = "needs_review"
        else:
            count = len(output.actions_taken)
            summary = f'Transcript "{topic}" read automatically — {count} action item(s) created.'
            status = "processed"
        update_event(
            event_id,
            status=status,
            summary=summary,
            run_id=run_id,
            detail={"document_id": document_id, "action_items_created": len(output.actions_taken)},
        )
    except TranscriptDownloadError as exc:
        logger.warning("Zoom transcript download failed: %s", exc)
        update_event(event_id, status="failed", summary=f"Could not download the transcript: {exc}.")
    except Exception as exc:
        logger.exception("Processing of Zoom event %s failed", event_id)
        update_event(
            event_id, status="failed",
            summary=f"Processing failed ({type(exc).__name__}). The meeting was not recorded.",
        )


def process_github_merge(event_id: str, owner: str, merge: dict) -> None:
    try:
        title = merge["title"] or "(no title)"
        text = f'{owner.capitalize()} merged a pull request: "{title}".'
        if merge["body"]:
            text += f" {merge['body']}"
        occurred_at = merge["merged_at"]

        upsert_embedding(
            vertical=VERTICAL,
            source_type="owner_activity",
            chunk_text=text,
            metadata={
                "owner": owner,
                "date": occurred_at[:10],
                "occurred_at": occurred_at,
                "source": "github",
                "ref": merge.get("url"),
            },
        )

        resolved = check_owner_for_new_evidence(owner)
        if resolved:
            names = "; ".join(r["description"] for r in resolved)
            summary = f'PR #{merge["number"]} "{title}" by {owner} — resolved {len(resolved)} item(s): {names}'
        else:
            summary = f'PR #{merge["number"]} "{title}" by {owner} stored as activity — no open item matched.'
        update_event(
            event_id,
            status="processed",
            summary=summary,
            run_id=resolved[0]["run_id"] if resolved else None,
            detail={"resolved": resolved, "pull_request": merge["url"]},
        )
    except Exception as exc:
        logger.exception("Processing of GitHub event %s failed", event_id)
        update_event(
            event_id, status="failed",
            summary=f"Processing failed ({type(exc).__name__}). The merge was not recorded as activity.",
        )
