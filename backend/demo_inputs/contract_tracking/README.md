# Contract Tracking — Demo Inputs

These are NOT seed data — they are never automatically ingested into
the KB. They exist purely for manually testing/demoing the Submit
page (vertical = "contract_tracking"). Each is genuinely new content,
not a copy of anything in `seed_data/contract_tracking/`, so
retrieval via `search_similar_contracts` actually proves something.

## demo_1_confident_auto_action.txt

A short, well-structured SaaS subscription agreement with four
clean, specific, date-bound obligations. Expect:
- All 4 obligations extract with high confidence (0.90+)
- All 4 trigger `create_calendar_reminder` automatically
- `search_similar_contracts` surfaces the renewal-notice clause from
  `vendor_northbridge_storage.txt` as a precedent match
- Good "everything green" happy-path demo

## demo_2_mixed_escalation.txt

A professional services agreement with mixed clear and ambiguous
obligations. Expect:
- 2 clear obligations → auto-reminded
- 1 vague obligation ("within a reasonable time") → escalated
- Good demo of per-obligation confidence gate

## demo_3_obligation_dates_computed.txt

A technology partnership agreement with a stated Effective Date
(1 February 2026) and specific duration-based obligations. Expect:
- All obligations auto-reminded
- Real computed dates visible in RunDetail "Due:" field
  (e.g. 17 March 2026, 3 March 2026, 1 April 2027)
- Primary demo of the obligation_date parsing feature

## demo_4_vague_timing_escalation.txt

A management consulting agreement where every obligation has
genuinely vague timing ("reasonable time", "at intervals that
reflect", "as and when required"). Expect:
- All obligations escalated due to vague date confidence penalty
  (raw confidence ~0.90, effective ~0.78 after 0.12 penalty)
- Escalation reasons name the exact vague timing language
- `obligation_date` null for all
- Demonstrates Approach B: vague timing routed to human review

## demo_5_event_triggered_escalation.txt

A supplier quality agreement where every obligation's timing
depends on a future event ("within 48 hours of becoming aware",
"within 10 days of receiving Buyer's request"). Expect:
- All obligations escalated due to event-triggered confidence
  penalty (0.05 reduction)
- Escalation reasons name the specific triggering event
- `obligation_date` null for all
- Demonstrates "monitor the triggering event" escalation reason

## demo_6_no_timing_standing_duties.txt

An IP and confidentiality agreement with permanent standing
duties and no deadlines ("shall maintain strict confidentiality",
"shall take all reasonable steps"). Expect:
- Obligations extracted but escalated with "standing duty —
  no deadline" reasons
- `obligation_date` null for all
- Demonstrates the `no_timing` date_status branch

## demo_7_cross_reference_resolution.txt

A software development agreement where clauses explicitly
reference other sections ("Delivery Schedule defined in Section 1",
"Warranty Period as defined in Section 1"). Expect:
- `get_surrounding_clauses` fires for cross-referenced clauses
- Re-extraction with resolved context produces higher confidence
- Demonstrates the cross-reference tool working live

## demo_8_mixed_date_types.txt

An enterprise services agreement containing all four date_status
types in a single contract — computed dates, vague timing,
event-triggered, and standing duties. Expect:
- Clauses 1-3 → computed dates, auto-reminded with "Due:" visible
- Clause 4 → event-triggered, escalated (0.05 penalty)
- Clause 5 → vague, escalated (0.12 penalty)
- Clause 6 → no timing, escalated
- Clause 7 → vague, escalated (0.12 penalty)
- Best single-contract demo of the full date resolution system

## demo_9_high_value_contract.txt

A long enterprise license agreement (12 clauses) with a stated
Effective Date (1 January 2026) and many specific obligations.
Expect:
- Most obligations auto-reminded with real computed dates
- Tests pipeline under load: rate limits, key alternation
- Good stress test for a live demo session

## demo_10_unusual_wording_precedent.txt

A strategic alliance agreement using non-standard legal phrasing
("evergreen basis", "sunset election", "clawback netting",
"lookback window"). Expect:
- `search_similar_contracts` fires due to unusual wording flag
- Agent surfaces similar precedent clauses from the seed corpus
- Some obligations escalated due to atypical phrasing reducing
  confidence
- Demonstrates the unusual wording detection path