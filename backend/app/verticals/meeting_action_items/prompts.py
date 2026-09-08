"""
Meeting Action-Item Enforcement (Vertical 4, Section 8.4) — prompts.

Section 7.2 explicitly lists prompt template(s) as their own vertical
owner deliverable, kept separate from graph.py's orchestration logic.
"""

EXTRACTION_SYSTEM_PROMPT = """You are analyzing a meeting transcript to extract action items.

Read the transcript and identify every concrete action item that was
assigned to a specific person during the meeting.

Respond ONLY with a strict JSON array in this exact shape, no other text:
[
  {"description": "<what needs to be done>", "owner": "<first name of the person responsible>", "deadline": "<YYYY-MM-DD, or null if no deadline was mentioned>"}
]

If no action items were mentioned, respond with an empty array: []

Rules:
- Do not invent action items that were not actually discussed.
- "owner" must be the person's first name only, in lowercase, exactly
  as they were referred to in the transcript.
- "description" should be a short, concrete summary of the task, not
  a verbatim quote of the whole discussion around it.
"""

FOLLOWUP_VERDICT_PROMPT = """You are checking whether a previously assigned action item has been completed, based on evidence of the owner's recent activity (tickets, commits, follow-up mentions).

Given the action item's description and any related activity evidence found, respond ONLY with strict JSON in this exact shape, no other text:
{"verdict": "done" | "in_progress" | "no_evidence", "confidence": <float 0.0-1.0>}

Rules:
- "done": the evidence clearly shows this specific task was completed.
- "in_progress": there is some related activity, but it does not
  clearly show the task is finished.
- "no_evidence": no related activity was found, or what was found is
  unrelated to this specific task.
- Base your verdict only on the evidence given — do not assume
  completion just because time has passed.
"""