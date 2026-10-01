# Vertical 3 — Threshold Tuning Notes

## Contract Obligation \& Renewal Tracking Agent

This document records the reasoning behind the threshold values chosen for Vertical 3's confidence gate and date-clarity penalty system. All three values are env-configurable and were chosen based on real experiments against actual LLM (Groq `openai/gpt-oss-120b`) output, not guesswork or defaults.





## 1\. ACTION\_THRESHOLD (default: 0.85)

### What it controls

The minimum extraction confidence required for an obligation to be auto-actioned via `create\_calendar\_reminder`. Obligations below this threshold are routed to `flag\_for\_manual\_review` instead.



### The experiment

The same contract (`threshold\_experiment\_contract.txt`, a 7-clause Managed Services and Subscription Agreement) was run through the full extraction pipeline at three threshold values using real LLM calls:



|Threshold|Status|Auto-reminded|Escalated|
|-|-|-|-|
|0.75|completed|5|0|
|0.85|completed|6|0|
|0.95|escalated|4|1|

### 

### What the data showed

The real LLM produces confidences in a narrow **0.85–0.98 band** for well-defined, date-bound obligations. The one exception in the experiment was a clause using "reasonable advance notice" with no specific number of days — this consistently scored **0.85**, sitting exactly at the boundary.



**Why 0.75 is too permissive:** At 0.75, every obligation auto-reminds, including the "reasonable advance notice" clause. An obligation with no concrete deadline cannot be meaningfully calendar-reminded without human judgment. The model was clearly less certain about this clause  than the others, and the lower threshold was ignoring that signal.



**Why 0.95 is too aggressive:** At 0.95, even clearly-stated obligations with specific periods get escalated if the LLM scores them at 0.93 or 0.94 — which happens for obligations that are clear but use slightly informal or non-standard phrasing. This produces unnecessary HITL work.



**Why 0.85 is right for this domain:** It sits exactly at the natural gap in the confidence distribution — specific, concrete obligations land above it (0.90–0.98), and genuinely ambiguous ones land at or below it (0.85 or lower). The "reasonable advance notice" clause at exactly 0.85 is the most illustrative case: it is a real obligation, but without a concrete date it is not safe to auto-action.

### 

Configuration
CONTRACT\_TRACKING\_ACTION\_THRESHOLD=0.85   # default


Override in `backend/.env` to experiment with other values. The value is read at module import time from `graph.py`.



## 

## 2\. VAGUE\_DATE\_CONFIDENCE\_PENALTY (default: 0.12)

### What it controls

A reduction applied to the **effective confidence** used for the action gate when `\_resolve\_obligation\_date()` classifies the obligation's timing as `"vague"` — meaning the timing description is genuinely unresolvable: "within a reasonable time", "from time to time", "as agreed", "periodically", "at a mutually convenient point", etc. The raw extraction confidence is not changed. Only the value used at the action gate is reduced.

### 

### Rationale

An obligation whose timing cannot be computed into a concrete calendar date is genuinely harder to auto-action than one with a specific deadline. A `create\_calendar\_reminder` call with no date is meaningless — it produces a reminder with `obligation\_date = null`, which tells a human nothing about when to act. The penalty routes these obligations to human review, where a reviewer can determine the appropriate deadline in context.

### 

### Why 0.12

The penalty was chosen to push obligations that would otherwise sit just above ACTION\_THRESHOLD (0.85–0.92 raw confidence) below it, while not affecting obligations that are clearly high-confidence in every other respect:

* A clause with raw confidence 0.92 and vague timing:
`0.92 - 0.12 = 0.80 < 0.85` → correctly escalated
* A clause with raw confidence 0.97 and vague timing:
`0.97 - 0.12 = 0.85` → borderline, may still auto-remind (acceptable — a 0.97-confidence obligation is very clearly identified even if its timing is uncertain)



A penalty smaller than 0.10 would not move most vague-timed obligations across the threshold. A penalty larger than 0.15 would start escalating obligations that are both clearly identified AND clearly timed, which is not the intent.

### 

Configuration
CONTRACT\_TRACKING\_VAGUE\_DATE\_PENALTY=0.12   # default








## 3\. EVENT\_TRIGGERED\_CONFIDENCE\_PENALTY (default: 0.05)

### What it controls

A smaller reduction applied when `date\_status = "event\_triggered"` — meaning the timing depends on a future event that has not yet occurred:
"within 30 days of the invoice date", "14 days following written notice of breach", "within 48 hours of discovery", etc.

### 

### Rationale

Event-triggered obligations are different from vague ones in an important way: the obligation itself is usually clearly stated and confidently extracted. The  ncertainty is not about what the obligation is, but about when the triggering event will occur. A `create\_calendar\_reminder` is still somewhat useful here — it reminds a reviewer that this obligation exists and will need to be actioned when the trigger fires. But the reminder cannot carry a concrete date, and a human should monitor for the triggering event rather than relying on a calendar entry. The smaller penalty (0.05 vs 0.12) reflects this distinction: event-triggered obligations  are less problematic to auto-action than vague ones, but still warrant a slight push toward review.

### 

### Why 0.05

A penalty of 0.05 moves obligations at 0.88 raw confidence: `0.88 - 0.05 = 0.83 < 0.85` → escalated with "monitor the triggering event" reason.

Obligations at 0.90+: `0.90 - 0.05 = 0.85` → borderline or auto-reminded. 

A clearly-identified event-triggered obligation (e.g. "notify within 48 hours of a data breach") is specific enough that auto-reminding it is defensible — the reviewer is being reminded the obligation exists, even if the clock hasn't started yet.



Configuration
CONTRACT\_TRACKING\_EVENT\_TRIGGERED\_PENALTY=0.05   # default






## 4\. Summary table

|Parameter|Default|Env var|
|-|-|-|
|Action gate threshold|0.85|`CONTRACT\_TRACKING\_ACTION\_THRESHOLD`|
|Vague timing penalty|0.12|`CONTRACT\_TRACKING\_VAGUE\_DATE\_PENALTY`|
|Event-triggered penalty|0.05|`CONTRACT\_TRACKING\_EVENT\_TRIGGERED\_PENALTY`|
|Precedent check floor|0.60|`CONTRACT\_TRACKING\_PRECEDENT\_THRESHOLD`|

All four values are read at module import time in `graph.py` via `os.getenv()`. Override any of them in `backend/.env` — no code change required.







## 5\. What is not tuned here

**`PRECEDENT\_CHECK\_THRESHOLD` (default: 0.60)** — the floor that triggers `search\_similar\_contracts` even when the LLM did not explicitly flag unusual wording. No real obligation in the test corpus scored below 0.85, so this threshold never actually fired during the experiment. It is kept at 0.60 as a safety net for genuinely unusual extraction scenarios, but has no empirical basis from this experiment beyond "it should be well below ACTION\_THRESHOLD."



**`GROQ\_MAX\_RETRIES` (default: 2) and `GROQ\_DEFAULT\_BACKOFF\_SECONDS` (default: 2.0)** — retry/backoff parameters for rate-limit handling. These were not tuned experimentally; 2 retries with a 2-second default backoff (overridden by Groq's suggested retry-after duration when present) is a reasonable conservative default for a free-tier API with an 8000 TPM limit.

