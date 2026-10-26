"""
Tests for the automatic-intake path (Vertical 4): signature checks,
payload reading, the Zoom and GitHub webhook endpoints, duplicate
protection, and the event-driven follow-up.

Only the LLM calls and the transcript download are mocked; the
database, embeddings, signature code and the real Trigger 1 / Trigger 2
logic all run for real.
"""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.core.documents as documents_module
from app.core.db import get_connection
from app.core.embeddings import upsert_embedding
from app.integrations import handlers
from app.integrations.identity import is_allowed_download_url
from app.integrations.parsing import (
    normalise_timestamp,
    parse_github_merge,
    parse_vtt,
    parse_zoom_transcript_event,
)
from app.integrations.security import (
    github_signature,
    verify_github_signature,
    verify_zoom_signature,
    zoom_signature,
    zoom_url_validation_response,
)
from app.main import app
from app.verticals.meeting_action_items import followup

client = TestClient(app)

GH_SECRET = "test-github-secret"
ZOOM_SECRET = "test-zoom-secret"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", GH_SECRET)
    monkeypatch.setenv("ZOOM_WEBHOOK_SECRET", ZOOM_SECRET)
    monkeypatch.setenv("ZOOM_DOWNLOAD_HOSTS", "zoom.us,mock-sources")
    monkeypatch.setattr(documents_module, "SUBMITTED_ROOT", tmp_path / "submitted")


# ---------------------------------------------------------------
# helpers
# ---------------------------------------------------------------

def _gh_payload(login="morgan-dev", repo="acme-corp/platform", merged=True, action="closed",
                title="Restore nightly backup job schedule",
                body="Re-enables the nightly backup job removed during the maintenance window.",
                merged_at=None):
    merged_at = merged_at or (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
    return {
        "action": action,
        "number": 42,
        "pull_request": {
            "number": 42, "title": title, "body": body, "merged": merged,
            "merged_at": merged_at if merged else None,
            "html_url": "https://github.com/acme-corp/platform/pull/42",
            "user": {"login": login},
        },
        "repository": {"full_name": repo},
    }


def _post_github(payload, delivery="d-1", event="pull_request", secret=GH_SECRET, signature=None):
    raw = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": signature or github_signature(secret, raw),
        "Content-Type": "application/json",
    }
    return client.post("/integrations/github", content=raw, headers=headers)


def _zoom_payload(uuid="meeting-uuid-1", topic="Customer Escalation Review",
                  url="http://mock-sources:9000/files/abc.vtt", event="recording.transcript_completed"):
    return {
        "event": event,
        "event_ts": int(time.time() * 1000),
        "download_token": "tok-123",
        "payload": {
            "account_id": "acct",
            "object": {
                "uuid": uuid, "id": 1234, "topic": topic,
                "recording_files": [
                    {"file_type": "TRANSCRIPT", "recording_type": "audio_transcript",
                     "file_extension": "VTT", "download_url": url},
                ],
            },
        },
    }


def _post_zoom(payload, secret=ZOOM_SECRET, timestamp=None, signature=None):
    raw = json.dumps(payload).encode()
    ts = timestamp or str(int(time.time()))
    headers = {
        "x-zm-request-timestamp": ts,
        "x-zm-signature": signature or zoom_signature(secret, ts, raw),
        "Content-Type": "application/json",
    }
    return client.post("/integrations/zoom", content=raw, headers=headers)


def _events():
    return client.get("/integrations/events").json()


def _create_open_item(owner="morgan", description="Restore the nightly backup job", deadline="2020-01-01"):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO meetings (meeting_date) VALUES (now()) RETURNING id;")
            meeting_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO action_items (meeting_id, description, owner, deadline) "
                "VALUES (%s, %s, %s, %s) RETURNING id;",
                (meeting_id, description, owner, deadline),
            )
            item_id = str(cur.fetchone()["id"])
        conn.commit()
        return item_id
    finally:
        conn.close()


