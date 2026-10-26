"""
Pure helpers that read the raw payloads sent by GitHub and Zoom.
No database, no network — easy to test, and the one place that knows
the shape of each provider's message.
"""

from datetime import datetime, timezone


def parse_github_merge(event_name: str, payload: dict) -> dict | None:
    """
    Returns the facts we care about if this is a pull request that was
    MERGED (action 'closed' with pull_request.merged true); otherwise
    None. A pull request closed without merging is not evidence of work.
    """
    if event_name != "pull_request" or not isinstance(payload, dict):
        return None
    if payload.get("action") != "closed":
        return None
    pr = payload.get("pull_request")
    if not isinstance(pr, dict) or pr.get("merged") is not True:
        return None

    repo = (payload.get("repository") or {}).get("full_name")
    author = (pr.get("user") or {}).get("login")
    return {
        "repo": repo,
        "number": pr.get("number") or payload.get("number"),
        "title": (pr.get("title") or "").strip(),
        "body": (pr.get("body") or "").strip(),
        "author_login": author,
        "merged_at": normalise_timestamp(pr.get("merged_at")),
        "url": pr.get("html_url"),
    }


def normalise_timestamp(value: str | None) -> str:
    """ISO-8601 in UTC. Falls back to 'now' if missing or unreadable."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat()


def parse_zoom_transcript_event(payload: dict) -> dict | None:
    """
    For a recording.transcript_completed event, returns
    {meeting_uuid, topic, download_url}; otherwise None. The message
    carries a link to the transcript file, not the transcript itself.
    """
    if not isinstance(payload, dict) or payload.get("event") != "recording.transcript_completed":
        return None
    obj = ((payload.get("payload") or {}).get("object")) or {}
    meeting_uuid = obj.get("uuid")
    if not meeting_uuid:
        return None
    for recording in obj.get("recording_files") or []:
        if recording.get("file_type") == "TRANSCRIPT" or recording.get("recording_type") == "audio_transcript":
            url = recording.get("download_url")
            if url:
                return {
                    "meeting_uuid": str(meeting_uuid),
                    "topic": (obj.get("topic") or "Zoom meeting").strip(),
                    "download_url": url,
                }
    return None


def parse_vtt(text: str) -> str:
    """
    Zoom transcripts are WebVTT files: a header, then numbered cues with
    time ranges. Keeps only what was said (including 'Name: ...' speaker
    prefixes); drops the header, cue numbers and timestamps.
    """
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit():
            continue
        if line.startswith(("NOTE", "STYLE", "Kind:", "Language:")):
            continue
        lines.append(line)
    return "\n".join(lines)
