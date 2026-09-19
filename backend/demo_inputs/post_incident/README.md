# Post-Incident Vertical — Demo Inputs

These files are **genuinely new postmortem reports** intended for live testing
and demonstration through the Submit page. They are **not** copies of seed data.

## Files and Expected Behavior

### 1. `demo-redis-split-brain-session-store.txt`
**Expected outcome: HIGH confidence → auto-create remediation ticket**

This postmortem describes a Redis split-brain during failover causing session
data inconsistency. The root cause (cascading failure from a flapping service
and reconnection storms) overlaps with seeded incidents:
- `real-2020-03-09-discord-thundering-herd-cascade.txt` (thundering herd / cascading failure)
- `2024-07-10-redis-cache-oom.txt` (Redis infrastructure failure)

The agent should retrieve these as similar precedents, recognize the pattern,
and auto-create a remediation ticket.

### 2. `demo-pki-hsm-certificate-failure.txt`
**Expected outcome: LOW confidence → escalate to human review**

This postmortem describes a PKI/HSM hardware token failure causing TLS certificate
issuance delays. This root cause (hardware security module battery failure) has
**no matching precedent** in the seeded knowledge base — no existing postmortem
covers PKI, HSM, or certificate lifecycle failures.

The agent should find no strong matches, return low confidence, and escalate
for human review. This demonstrates the system's ability to say "I don't know"
rather than force-matching unrelated incidents.

### 3. `demo-checkout-db-pool-saturation.txt`
**Expected outcome: HIGH confidence → auto-create remediation ticket**

This postmortem describes a PostgreSQL connection pool exhaustion caused by a
misrouted analytics query. The root cause directly overlaps with:
- `2024-03-15-payments-db-pool-exhaustion.txt` (DB connection pool exhaustion, same pattern)

The agent should retrieve the payments-service pool exhaustion as a strong match,
recognize the nearly identical root cause (pool saturation from leaked/long-running
connections), and auto-create a remediation ticket.

## Usage

Submit these files one at a time through the platform's Submit page
(`/submit` → select `post_incident` vertical → upload file).

Do **not** add comments or notes inside these files — any inline text becomes
part of the extracted/embedded content and will distort retrieval results.