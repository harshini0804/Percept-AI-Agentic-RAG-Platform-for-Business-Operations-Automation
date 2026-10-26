"""
Pretend senders for the automatic-intake demo.

This service plays the part of Zoom and GitHub. It sends the platform
the same kind of signed message the real services send (same headers,
same payload shape, same signing method), so the platform's receiving
side is exercised exactly as it would be in production.

What is NOT real: no Zoom or GitHub account is involved, and the
"meeting" and "pull request" are scripted. The meeting transcript comes
from the existing demo input file, so the story matches the demo README.

Open http://localhost:9000 for the control page.
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000").rstrip("/")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://mock-sources:9000").rstrip("/")
GITHUB_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
ZOOM_SECRET = os.getenv("ZOOM_WEBHOOK_SECRET", "")
DEMO_INPUTS = Path(os.getenv("DEMO_INPUTS_DIR", "/demo_inputs"))

SCENARIOS = {
    # Morgan and Jamie each own an item due today; no seeded evidence matches.
    "backup": "demo_4_due_today_no_evidence.txt",
    # A contract instead of a meeting: the platform should refuse it.
    "wrong-document": "demo_7_wrong_document_contract.txt",
}

PEOPLE = {
    "morgan": {
        "login": "morgan-dev",
        "title": "Restore nightly backup job schedule",
        "body": "Re-enables the nightly backup job that stopped after Tuesday's maintenance window and adds an alert if it fails to run.",
    },
    "jamie": {
        "login": "jamie-dev",
        "title": "Update pricing page plan names",
        "body": "Replaces the old plan names on the pricing page with the new ones and updates the supporting copy.",
    },
}

app = FastAPI(title="Pretend Zoom + GitHub senders")

_files: dict[str, dict] = {}          # file id -> {"vtt": str, "token": str}
_last: dict[str, dict] = {}           # source -> last request sent (for the duplicate button)
_log: list[dict] = []


# ---------------------------------------------------------------
# signing (same schemes the real services use)
# ---------------------------------------------------------------

def _hex_hmac(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _explain(response: httpx.Response | None) -> str:
    """The platform's answer in plain words, for the control page."""
    if response is None:
        return ""
    body = _safe_json(response)
    status = body.get("status") if isinstance(body, dict) else None
    if response.status_code == 202:
        return "Accepted — the platform is processing it."
    if response.status_code == 200 and status == "duplicate":
        return "Already received — not processed again."
    if response.status_code == 200 and status == "ignored":
        return f"Ignored: {body.get('reason', 'not something the platform acts on')}"
    if response.status_code == 401:
        return "Rejected: the signature did not match, so nothing was read."
    if response.status_code == 503:
        return "Platform is not configured with a secret, so it refuses everything."
    return "Unexpected answer from the platform."


def _record(step: str, response: httpx.Response | None, note: str = "") -> dict:
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "step": step,
        "platform_answered": None if response is None else response.status_code,
        "platform_said": None if response is None else _safe_json(response),
        "meaning": _explain(response),
        "note": note,
    }
    _log.append(entry)
    del _log[:-40]
    return entry


