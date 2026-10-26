# Automatic intake for Vertical 4 (Zoom + GitHub)

## What this shows

Meeting transcripts and evidence of finished work reach the platform **without anyone uploading a file**.
Zoom and GitHub each send the platform a message when something happens, and the platform acts on it.

| Message | Sent when | What the platform does |
|---|---|---|
| Zoom: "transcript ready" | A meeting's transcript has been produced | Downloads it, finds the action items (Trigger 1), shows them in the Tracker |
| GitHub: "pull request merged" | Someone merges a pull request | Records it as that person's work; if it completes one of their open items, the item is resolved |

## What is simulated, honestly

- The **platform side is complete**: the two receiving endpoints, signature checks, duplicate protection, name mapping, allow-lists and the Live Feed are real code.
- The **senders are pretend**: a small service (`mock-sources`) plays Zoom and GitHub. It signs and formats its messages exactly as the real services do, but no Zoom or GitHub account is involved.
- Say "integration-ready, with simulated senders". Do **not** say "integrated".

Still needed for a real rollout: register the platform's URLs in a Zoom app and a GitHub webhook; a public HTTPS address; real secrets (replace the demo defaults); a real GitHub-account-to-person mapping (`backend/app/integrations/identity.py`).

## How it protects against unwanted data

This is the answer to "anyone can upload unrelated documents":

1. **Signature**: every message is signed with a secret shared only with the sender. Without it, the message is rejected before anything is read. (GitHub: HMAC-SHA256 of the body. Zoom: HMAC-SHA256 of timestamp + body, and messages older than 5 minutes are refused, so a captured message cannot be replayed.)
2. **Event allow-list**: only "transcript ready" (Zoom) and "pull request merged" (GitHub) are acted on.
3. **Source allow-list**: GitHub merges count only for listed repositories and for accounts mapped to a team member. Zoom transcript links must point at an allowed file host.
4. **Duplicate protection**: a repeated delivery of the same message is recorded and ignored.
5. **Wrong content still caught**: a transcript that is not a meeting goes through the same check as a manual upload and is sent for human review.
6. **Everything is logged**: accepted, ignored, rejected and failed messages all appear in the Live Feed.

## Running the demo

Start everything: `docker-compose up --build -d`, then open **http://localhost:9000** (the pretend senders) and the platform at http://localhost:5173.

Before a rehearsal or the real demo, clear this vertical's data (the command in `backend/demo_inputs/meeting_action_items/README.md`, which also clears the Live Feed) and reseed: `docker exec -it rag_backend python seed.py`. Not needed for ordinary testing.

Suggested order (about 3 minutes):

1. **Platform: Tracker and Live Feed** — show the existing items and an empty (or short) feed. Nothing has been uploaded.
2. **Pretend senders: "Meeting ends (Customer Escalation Review)"**. The Live Feed shows "Transcript received" and, a few seconds later, "Handled". Within a few seconds the Tracker gains two items (Morgan: restore the backup job; Jamie: send the pricing page copy). Open the run: it says "Received automatically".
3. **Pretend senders: "Morgan's pull request is merged"**. The Live Feed shows the merge and which item it resolved; Morgan's item flips to resolved within seconds. Jamie's stays open (no pull request from him).
4. **Pretend senders: "Send a forged message"** — the platform answers 401 and the Live Feed shows a red "Rejected" card with the reason.
5. **"Send the last GitHub message again"** — the Live Feed shows "Repeat — not processed again".
6. Optional: **"Meeting ends — but the file is a contract"** — the run is sent to Escalations with a reason.

"Play the whole story" does steps 2 and 3 automatically.

The model decides what is extracted and each verdict, so rehearse several times. Keep one completed run as a fallback.

## Technical notes for teammates

- New table `integration_events`; new trigger type `integration_event` (a plain text value, no schema change for it).
- Existing databases need `backend/db/integration_events_migration.sql` run once.
- `followup.py`: the evidence rule now compares exact times, not calendar dates. Seeded evidence (date only) behaves exactly as before.
- New secrets: `GITHUB_WEBHOOK_SECRET`, `ZOOM_WEBHOOK_SECRET` (demo defaults set in `docker-compose.yml`). The endpoints refuse everything if a secret is not set.
