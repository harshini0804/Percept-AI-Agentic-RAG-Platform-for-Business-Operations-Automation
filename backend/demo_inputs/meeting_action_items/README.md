# Meeting Action Items — Demo Inputs

These are NOT seed data — they are never automatically ingested into the
KB. They exist for manually demoing the Submit page (vertical = "Meeting
Action Items"). Each file is new content, not a copy of anything in
`seed_data/`, so retrieval against it proves something.

Explanations live here, never inside the files themselves: anything written
into a demo file becomes part of the text the system reads.

Expected outcomes are the *design intent*. The model makes the final call on
what is extracted and on each follow-up verdict, so rehearse once before a
live demo.

## Before a demo

1. **Wipe this vertical's data** (the `escalations` step is required once any
   wrong-document run has been escalated, otherwise the delete fails):

   ```
   docker exec -it rag_postgres psql -U rag_user -d rag_platform -c "DELETE FROM agent_decisions WHERE run_id IN (SELECT id FROM agent_runs WHERE vertical = 'meeting_action_items'); DELETE FROM notifications WHERE run_id IN (SELECT id FROM agent_runs WHERE vertical = 'meeting_action_items'); DELETE FROM escalations WHERE run_id IN (SELECT id FROM agent_runs WHERE vertical = 'meeting_action_items'); DELETE FROM agent_runs WHERE vertical = 'meeting_action_items'; DELETE FROM embeddings WHERE vertical = 'meeting_action_items'; DELETE FROM action_items; DELETE FROM meetings;"
   ```

2. **Reseed on the day of the demo:** `docker exec -it rag_backend python seed.py`.
   Seeded owner activity is dated "tomorrow" relative to the seeding day, and
   follow-up only accepts evidence dated after an item was created. Seeding on
   the demo day keeps that true for items you submit live.

3. **Run the follow-up check twice** (or wait for two 10-minute scheduler
   cycles). Cycle 1 resolves items with evidence and nudges the rest; cycle 2
   escalates items that were already nudged:

   ```
   docker exec -it rag_backend python -c "from app.main import app; from app.verticals.meeting_action_items.followup import run_followup_check as r; x = r(); print({k: x[k] for k in ('checked','processed','skipped_noop','failed')})"
   ```

## Files

| File | Format | Shows | Expected |
|---|---|---|---|
| `demo_1_likely_recurring_match.txt` | txt | Recurrence | Alex's pipeline item flagged `recurring`; Priya's status line yields no item |
| `demo_2_unrelated_new_topic.txt` | txt | New, unrelated items | Two items (Jamie, Morgan), not recurring |
| `demo_3_due_today_resolves.txt` | txt | Live follow-up: resolves | Two items due today (Priya, Alex). Within one follow-up cycle both resolve, because matching evidence is seeded. |
| `demo_4_due_today_no_evidence.txt` | txt | Live follow-up: nudge, then escalate | Two items due today (Morgan, Jamie), no evidence. Cycle 1 sends nudges; cycle 2 escalates to their managers. |
| `demo_5_long_meeting.docx` | docx | Many items, file upload | About 7 items across all four owners, mixed deadlines; the "error tracking vendor", "headcount" and "offsite" mentions produce no items. Run page shows "N action items created" and the Original Document panel. |
| `demo_6_recurring_priya_api_versioning.txt` | txt | Recurrence vs. an open item | Priya's API versioning item flagged `recurring`; Alex's deprecation-notice item is new |
| `demo_7_wrong_document_contract.txt` | txt | Wrong document | Zero items, run `escalated` with "No action items were found...", visible in the Escalations queue |
| `demo_8_wrong_document_article.pdf` | pdf | Wrong document (PDF path) | Same as above |

Live follow-up demos (3 and 4) only work if the model resolves "today" to
today's date. If an item shows "No deadline — not monitored" on the Tracker,
it will not be followed up.

## Owner-activity seed files (in `seed_data/.../owner_activity/`)

These files cannot carry a README of their own: seeding reads every file in
that folder, so a README there would be parsed as activity.

| Purpose | Files | Expected effect |
|---|---|---|
| Clear completion | `alex_deploy_pipeline_fix`, `priya_api_review`, `morgan_staging_db_docs`, `owner_activity_jamie_checkout_mockups`, `owner_activity_priya_payment_errors`, `jamie_client_walkthrough_video` | Matching item resolves |
| Partial progress | `morgan_onboarding_docs_partial`, `priya_api_versioning_outline`, `morgan_analytics_db_storage_review` | Item is not resolved; nudge, then escalate once due |
| Related but not the task | `priya_client_call_scheduling`, `morgan_vendor_invoice_review` | Item is not resolved |
| Evidence for the live demo | `priya_vendor_shortlist_sent`, `alex_release_notes_published` | Resolves the items in `demo_3` |
| Unrelated activity | `alex_dependency_upgrade`, `priya_sprint_retro_notes`, `jamie_brand_assets_cleanup` | Noise the search must see past |

## Expected state after seeding and two follow-up cycles

About nine seeded items resolve. Four are nudged and then escalated: Morgan's
onboarding docs, and Priya's API versioning proposal, client integration
summary and leadership summary. Two are not yet due (Morgan's staging database
setup and Jamie's mobile feedback, both dated five days out).

The least certain verdict is Morgan's onboarding docs, because his older
staging-database doc also mentions onboarding.
