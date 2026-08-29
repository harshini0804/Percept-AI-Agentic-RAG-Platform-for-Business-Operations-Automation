"""
Orchestration Engine (Section 3.1, 3.3) — reusable LangGraph node
functions implementing the shared 5-stage pipeline shape. These are
building blocks, not a fixed graph: each vertical owner (Section 7,
point 11) assembles their own StateGraph from these nodes, adding
their own vertical-specific prompt and tools.

Shared state every node reads/writes. Vertical-specific graphs may
extend this with extra fields as needed.
"""

from typing import TypedDict, Optional, Any
from langgraph.graph import StateGraph, END

from app.core.embeddings import embed_text
from app.core.retrieval import search_with_retry
from app.core.llm_gateway import call_llm
from app.core.tool_registry import get_tools_for_vertical, execute_tool
from app.core.logging_service import (
    create_agent_run,
    log_decision,
    complete_agent_run,
    create_escalation,
)


class AgentState(TypedDict, total=False):
    # Set before the graph runs
    run_id: str
    vertical: str
    source_type: str
    input_text: str
    system_prompt: str
    confidence_threshold: float

    # Populated as the graph runs
    embedding: list[float]
    retrieval_results: list[dict]
    retrieval_retried: bool
    llm_content: Optional[str]
    tool_calls: list[dict]
    confidence: float
    action_taken: Optional[dict]
    escalated: bool
    escalation_reason: Optional[str]


# ---------------------------------------------------------------
# Stage 1 — Embed
# ---------------------------------------------------------------

def embed_node(state: AgentState) -> AgentState:
    """
    Embeds the input text. Storage into the KB (runtime persistence,
    Section 6.4) is a separate, deliberate call to
    embeddings.upsert_embedding() — not automatic here, since not
    every vertical persists every input (e.g. Vertical 2's role
    postings are never embedded, per Section 8.2 Notes).
    """
    state["embedding"] = embed_text(state["input_text"])
    return state


# ---------------------------------------------------------------
# Stage 2 & 3 — Retrieve, with confidence-gated retry
# ---------------------------------------------------------------

def retrieve_node(state: AgentState) -> AgentState:
    """
    Runs retrieval with the code-gated, one-retry pattern (Section
    3.3 Stage 3). No LLM-driven query reformulation here — this
    dummy/generic version retries with the same query, since actual
    reformulation logic is vertical-specific reasoning. A real
    vertical can pass its own reformulate_query_fn by not using this
    node directly and calling search_with_retry itself instead.
    """
    results, retried = search_with_retry(
        query_text=state["input_text"],
        vertical=state["vertical"],
        source_type=state["source_type"],
        confidence_threshold=state["confidence_threshold"],
        reformulate_query_fn=None,
    )
    state["retrieval_results"] = results
    state["retrieval_retried"] = retried

    log_decision(
        state["run_id"],
        "retrieval",
        {
            "top_score": results[0]["similarity"] if results else 0.0,
            "num_results": len(results),
            "retried": retried,
        },
    )
    return state


# ---------------------------------------------------------------
# Stage 4 — Reason, optionally call a read tool
# ---------------------------------------------------------------

def reason_node(state: AgentState) -> AgentState:
    context_text = "\n\n".join(r["chunk_text"] for r in state.get("retrieval_results", []))
    tools = get_tools_for_vertical(state["vertical"])

    user_content = f"Input:\n{state['input_text']}\n\nRetrieved context:\n{context_text}"
    messages = [
        {"role": "system", "content": state["system_prompt"]},
        {"role": "user", "content": user_content},
    ]

    response = call_llm(messages=messages, tools=tools)

    tool_calls = response["tool_calls"]
    if tool_calls:
        for tc in tool_calls:
            log_decision(state["run_id"], "tool_call", tc)
            result = execute_tool(state["vertical"], tc["name"], tc["arguments"])
            messages.append({"role": "assistant", "content": None, "tool_calls": [tc]})
            messages.append({"role": "tool", "content": str(result)})

        response = call_llm(messages=messages, tools=tools)

    state["llm_content"] = response["content"]
    state["tool_calls"] = tool_calls

    log_decision(state["run_id"], "llm_reasoning", {"content": response["content"]})
    return state


# ---------------------------------------------------------------
# Stage 5 — Confidence-gate on action
# ---------------------------------------------------------------

def action_gate_node(
    state: AgentState,
    confidence: float,
    action_tool_name: Optional[str] = None,
    action_tool_args: Optional[dict] = None,
    escalation_reason: Optional[str] = None,
) -> AgentState:
    """
    Fires a write tool if confidence clears the threshold; otherwise
    escalates. confidence/action_tool_name/escalation_reason are
    passed in explicitly rather than parsed here, since extracting
    them from the LLM's response is vertical-specific (each
    vertical's output shape differs, per Section 8).
    """
    state["confidence"] = confidence

    if confidence >= state["confidence_threshold"] and action_tool_name:
        result = execute_tool(state["vertical"], action_tool_name, action_tool_args or {})
        state["action_taken"] = {"action_name": action_tool_name, "result": result}
        state["escalated"] = False
        state["escalation_reason"] = None
        log_decision(state["run_id"], "action", state["action_taken"])
        complete_agent_run(state["run_id"], status="completed", confidence=confidence)
    else:
        reason = escalation_reason or "Confidence below action threshold."
        create_escalation(state["run_id"], reason=reason)
        state["action_taken"] = None
        state["escalated"] = True
        state["escalation_reason"] = reason
        log_decision(state["run_id"], "escalation", {"reason": reason})
        complete_agent_run(state["run_id"], status="escalated", confidence=confidence)

    return state


# ---------------------------------------------------------------
# Helper: any vertical can use this to open a run before building
# its own graph's initial state.
# ---------------------------------------------------------------

def start_run(vertical: str, trigger_type: str, input_document_id: str | None = None) -> str:
    return create_agent_run(vertical, trigger_type, input_document_id)