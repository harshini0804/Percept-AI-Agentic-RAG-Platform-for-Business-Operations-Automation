"""
Tests for live GitHub evidence from commits (push events), the
configurable account/repo settings, and the public-exposure guard.

Messages are built in GitHub's real "push" shape and signed the way
GitHub signs them. Only the LLM verdict is mocked.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_connection
from app.integrations.identity import (
    allowed_github_repos,
    github_login_map,
    is_allowed_repo,
    owner_for_github_login,
)
from app.integrations.parsing import parse_github_push
from app.integrations.security import github_signature
from app.main import app
from app.verticals.meeting_action_items import followup

client = TestClient(app)

SECRET = "push-test-secret"
REPO = "harshini0825/morgan-percept-ai-demo"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("GITHUB_LOGIN_MAP", "harshini0825=morgan")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", REPO)


def _later():
    return (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()


def _push(message="Restore nightly backup job schedule", login="harshini0825", branch="main",
          repo=REPO, files=("backup_job.md",), timestamp=None, author_has_username=True,
          commits=None, deleted=False, sender="harshini0825"):
    if commits is None:
        author = {"name": "Demo", "email": "d@example.com"}
        if author_has_username:
            author["username"] = login
        commits = [{
            "id": "abc123", "message": message, "timestamp": timestamp or _later(),
            "url": f"https://github.com/{repo}/commit/abc123",
            "author": author, "committer": dict(author),
            "added": list(files), "modified": [], "removed": [],
        }]
    return {
        "ref": f"refs/heads/{branch}",
        "deleted": deleted,
        "repository": {"full_name": repo, "default_branch": "main"},
        "sender": {"login": sender},
        "pusher": {"name": sender},
        "commits": commits,
    }


def _post(payload, delivery="g-1", event="push", host=None, signature=None):
    raw = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": signature or github_signature(SECRET, raw),
        "Content-Type": "application/json",
    }
    if host:
        headers["Host"] = host
    return client.post("/integrations/github", content=raw, headers=headers)


def _events():
    return client.get("/integrations/events").json()


def _open_item(owner="morgan", description="Restore the nightly backup job"):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO meetings (meeting_date) VALUES (now()) RETURNING id;")
            meeting_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO action_items (meeting_id, description, owner, deadline) "
                "VALUES (%s, %s, %s, '2020-01-01') RETURNING id;", (meeting_id, description, owner),
            )
            item_id = str(cur.fetchone()["id"])
        conn.commit()
        return item_id
    finally:
        conn.close()


def _status(item_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM action_items WHERE id = %s;", (item_id,))
            return cur.fetchone()["status"]
    finally:
        conn.close()


def _evidence_rows():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT chunk_text, metadata FROM embeddings WHERE source_type = 'owner_activity' ORDER BY created_at;")
            return cur.fetchall()
    finally:
        conn.close()


def _verdict(v):
    return lambda messages: {"content": json.dumps({"verdict": v, "confidence": 0.9}), "tool_calls": []}


# ---------------------------------------------------------------
# settings
# ---------------------------------------------------------------

def test_settings_add_to_the_built_in_demo_values(monkeypatch):
    monkeypatch.setenv("GITHUB_LOGIN_MAP", "Harshini0825=Morgan, other-dev = alex ,broken")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "Harshini0825/Some-Repo")
    assert owner_for_github_login("HARSHINI0825") == "morgan"
    assert owner_for_github_login("other-dev") == "alex"
    assert owner_for_github_login("alex-dev") == "alex"          # built-in demo value still there
    assert owner_for_github_login("stranger") is None
    assert is_allowed_repo("harshini0825/some-repo")
    assert is_allowed_repo("acme-corp/platform")                  # built-in demo value still there
    assert not is_allowed_repo("someone/else")
    assert "broken" not in github_login_map()


def test_unset_settings_leave_only_the_defaults(monkeypatch):
    monkeypatch.delenv("GITHUB_LOGIN_MAP")
    monkeypatch.delenv("GITHUB_ALLOWED_REPOS")
    assert allowed_github_repos() == {"acme-corp/platform"}
    assert owner_for_github_login("harshini0825") is None


# ---------------------------------------------------------------
# parsing
# ---------------------------------------------------------------

def test_parse_push_reads_commits_files_and_branch():
    parsed = parse_github_push("push", _push(files=("a", "b", "c", "d", "e", "f", "g")))
    assert parsed["repo"] == REPO and parsed["branch"] == "main" and parsed["is_default_branch"]
    commit = parsed["commits"][0]
    assert commit["author_login"] == "harshini0825"
    assert commit["files"] == ["a", "b", "c", "d", "e"]          # capped at five
    assert not commit["is_merge"]


def test_parse_push_nothing_to_read():
    assert parse_github_push("pull_request", _push()) is None
    assert parse_github_push("push", _push(deleted=True)) is None
    assert parse_github_push("push", _push(commits=[])) is None
    assert parse_github_push("push", "garbage") is None


def test_parse_push_one_file_or_none_is_enough():
    assert parse_github_push("push", _push(files=("only.md",)))["commits"][0]["files"] == ["only.md"]
    assert parse_github_push("push", _push(files=()))["commits"][0]["files"] == []


def test_parse_push_falls_back_to_the_person_who_pushed():
    parsed = parse_github_push("push", _push(author_has_username=False, sender="harshini0825"))
    assert parsed["commits"][0]["author_login"] == "harshini0825"


def test_parse_push_timestamp_with_offset_is_converted_to_utc():
    parsed = parse_github_push("push", _push(timestamp="2026-10-26T10:00:00+05:30"))
    assert parsed["commits"][0]["timestamp"].startswith("2026-10-26T04:30:00")


# ---------------------------------------------------------------
# endpoint
# ---------------------------------------------------------------

def test_commit_resolves_the_matching_item(monkeypatch):
    item = _open_item()
    monkeypatch.setattr(followup, "call_llm", _verdict("done"))
    response = _post(_push())
    assert response.status_code == 202
    assert _status(item) == "resolved"

    evidence = _evidence_rows()
    assert len(evidence) == 1
    assert 'Morgan committed to morgan-percept-ai-demo: "Restore nightly backup job schedule".' in evidence[0]["chunk_text"]
    assert "Changed files: backup_job.md." in evidence[0]["chunk_text"]
    assert evidence[0]["metadata"]["owner"] == "morgan" and evidence[0]["metadata"]["source"] == "github"

    event = _events()[0]
    assert event["status"] == "processed" and event["owner"] == "morgan" and event["run_id"]
    assert "resolved 1 item(s)" in event["summary"]


def test_commit_with_no_changed_files_still_works(monkeypatch):
    item = _open_item()
    monkeypatch.setattr(followup, "call_llm", _verdict("done"))
    assert _post(_push(files=())).status_code == 202
    assert _status(item) == "resolved"
    assert "Changed files" not in _evidence_rows()[0]["chunk_text"]


def test_commit_pushed_from_a_laptop_with_unlinked_email_uses_the_pusher(monkeypatch):
    item = _open_item()
    monkeypatch.setattr(followup, "call_llm", _verdict("done"))
    assert _post(_push(author_has_username=False)).status_code == 202
    assert _status(item) == "resolved"


def test_commit_that_does_not_complete_the_item_leaves_it_open(monkeypatch):
    item = _open_item()
    monkeypatch.setattr(followup, "call_llm", _verdict("no_evidence"))
    _post(_push(message="Fix typo in readme"))
    assert _status(item) == "open"
    assert "no open item matched" in _events()[0]["summary"]


def test_several_commits_by_one_author_are_all_stored_and_checked_once(monkeypatch):
    item = _open_item()
    calls = []
    def counting(messages):
        calls.append(1)
        return {"content": json.dumps({"verdict": "done", "confidence": 0.9}), "tool_calls": []}
    monkeypatch.setattr(followup, "call_llm", counting)
    commits = [
        {"message": f"Restore backup step {i}", "timestamp": _later(), "author": {"username": "harshini0825"},
         "added": [], "modified": ["x"], "removed": []} for i in range(3)
    ]
    assert _post(_push(commits=commits)).status_code == 202
    assert len(_evidence_rows()) == 3
    assert len(calls) == 1
    assert _status(item) == "resolved"
    assert "3 commits" in _events()[0]["summary"]


@pytest.mark.parametrize("payload,fragment", [
    (_push(branch="feature-x"), 'branch "feature-x" are ignored'),
    (_push(repo="someone/else"), "not on the allowed list"),
    (_push(login="stranger", sender="stranger"), "No commit by a GitHub account"),
    (_push(message="Merge pull request #4 from x/y"), "Only merge commits"),
    (_push(commits=[]), "no new commits"),
])
def test_commits_that_should_not_count_are_ignored_and_recorded(payload, fragment):
    response = _post(payload)
    assert response.status_code == 200 and response.json()["status"] == "ignored"
    assert fragment in _events()[0]["summary"]
    assert _evidence_rows() == []


def test_same_push_delivered_twice_is_processed_once(monkeypatch):
    _open_item()
    monkeypatch.setattr(followup, "call_llm", _verdict("done"))
    payload = _push()
    assert _post(payload, delivery="redeliver-me").status_code == 202
    assert _post(payload, delivery="redeliver-me").json() == {"status": "duplicate"}
    assert len(_evidence_rows()) == 1
    assert sorted(e["status"] for e in _events()) == ["duplicate", "processed"]


def test_forged_push_is_rejected_and_recorded():
    response = _post(_push(), signature="sha256=" + "0" * 64)
    assert response.status_code == 401
    assert _events()[0]["status"] == "rejected"
    assert _evidence_rows() == []


def test_commit_before_the_item_existed_does_not_resolve_it(monkeypatch):
    item = _open_item()
    # the scripted judge says "done" only if it is actually shown the commit
    def judge(messages):
        shown = "committed to" in messages[-1]["content"]
        return {"content": json.dumps({"verdict": "done" if shown else "no_evidence", "confidence": 0.9}), "tool_calls": []}
    monkeypatch.setattr(followup, "call_llm", judge)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    _post(_push(timestamp=old))
    assert _status(item) == "open"


# ---------------------------------------------------------------
# public-exposure guard
# ---------------------------------------------------------------

def test_public_host_can_only_reach_the_webhooks(monkeypatch):
    public = "random-words.trycloudflare.com"
    assert client.get("/health", headers={"Host": public}).status_code == 404
    assert client.get("/integrations/events", headers={"Host": public}).status_code == 404
    assert client.post("/agent-runs", json={"vertical": "x", "input_text": "y"}, headers={"Host": public}).status_code == 404
    # a webhook still gets in — and is judged by its signature
    assert _post(_push(), host=public, signature="sha256=" + "0" * 64).status_code == 401
    monkeypatch.setattr(followup, "call_llm", _verdict("done"))
    assert _post(_push(), host=public).status_code in (200, 202)


def test_local_hosts_are_unaffected():
    for host in ("localhost:8000", "127.0.0.1:8000", "backend:8000", "testserver"):
        assert client.get("/health", headers={"Host": host}).status_code == 200


def test_local_hosts_setting_extends_the_list(monkeypatch):
    monkeypatch.setenv("LOCAL_HOSTS", "my-laptop.local")
    assert client.get("/health", headers={"Host": "my-laptop.local:8000"}).status_code == 200
