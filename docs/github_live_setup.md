# Live GitHub evidence for Vertical 4 — setup and demo

Transcripts are uploaded as before. **Evidence of finished work now comes from real GitHub.**
A commit (or merged pull request) in the demo repository is sent to the platform by GitHub itself,
signed with a shared secret, and shows up as a real card in the **Live Feed**.

- Demo account: `harshini0825` → treated as **Morgan**
- Demo repository: `harshini0825/morgan-percept-ai-demo`
- Only commits to the repository's default branch (and merged pull requests) count.

## One-time setup (about 10 minutes)

### 1. Choose a secret
Create a file named `.env` in the repository root (next to `docker-compose.yml`; it is already git-ignored — check with `git status` that it is not listed):

```
GITHUB_WEBHOOK_SECRET=pick-a-long-random-phrase-here
```

Restart the backend so it picks it up: `docker-compose up -d backend`

### 2. Start a public tunnel
GitHub must reach your laptop, so a tunnel gives it a temporary public address. Example with Cloudflare's free quick tunnel (no account needed; install `cloudflared` first):

```
cloudflared tunnel --url http://localhost:8000
```

It prints an address like `https://random-words.trycloudflare.com`. Keep this window open. **The address changes every time you restart the tunnel** (then update step 3).

The platform only lets public requests reach the two webhook endpoints; everything else (dashboard data, uploads) answers 404 to the outside world.

### 3. Register the webhook on GitHub
Log in as `harshini0825` (use a separate browser profile or a private window so your main account is untouched). Open the repository → **Settings → Webhooks → Add webhook**:

| Field | Value |
|---|---|
| Payload URL | `https://random-words.trycloudflare.com/integrations/github` |
| Content type | `application/json` |
| Secret | the same phrase as `GITHUB_WEBHOOK_SECRET` |
| Events | **Just the push event** (pull request merges also work if you tick "Pull requests") |

Save. GitHub immediately sends a `ping`; under **Recent Deliveries** it should show a green tick (200).
The platform records it as "ignored" in the Live Feed, which is correct.

## Demo run

1. Platform → **Submit**: upload `demo_4_due_today_no_evidence.txt`. Morgan's and Jamie's items appear.
2. GitHub (as `harshini0825`, repo `morgan-percept-ai-demo`): **Add file → Create new file** (or edit any file) in the web editor. Use a clear commit message that matches Morgan's item, e.g. `Restore nightly backup job schedule`. Commit directly to the default branch.
3. Platform → **Live Feed**: a GitHub card appears within seconds; Morgan's item turns resolved. Jamie's stays open.

The number of files changed does not matter; one file or none is enough. The commit **message** is what the judgement mostly rests on, so make it specific.

## If something does not appear

| Symptom | Likely cause |
|---|---|
| GitHub Recent Deliveries shows red / 401 | Secret differs between GitHub and `.env`, or backend not restarted |
| Red / 503 | `GITHUB_WEBHOOK_SECRET` not set in the backend |
| Red / timeout or 530 | Tunnel closed or address changed — update the Payload URL |
| Green 200 but Live Feed says ignored | Wrong repo, wrong branch, or account not mapped (the card says which) |
| Card appears but the item stays open | The commit message did not clearly match the item (the AI judged "not enough evidence"), or the commit was made **before** the transcript was uploaded |
| Nothing at all | Check `docker logs rag_backend` and the Recent Deliveries response body |

Make the commit **after** the upload: only activity later than the action item counts.

## Honest framing for reviewers

- Real: GitHub webhook, signature check, event/repo/account allow-lists, duplicate protection, identity mapping, Live Feed.
- Manual by design: transcript upload (Zoom/Meet transcript webhooks need paid plans or Workspace editions).
- Demo-only: a single GitHub account standing in for one team member; a temporary tunnel instead of a deployed HTTPS address.
- Say "GitHub integration is live; transcript intake is manual". Do not say the whole pipeline is automatic.

## Optional: the pretend senders
`docker-compose --profile simulator up -d mock-sources` starts the earlier simulator at http://localhost:9000. It is a fallback for rehearsal when there is no internet.