def _item(item_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM action_items WHERE id = %s;", (item_id,))
            return cur.fetchone()
    finally:
        conn.close()


def _llm_verdict(verdict):
    return lambda messages: {"content": json.dumps({"verdict": verdict, "confidence": 0.9}), "tool_calls": []}


def _count(sql, params=()):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()["n"]
    finally:
        conn.close()


# ---------------------------------------------------------------
# signatures (pure)
# ---------------------------------------------------------------

def test_github_signature_accepts_correct_and_rejects_wrong():
    body = b'{"a": 1}'
    good = github_signature("s3cret", body)
    assert verify_github_signature("s3cret", body, good)
    assert not verify_github_signature("other", body, good)
    assert not verify_github_signature("s3cret", b'{"a": 2}', good)
    assert not verify_github_signature("s3cret", body, None)
    assert not verify_github_signature("s3cret", body, "sha256=")
    assert not verify_github_signature("", body, good)


def test_zoom_signature_accepts_correct_and_rejects_tampering_and_replay():
    body = b'{"event": "x"}'
    now = 1_800_000_000
    ts = str(now)
    good = zoom_signature("s3cret", ts, body)
    assert verify_zoom_signature("s3cret", body, good, ts, now=now)
    assert not verify_zoom_signature("s3cret", b'{"event": "y"}', good, ts, now=now)
    assert not verify_zoom_signature("wrong", body, good, ts, now=now)
    # replay: a correctly signed message from 10 minutes ago is refused
    assert not verify_zoom_signature("s3cret", body, good, ts, now=now + 600)
    assert not verify_zoom_signature("s3cret", body, good, "not-a-number", now=now)
    assert not verify_zoom_signature("s3cret", body, None, ts, now=now)


def test_zoom_url_validation_response_is_hmac_of_plain_token():
    result = zoom_url_validation_response("s3cret", "abc")
    assert result["plainToken"] == "abc"
    assert len(result["encryptedToken"]) == 64


# ---------------------------------------------------------------
# parsing / identity (pure)
# ---------------------------------------------------------------

def test_parse_github_merge_only_for_merged_pull_requests():
    assert parse_github_merge("pull_request", _gh_payload())["author_login"] == "morgan-dev"
    assert parse_github_merge("pull_request", _gh_payload(merged=False)) is None
    assert parse_github_merge("pull_request", _gh_payload(action="opened")) is None
    assert parse_github_merge("push", _gh_payload()) is None
    assert parse_github_merge("pull_request", "garbage") is None


def test_normalise_timestamp_handles_z_suffix_and_garbage():
    assert normalise_timestamp("2026-10-26T05:12:03Z").startswith("2026-10-26T05:12:03")
    assert normalise_timestamp("nonsense")  # falls back to now, never raises
    assert normalise_timestamp(None)


def test_parse_zoom_transcript_event_finds_transcript_file():
    parsed = parse_zoom_transcript_event(_zoom_payload())
    assert parsed["meeting_uuid"] == "meeting-uuid-1"
    assert parsed["download_url"].endswith("abc.vtt")
    assert parse_zoom_transcript_event(_zoom_payload(event="meeting.ended")) is None
    no_files = _zoom_payload()
    no_files["payload"]["object"]["recording_files"] = []
    assert parse_zoom_transcript_event(no_files) is None


def test_parse_vtt_keeps_speech_and_drops_timestamps():
    vtt = "WEBVTT\n\n1\n00:00:00.000 --> 00:00:05.000\nMorgan: I will restore the backup job today.\n\n2\n00:00:05.000 --> 00:00:09.000\nJamie: Noted.\n"
    assert parse_vtt(vtt) == "Morgan: I will restore the backup job today.\nJamie: Noted."


def test_download_host_allow_list(monkeypatch):
    monkeypatch.setenv("ZOOM_DOWNLOAD_HOSTS", "zoom.us,mock-sources")
    assert is_allowed_download_url("https://us02web.zoom.us/rec/download/x")
    assert is_allowed_download_url("http://mock-sources:9000/files/a.vtt")
    assert not is_allowed_download_url("http://evil.example.com/a.vtt")
    assert not is_allowed_download_url("http://zoom.us.evil.com/a.vtt")
    assert not is_allowed_download_url("file:///etc/passwd")
    assert not is_allowed_download_url(None)


# ---------------------------------------------------------------
# GitHub endpoint
# ---------------------------------------------------------------

def test_github_fails_closed_when_secret_not_configured(monkeypatch):
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET")
    assert _post_github(_gh_payload()).status_code == 503


def test_github_bad_signature_is_rejected_and_recorded():
    response = _post_github(_gh_payload(), signature="sha256=" + "0" * 64)
    assert response.status_code == 401
    events = _events()
    assert events[0]["status"] == "rejected"
    assert _count("SELECT count(*) AS n FROM embeddings WHERE source_type = 'owner_activity';") == 0


def test_github_merge_stores_evidence_and_resolves_matching_item(monkeypatch):
    item_id = _create_open_item()
    monkeypatch.setattr(followup, "call_llm", _llm_verdict("done"))

    response = _post_github(_gh_payload())
    assert response.status_code == 202

    assert _item(item_id)["status"] == "resolved"
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT metadata FROM embeddings WHERE source_type = 'owner_activity';")
            meta = cur.fetchone()["metadata"]
            cur.execute("SELECT trigger_type, status FROM agent_runs WHERE vertical = 'meeting_action_items';")
            run = cur.fetchone()
    finally:
        conn.close()
    assert meta["owner"] == "morgan" and meta["source"] == "github" and "occurred_at" in meta
    assert run["trigger_type"] == "integration_event" and run["status"] == "completed"

    event = _events()[0]
    assert event["status"] == "processed" and event["owner"] == "morgan"
    assert "resolved 1 item" in event["summary"] and event["run_id"]


def test_github_merge_leaves_item_open_when_verdict_is_not_done(monkeypatch):
    item_id = _create_open_item()
    monkeypatch.setattr(followup, "call_llm", _llm_verdict("in_progress"))
    assert _post_github(_gh_payload()).status_code == 202
    assert _item(item_id)["status"] == "open"
    # resolve-only: no nudge, no escalation, and no run logged for a non-resolution
    assert _item(item_id)["nudge_count"] == 0
    assert _count("SELECT count(*) AS n FROM agent_runs;") == 0
    assert "no open item matched" in _events()[0]["summary"]


def test_github_only_checks_the_merging_owners_items(monkeypatch):
    morgan_item = _create_open_item(owner="morgan")
    jamie_item = _create_open_item(owner="jamie", description="Send revised pricing page copy")
    monkeypatch.setattr(followup, "call_llm", _llm_verdict("done"))
    _post_github(_gh_payload(login="morgan-dev"))
    assert _item(morgan_item)["status"] == "resolved"
    assert _item(jamie_item)["status"] == "open"


def test_github_same_delivery_twice_is_processed_once(monkeypatch):
    _create_open_item()
    monkeypatch.setattr(followup, "call_llm", _llm_verdict("done"))
    payload = _gh_payload()
    first = _post_github(payload, delivery="same-id")
    second = _post_github(payload, delivery="same-id")
    assert first.status_code == 202
    assert second.json() == {"status": "duplicate"}
    assert _count("SELECT count(*) AS n FROM embeddings WHERE source_type = 'owner_activity';") == 1
    statuses = sorted(e["status"] for e in _events())
    assert statuses == ["duplicate", "processed"]


@pytest.mark.parametrize("payload,reason_fragment", [
    (_gh_payload(merged=False), "Not a commit or a merged pull request"),
    (_gh_payload(repo="someone/else"), "not on the allowed list"),
    (_gh_payload(login="stranger"), "not mapped"),
])
def test_github_ignores_events_that_are_not_valid_evidence(payload, reason_fragment):
    response = _post_github(payload)
    assert response.status_code == 200 and response.json()["status"] == "ignored"
    assert reason_fragment in _events()[0]["summary"]
    assert _count("SELECT count(*) AS n FROM embeddings WHERE source_type = 'owner_activity';") == 0


def test_github_ping_is_answered():
    raw = b"{}"
    response = client.post(
        "/integrations/github", content=raw,
        headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "p",
                 "X-Hub-Signature-256": github_signature(GH_SECRET, raw)},
    )
    assert response.json() == {"status": "pong"}