def _safe_json(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return response.text[:200]


def _send(source: str, path: str, raw: bytes, headers: dict, remember: bool = True) -> httpx.Response:
    # Only genuine messages are remembered for the "send it again" button;
    # otherwise clicking the forged-message button first would make "again"
    # replay the forged message instead of a real one.
    if remember:
        _last[source] = {"path": path, "raw": raw, "headers": headers}
    return httpx.post(f"{BACKEND_URL}{path}", content=raw, headers=headers, timeout=30)


# ---------------------------------------------------------------
# Zoom
# ---------------------------------------------------------------

def _to_vtt(text: str) -> tuple[str, str]:
    """Turns a demo transcript into a Zoom-style WebVTT file. Returns (topic, vtt)."""
    paragraphs = [p.strip() for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
    topic = paragraphs[0].split("\n")[0].replace(" — Demo", "").strip()
    cues = ["WEBVTT", ""]
    for i, paragraph in enumerate(paragraphs[1:] if len(paragraphs) > 1 else paragraphs, start=1):
        start, end = (i - 1) * 20, i * 20
        cues += [str(i), f"00:{start // 60:02d}:{start % 60:02d}.000 --> 00:{end // 60:02d}:{end % 60:02d}.000",
                 paragraph.replace("\n", " "), ""]
    return topic, "\n".join(cues)


def _send_zoom_meeting(scenario: str) -> dict:
    if not ZOOM_SECRET:
        raise HTTPException(500, "ZOOM_WEBHOOK_SECRET is not set for the pretend sender.")
    source_file = DEMO_INPUTS / SCENARIOS[scenario]
    if not source_file.exists():
        raise HTTPException(500, f"Demo transcript not found: {source_file}")
    topic, vtt = _to_vtt(source_file.read_text(encoding="utf-8"))

    file_id, token = uuid.uuid4().hex, uuid.uuid4().hex
    _files[file_id] = {"vtt": vtt, "token": token}
    payload = {
        "event": "recording.transcript_completed",
        "event_ts": int(time.time() * 1000),
        "download_token": token,
        "payload": {
            "account_id": "demo-account",
            "object": {
                "uuid": uuid.uuid4().hex,
                "id": 987654321,
                "topic": topic,
                "host_id": "demo-host",
                "recording_files": [{
                    "id": file_id,
                    "file_type": "TRANSCRIPT",
                    "file_extension": "VTT",
                    "recording_type": "audio_transcript",
                    "download_url": f"{PUBLIC_URL}/files/{file_id}.vtt",
                }],
            },
        },
    }
    raw = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "x-zm-request-timestamp": timestamp,
        "x-zm-signature": "v0=" + _hex_hmac(ZOOM_SECRET, f"v0:{timestamp}:".encode() + raw),
    }
    response = _send("zoom", "/integrations/zoom", raw, headers)
    return _record(f'Zoom: transcript ready for "{topic}"', response)


@app.get("/files/{file_id}.vtt", response_class=PlainTextResponse)
def download_transcript(file_id: str, authorization: str | None = Header(default=None)):
    """The file host. Like Zoom's, it only hands the file to a caller holding the download token."""
    entry = _files.get(file_id)
    if entry is None:
        raise HTTPException(404, "No such file.")
    if authorization != f"Bearer {entry['token']}":
        raise HTTPException(401, "Missing or wrong download token.")
    return entry["vtt"]


# ---------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------

def _github_headers(raw: bytes, delivery: str, secret: str) -> dict:
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": "sha256=" + _hex_hmac(secret, raw),
    }


def _github_payload(person: str) -> dict:
    info = PEOPLE[person]
    merged_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "action": "closed",
        "number": 101 if person == "morgan" else 102,
        "pull_request": {
            "number": 101 if person == "morgan" else 102,
            "title": info["title"],
            "body": info["body"],
            "merged": True,
            "merged_at": merged_at,
            "html_url": "https://github.com/acme-corp/platform/pull/101",
            "user": {"login": info["login"]},
        },
        "repository": {"full_name": "acme-corp/platform"},
    }


def _send_github_merge(person: str, secret: str, label: str, remember: bool = True) -> dict:
    raw = json.dumps(_github_payload(person)).encode()
    response = _send(
        "github", "/integrations/github", raw, _github_headers(raw, str(uuid.uuid4()), secret),
        remember=remember,
    )
    return _record(label, response)


# ---------------------------------------------------------------
# control endpoints
# ---------------------------------------------------------------

@app.post("/play/zoom-meeting")
def play_zoom_meeting(scenario: str = "backup"):
    if scenario not in SCENARIOS:
        raise HTTPException(404, f"Unknown scenario. Choose from {sorted(SCENARIOS)}.")
    return _send_zoom_meeting(scenario)


@app.post("/play/github-merge/{person}")
def play_github_merge(person: str):
    if person not in PEOPLE:
        raise HTTPException(404, f"Unknown person. Choose from {sorted(PEOPLE)}.")
    if not GITHUB_SECRET:
        raise HTTPException(500, "GITHUB_WEBHOOK_SECRET is not set for the pretend sender.")
    return _send_github_merge(person, GITHUB_SECRET, f"GitHub: {person}'s pull request merged")


@app.post("/play/forged")
def play_forged():
    """A message signed with the WRONG secret — what an outsider without the shared secret would send."""
    return _send_github_merge(
        "morgan", "not-the-real-secret", "FORGED GitHub message (wrong secret)", remember=False
    )


@app.post("/play/duplicate/{source}")
def play_duplicate(source: str):
    """Re-sends the most recent message of that source byte-for-byte, as providers do after a timeout."""
    last = _last.get(source)
    if last is None:
        raise HTTPException(404, f"No genuine {source} message has been sent yet — send one first.")
    response = httpx.post(f"{BACKEND_URL}{last['path']}", content=last["raw"], headers=last["headers"], timeout=30)
    return _record(f"Same {source} message sent AGAIN", response)


