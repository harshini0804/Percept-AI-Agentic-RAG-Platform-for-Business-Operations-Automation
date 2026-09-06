"""
Dummy vertical graph — assembles the shared orchestration nodes
(Section 3.1) into an actual LangGraph StateGraph, exactly as a real
vertical owner would (Section 7, point 11). Proves the shared core
pieces genuinely compose into a working end-to-end pipeline.
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
from app.schemas.agent_contract import AgentRunInput, AgentRunOutput, build_agent_run_output
from app.core.documents import resolve_document_text
from app.core.embeddings import upsert_embedding

# Ensure the dummy tool is registered (import triggers the decorator)
import app.verticals.dummy.tools  # noqa: F401


DUMMY_SYSTEM_PROMPT = """You are a dummy test agent. Given an input and
retrieved context, respond ONLY with strict JSON in this exact shape,
no other text:
{"confidence": <float 0.0-1.0>, "should_act": <true/false>, "reason": "<short reason>"}
"""


def finalize_node(state: AgentState) -> AgentState:
    """
    Vertical-specific: parses this dummy vertical's expected JSON
    output shape and applies the shared confidence gate.
    """
    try:
        parsed = json.loads(state["llm_content"])
        confidence = float(parsed["confidence"])
        should_act = bool(parsed["should_act"])
        reason = parsed.get("reason", "")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        confidence = 0.0
        should_act = False
        reason = "Failed to parse LLM output."

    return action_gate_node(
        state,
        confidence=confidence,
        action_tool_name="log_dummy_action" if should_act else None,
        action_tool_args={"note": reason} if should_act else None,
        escalation_reason=reason if not should_act else None,
    )


def build_dummy_graph():
    graph = StateGraph(AgentState)
    graph.add_node("embed", embed_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("reason", reason_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("embed")
    graph.add_edge("embed", "retrieve")
    graph.add_edge("retrieve", "reason")
    graph.add_edge("finalize", END)
    graph.add_edge("reason", "finalize")

    return graph.compile()


def run_dummy_vertical(agent_input: AgentRunInput) -> AgentRunOutput:
    """
    Entry point mirroring how a real vertical's trigger handler
    would kick off a run. Accepts an AgentRunInput and returns a
    validated AgentRunOutput directly (Section 3.2, 7.2).

    Supports both input modes: input_document_id (resolved via
    app.core.documents, Phase C) and input_payload={"text": ...}
    (plain pasted text, unchanged since Phase B).

    Runtime persistence (Section 6.4): after any run finishes —
    completed or escalated — the analyzed text is embedded and
    stored into the KB as future precedent, mirroring Section 8.1's
    Vertical 1 workflow step 8. This is the reference pattern real
    verticals (1, 3, 4 per Section 6.4's carve-out — not 2) should
    follow in their own graphs.
    """
    if agent_input.input_document_id:
        input_text = resolve_document_text(agent_input.input_document_id)
    elif agent_input.input_payload and "text" in agent_input.input_payload:
        input_text = agent_input.input_payload["text"]
    else:
        raise ValueError(
            "The dummy vertical requires either input_document_id or "
            "input_payload={'text': ...}."
        )

    run_id = start_run(
        vertical=agent_input.vertical,
        trigger_type=agent_input.trigger_type.value,
    )

    initial_state: AgentState = {
        "run_id": run_id,
        "vertical": agent_input.vertical,
        "source_type": "postmortem",
        "input_text": input_text,
        "system_prompt": DUMMY_SYSTEM_PROMPT,
        "confidence_threshold": 0.7,
    }

    graph = build_dummy_graph()
    final_state = graph.invoke(initial_state)

    # Runtime persistence — happens regardless of completed/escalated
    # outcome, and regardless of which input mode was used.
    upsert_embedding(
        vertical=agent_input.vertical,
        source_type="postmortem",
        chunk_text=input_text,
        source_id=agent_input.input_document_id,
    )

    return build_agent_run_output(final_state)


from app.core.vertical_registry import register_vertical

register_vertical("dummy", run_dummy_vertical)