# ---------------------------------------------------------------
# Zoom endpoint
# ---------------------------------------------------------------

_TRANSCRIPT = (
    "WEBVTT\n\n1\n00:00:00.000 --> 00:00:09.000\n"
    "Morgan will restore the nightly backup job today.\n\n"
    "2\n00:00:09.000 --> 00:00:15.000\nJamie will send the revised pricing page copy to marketing today.\n"
)


def _mock_extraction(monkeypatch, items):
    monkeypatch.setattr(handlers, "fetch_transcript", lambda url, token: _TRANSCRIPT)
    monkeypatch.setattr(
        "app.verticals.meeting_action_items.graph.call_llm",
        lambda messages: {"content": json.dumps(items), "tool_calls": []},
    )


def test_zoom_url_validation_handshake_needs_no_signature():
    response = client.post(
        "/integrations/zoom",
        json={"event": "endpoint.url_validation", "payload": {"plainToken": "xyz"}},
    )
    assert response.status_code == 200
    assert response.json()["plainToken"] == "xyz" and response.json()["encryptedToken"]


def test_zoom_bad_signature_and_stale_timestamp_are_rejected():
    assert _post_zoom(_zoom_payload(), signature="v0=" + "0" * 64).status_code == 401
    old = str(int(time.time()) - 3600)
    assert _post_zoom(_zoom_payload(), timestamp=old).status_code == 401
    assert [e["status"] for e in _events()] == ["rejected", "rejected"]
    assert _count("SELECT count(*) AS n FROM agent_runs;") == 0