@app.post("/play/full")
def play_full():
    """Whole story: meeting ends -> wait for the platform to read it -> Morgan's pull request merges."""
    steps = [_send_zoom_meeting("backup")]
    deadline = time.time() + 120
    finished = False
    while time.time() < deadline and not finished:
        time.sleep(2)
        events = httpx.get(f"{BACKEND_URL}/integrations/events?source=zoom&limit=1", timeout=15).json()
        finished = bool(events) and events[0]["status"] not in ("received",)
    if not finished:
        steps.append(_record("Waited for the platform to read the meeting", None, "Timed out after 2 minutes."))
        return steps
    time.sleep(2)
    steps.append(_send_github_merge("morgan", GITHUB_SECRET, "GitHub: morgan's pull request merged"))
    return steps


@app.get("/log")
def get_log():
    return list(reversed(_log))


@app.get("/", response_class=HTMLResponse)
def control_page():
    return CONTROL_PAGE


CONTROL_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pretend Zoom + GitHub</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#0f172a;background:#f8fafc}
 h1{font-size:1.4rem;margin-bottom:.2rem} .sub{color:#475569;margin-top:0}
 .card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:1rem;margin:1rem 0}
 button{background:#1e293b;color:#fff;border:0;border-radius:6px;padding:.6rem 1rem;margin:.25rem .25rem .25rem 0;cursor:pointer;font-size:.95rem}
 button.warn{background:#9a3412} button.alt{background:#475569} button:disabled{opacity:.5;cursor:wait}
 .note{color:#475569;font-size:.9rem;margin:.2rem 0 .6rem}
 table{width:100%;border-collapse:collapse;font-size:.88rem} td,th{text-align:left;padding:.35rem;border-bottom:1px solid #e2e8f0;vertical-align:top}
 code{background:#f1f5f9;padding:.1rem .3rem;border-radius:4px}
</style></head><body>
<h1>Pretend Zoom + GitHub</h1>
<p class="sub">These buttons play the part of Zoom and GitHub. Each one sends the platform a signed message in the same format the real service uses. Nothing here is a real account.</p>
<div class="card">
 <b>1. A meeting ends</b>
 <p class="note">Zoom tells the platform a transcript is ready. The platform downloads it and finds the action items. No file is uploaded.</p>
 <button onclick="play('/play/zoom-meeting?scenario=backup', this)">Meeting ends (Customer Escalation Review)</button>
 <button class="alt" onclick="play('/play/zoom-meeting?scenario=wrong-document', this)">Meeting ends — but the file is a contract</button>
</div>
<div class="card">
 <b>2. Work gets done</b>
 <p class="note">GitHub tells the platform a pull request was merged. If it matches an open item for that person, the item is resolved.</p>
 <button onclick="play('/play/github-merge/morgan', this)">Morgan's pull request is merged</button>
 <button class="alt" onclick="play('/play/github-merge/jamie', this)">Jamie's pull request is merged</button>
</div>
<div class="card">
 <b>3. Safety checks</b>
 <p class="note">What the platform does with messages it should not trust or should not repeat.</p>
 <button class="warn" onclick="play('/play/forged', this)">Send a forged message (wrong secret)</button>
 <button class="alt" onclick="play('/play/duplicate/github', this)">Send the last GitHub message again</button>
 <button class="alt" onclick="play('/play/duplicate/zoom', this)">Send the last Zoom message again</button>
</div>
<div class="card">
 <b>Whole story, hands-free</b>
 <p class="note">Meeting ends, then Morgan's pull request is merged once the platform has read the meeting (about 30 seconds).</p>
 <button onclick="play('/play/full', this)">Play the whole story</button>
</div>
<div class="card"><b>What was sent</b>
 <table><thead><tr><th>Time</th><th>Sent</th><th>Platform answered</th></tr></thead><tbody id="log"></tbody></table>
 <p class="note">See the results on the platform: <a href="http://localhost:5173/live-feed" target="_blank">Live Feed</a> (every message and what the platform decided) and <a href="http://localhost:5173/tracker" target="_blank">Tracker</a>.</p>
</div>
<script>
async function refresh(){
  const rows = await (await fetch('/log')).json();
  document.getElementById('log').innerHTML = rows.map(r =>
    `<tr><td>${r.time}</td><td>${r.step}</td><td><code>${r.platform_answered ?? '—'}</code> ${r.meaning || ''} ${r.note || ''}</td></tr>`).join('');
}
async function play(path, btn){
  btn.disabled = true;
  try { await fetch(path, {method:'POST'}); } finally { btn.disabled = false; refresh(); }
}
refresh(); setInterval(refresh, 3000);
</script></body></html>"""
