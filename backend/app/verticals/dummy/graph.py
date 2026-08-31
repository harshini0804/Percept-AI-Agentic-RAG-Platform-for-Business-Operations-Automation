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


def run_dummy_vertical(input_text: str) -> AgentState:
    """
    Entry point mirroring how a real vertical's trigger handler
    would kick off a run.
    """
    run_id = start_run(vertical="dummy", trigger_type="upload")

    initial_state: AgentState = {
    "run_id": run_id,
    "vertical": "dummy",
    "source_type": "postmortem",   # changed from "dummy_source"
    "input_text": input_text,
    "system_prompt": DUMMY_SYSTEM_PROMPT,
    "confidence_threshold": 0.7,
}

    graph = build_dummy_graph()
    final_state = graph.invoke(initial_state)
    return final_state


from app.core.vertical_registry import register_vertical

register_vertical("dummy", run_dummy_vertical)