def test_zoom_transcript_creates_action_items_with_no_upload(monkeypatch):
    _mock_extraction(monkeypatch, [
        {"description": "Restore the nightly backup job", "owner": "morgan", "deadline": None},
        {"description": "Send the revised pricing page copy to marketing", "owner": "jamie", "deadline": None},
    ])
    response = _post_zoom(_zoom_payload())
    assert response.status_code == 202

    assert _count("SELECT count(*) AS n FROM action_items;") == 2
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT trigger_type, status, input_document_id FROM agent_runs;")
            run = cur.fetchone()
            cur.execute("SELECT source FROM documents WHERE id = %s;", (run["input_document_id"],))
            doc = cur.fetchone()
    finally:
        conn.close()
    assert run["trigger_type"] == "integration_event" and run["status"] == "completed"
    assert doc["source"] == "zoom"

    event = _events()[0]
    assert event["status"] == "processed" and event["run_id"]
    assert "2 action item(s) created" in event["summary"]


def test_zoom_wrong_document_is_sent_for_review(monkeypatch):
    _mock_extraction(monkeypatch, [])
    assert _post_zoom(_zoom_payload()).status_code == 202
    assert _count("SELECT count(*) AS n FROM action_items;") == 0
    event = _events()[0]
    assert event["status"] == "needs_review" and "sent for review" in event["summary"]


def test_zoom_same_meeting_twice_is_processed_once(monkeypatch):
    _mock_extraction(monkeypatch, [{"description": "Restore the nightly backup job", "owner": "morgan", "deadline": None}])
    payload = _zoom_payload()
    assert _post_zoom(payload).status_code == 202
    assert _post_zoom(payload).json() == {"status": "duplicate"}
    assert _count("SELECT count(*) AS n FROM action_items;") == 1
    assert _count("SELECT count(*) AS n FROM agent_runs;") == 1


