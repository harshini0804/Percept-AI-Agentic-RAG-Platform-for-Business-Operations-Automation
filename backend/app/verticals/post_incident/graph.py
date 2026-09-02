"""
Post-Incident Knowledge Synthesis — LangGraph Agent (Section 8.1)

Assembles the shared orchestration nodes into a StateGraph that:
1. Embeds the incoming postmortem / incident description.
2. Retrieves historically similar incidents from the vector KB.
3. Reasons over retrieved context + available tools (the LLM may
   call lookup_incidents_by_service to check recurring patterns).
4. Parses the LLM's structured JSON output and routes to the
   action gate — either creating a remediation ticket autonomously
   or escalating to the HITL queue.

Model recommendation (Section 2):
  - The GROQ_MODEL env var controls which model the LLM gateway uses.
  - Default in the codebase: openai/gpt-oss-120b (free on Groq, but
    has the phantom tool-call bug documented in llm_gateway.py).
  - RECOMMENDED for this vertical: llama-3.3-70b-versatile
    Reason: Llama 3.3 70B is significantly more reliable at producing
    parseable structured JSON (the confidence/ticket output schema
    this vertical requires). It also handles tool-calling cleanly
    without the phantom tool-call bug. Available on Groq free tier.
  - Set GROQ_MODEL=llama-3.3-70b-versatile in backend/.env

  Alternative models on Groq (all free tier):
    - llama-3.1-8b-instant    → fast, but weaker at structured output
    - gemma2-9b-it            → good JSON compliance, smaller context
    - deepseek-r1-distill-llama-70b → strong reasoning, slow

  For embeddings: all-MiniLM-L6-v2 (384-dim) is already hardcoded in
  embeddings.py — no change needed there. This is a local HuggingFace
  model, no API key required.
"""

import json
from langgraph.graph import StateGraph, END

from app.core.orchestration import (
    AgentState,
    embed_node,
    retrieve_node,
    reason_node,
    action_gate_node,
    start_run,
)

# Ensure post_incident tools are registered (import triggers @tool decorators)
import app.verticals.post_incident.tools  # noqa: F401


# -------------------------------------------------------------------
# System prompt
# -------------------------------------------------------------------

POST_INCIDENT_SYSTEM_PROMPT = """\
You are a Post-Incident Knowledge Synthesis agent. Your job is to analyze \
a new incident postmortem or live incident description, compare it against \
historically similar incidents retrieved from the knowledge base, and \
identify recurring root cause patterns.

AVAILABLE TOOLS:
- lookup_incidents_by_service: Query the incidents database by service \
  name to check if this service has had past issues.
- get_incident_details: Fetch full details of a specific historical incident.

WORKFLOW:
1. Read the input incident description carefully.
2. Examine the retrieved context (chunks from historical postmortems).
3. If a service name is mentioned, call lookup_incidents_by_service to \
   check for recurring failures on that service.
4. Identify whether this incident shares a root cause pattern with any \
   historical incidents (e.g., same service + same failure mode = \
   recurring pattern).
5. Respond with ONLY strict JSON in this exact shape, no other text:

{
  "confidence": <float 0.0-1.0>,
  "root_cause_tag": "<short tag, e.g. 'connection_pool_exhaustion'>",
  "linked_incident_ids": ["<uuid1>", "<uuid2>"],
  "ticket_title": "<Descriptive remediation ticket title>",
  "should_create_ticket": <true if a recurring pattern was found and action is warranted>,
  "reasoning": "<2-3 sentence explanation of your analysis>"
}

SCORING GUIDANCE:
- confidence >= 0.8: Clear recurring pattern with strong evidence
- 0.5 <= confidence < 0.8: Possible pattern, but evidence is weak or partial
- confidence < 0.5: No clear pattern; new/unseen failure mode
- Set should_create_ticket=true ONLY if confidence >= 0.7 AND the pattern \
  warrants a follow-up ticket (e.g., same root cause seen 2+ times)
"""


# -------------------------------------------------------------------
# Vertical-specific finalize node
# -------------------------------------------------------------------

def finalize_node(state: AgentState) -> AgentState:
    """
    Parses the LLM's structured JSON output and dispatches to the
    shared confidence gate. If the LLM output is unparseable, we
    escalate with confidence 0.0 — better to route to a human than
    silently fail.
    """
    llm_output = state.get("llm_content", "")

    # Attempt to extract JSON from the response (the LLM sometimes
    # wraps it in markdown code fences)
    json_str = llm_output
    if "```" in llm_output:
        # Extract content between code fences
        parts = llm_output.split("```")
        for part in parts:
            stripped = part.strip()
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()
            if stripped.startswith("{"):
                json_str = stripped
                break

    try:
        parsed = json.loads(json_str)
        confidence = float(parsed.get("confidence", 0.0))
        should_create_ticket = bool(parsed.get("should_create_ticket", False))
        root_cause_tag = parsed.get("root_cause_tag", "unknown")
        linked_ids = parsed.get("linked_incident_ids", [])
        ticket_title = parsed.get("ticket_title", "")
        reasoning = parsed.get("reasoning", "")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        confidence = 0.0
        should_create_ticket = False
        root_cause_tag = "parse_failure"
        linked_ids = []
        ticket_title = ""
        reasoning = f"Failed to parse LLM output: {llm_output[:200]}"

    # Build the action tool arguments
    action_tool_name = None
    action_tool_args = None
    escalation_reason = None

    if should_create_ticket and ticket_title:
        action_tool_name = "create_incident_ticket"
        action_tool_args = {
            "title": ticket_title,
            "linked_incident_ids": linked_ids,
        }
    else:
        escalation_reason = (
            f"Root cause: {root_cause_tag}. {reasoning}"
            if reasoning
            else f"No actionable pattern detected (confidence={confidence:.2f})."
        )

    return action_gate_node(
        state,
        confidence=confidence,
        action_tool_name=action_tool_name,
        action_tool_args=action_tool_args,
        escalation_reason=escalation_reason,
    )


# -------------------------------------------------------------------
# Graph assembly
# -------------------------------------------------------------------

def build_post_incident_graph():
    graph = StateGraph(AgentState)
    graph.add_node("embed", embed_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("reason", reason_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("embed")
    graph.add_edge("embed", "retrieve")
    graph.add_edge("retrieve", "reason")
    graph.add_edge("reason", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


# -------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------

def run_post_incident(input_text: str) -> AgentState:
    """
    Entry point called by the vertical registry when a user submits
    to the post_incident vertical via POST /agent-runs.
    """
    run_id = start_run(vertical="post_incident", trigger_type="upload")

    initial_state: AgentState = {
        "run_id": run_id,
        "vertical": "post_incident",
        "source_type": "postmortem",
        "input_text": input_text,
        "system_prompt": POST_INCIDENT_SYSTEM_PROMPT,
        "confidence_threshold": 0.7,
    }

    graph = build_post_incident_graph()
    final_state = graph.invoke(initial_state)
    return final_state


# -------------------------------------------------------------------
# Register with the shared vertical registry
# -------------------------------------------------------------------

from app.core.vertical_registry import register_vertical  # noqa: E402

register_vertical("post_incident", run_post_incident)
