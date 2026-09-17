# Contract Tracking — Demo Inputs

These are NOT seed data — they are never automatically ingested into
the KB. They exist purely for manually testing/demoing the Submit
page (vertical = "contract_tracking"). Each is genuinely new content,
not a copy of anything in `seed_data/contract_tracking/`, so
retrieval via `search_similar_contracts` actually proves something.

## demo_1_confident_auto_action.txt

A short, well-structured Software-as-a-Service (SaaS) subscription
agreement with four clean, specific, date-bound obligations. Expect:

- All 4 obligations extract with high confidence (0.90+).
- All 4 trigger `create_calendar_reminder` automatically — the
  "everything green" happy-path demo showing the pipeline working
  end-to-end with no human intervention.
- `search_similar_contracts` should surface the renewal-notice clause
  from `vendor_northbridge_storage.txt` in the KB as a precedent
  match (similar 60-day renewal window language), demonstrating
  real cross-contract retrieval.

## demo_2_mixed_escalation.txt

A professional services agreement with a mix of clear and ambiguous
obligations — designed to produce both auto-reminded and escalated
outcomes from a single contract upload. Expect:

- 2 clear obligations (specific dates/periods) → auto-reminded.
- 1 deliberately vague obligation ("within a reasonable time",
  no concrete deadline) → confidence below ACTION_THRESHOLD →
  `flag_for_manual_review` → appears in the Escalations tab.
- Good demo of the per-obligation confidence gate (Section 8.3,
  Agentic Decision Points): same contract, different outcomes per
  clause.