def test_zoom_transcript_link_to_unlisted_host_is_ignored(monkeypatch):
    called = []
    monkeypatch.setattr(handlers, "fetch_transcript", lambda *a: called.append(a) or _TRANSCRIPT)
    response = _post_zoom(_zoom_payload(url="http://evil.example.com/a.vtt"))
    assert response.json()["status"] == "ignored"
    assert called == []


def test_zoom_download_failure_is_recorded_not_raised(monkeypatch):
    def boom(url, token):
        raise handlers.TranscriptDownloadError("file host answered 404")
    monkeypatch.setattr(handlers, "fetch_transcript", boom)
    assert _post_zoom(_zoom_payload()).status_code == 202
    event = _events()[0]
    assert event["status"] == "failed" and "404" in event["summary"]
    assert _count("SELECT count(*) AS n FROM agent_runs;") == 0


def test_zoom_other_event_types_are_ignored():
    response = _post_zoom(_zoom_payload(event="meeting.ended"))
    assert response.json()["status"] == "ignored"


# ---------------------------------------------------------------
# follow-up evidence timing (the same-day fix)
# ---------------------------------------------------------------

def _evidence(owner, text, occurred_at=None, date=None):
    meta = {"owner": owner}
    if occurred_at:
        meta["occurred_at"] = occurred_at.isoformat()
        meta["date"] = occurred_at.date().isoformat()
    if date:
        meta["date"] = date
    upsert_embedding("meeting_action_items", "owner_activity", text, metadata=meta)


def test_same_day_evidence_after_item_creation_counts():
    item = _item(_create_open_item())
    _evidence("morgan", "Morgan restored the nightly backup job.",
              occurred_at=item["created_at"] + timedelta(minutes=5))
    results = followup._search_owner_activity("morgan", "Restore the nightly backup job", item["created_at"])
    assert len(results) == 1


def test_evidence_from_before_the_item_existed_does_not_count():
    item = _item(_create_open_item())
    _evidence("morgan", "Morgan restored the nightly backup job.",
              occurred_at=item["created_at"] - timedelta(hours=2))
    assert followup._search_owner_activity("morgan", "Restore the nightly backup job", item["created_at"]) == []


def test_date_only_evidence_keeps_the_old_rule():
    item = _item(_create_open_item())
    today = item["created_at"].date()
    _evidence("morgan", "Seeded activity dated today.", date=today.isoformat())
    _evidence("morgan", "Seeded activity dated tomorrow.", date=(today + timedelta(days=1)).isoformat())
    results = followup._search_owner_activity("morgan", "activity", item["created_at"])
    assert [r["chunk_text"] for r in results] == ["Seeded activity dated tomorrow."]


def test_resolve_only_check_never_nudges_or_escalates(monkeypatch):
    item_id = _create_open_item()
    monkeypatch.setattr(followup, "call_llm", _llm_verdict("no_evidence"))
    assert followup.check_owner_for_new_evidence("morgan") == []
    item = _item(item_id)
    assert item["status"] == "open" and item["nudge_count"] == 0 and item["escalated"] is False
    assert _count("SELECT count(*) AS n FROM agent_runs;") == 0
    assert _count("SELECT count(*) AS n FROM notifications;") == 0


# ---------------------------------------------------------------
# Live Feed data
# ---------------------------------------------------------------

def test_events_endpoint_lists_newest_first_and_filters_by_source(monkeypatch):
    _post_github(_gh_payload(merged=False), delivery="a")
    _post_zoom(_zoom_payload(event="meeting.ended"))
    all_events = _events()
    assert [e["source"] for e in all_events] == ["zoom", "github"]
    only_github = client.get("/integrations/events?source=github").json()
    assert [e["source"] for e in only_github] == ["github"]
