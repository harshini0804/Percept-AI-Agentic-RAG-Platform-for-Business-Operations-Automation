"""
Internal Mobility & Skill-Gap Matching Agent — Graph (Vertical 2,
Section 8.2).

The shared 5-stage pipeline shape (embed -> retrieve -> reason ->
gate) applies, but Vertical 2's retrieval is deliberately NOT the
generic orchestration.retrieve_node: Section 8.2 step 2 requires a
struct RELATIONAL pre-filter (department, min_experience) BEFORE the
semantic vector search, and the semantic search must run only within
that filtered pool (step 3). Capacity (Section 8.2 decision mechanics)
is never a structural filter — it stays an optional tool lookup +
UI badge, applied only at the per-candidate gate.

Flow (mirrors Section 8.2's workflow steps):
  1. parse_requirements_node: LLM extracts the role's department +
     min_experience (and a richer search query) from the submitted
     open-role description.
  2. register_role_node: stores the role relationally in `roles`
     (Section 8.2: "Job listings are stored relationally and are
     never embedded" — no embedding here).
  3. embed_node: embeds the role description (the query vector).
  4. retrieve_node (custom): employees filtered by department +
     min_experience, then search_with_retry runs the semantic search
     scoped to those employee ids via source_id = ANY(...). Weak
     results trigger the single LLM-broadened retry (Section 3.3
     stage 3, Section 8.2 step 4).
  5. reason_node (custom): LLM ranks candidates, identifies skill
     gaps, may call check_capacity() as an optional read tool.
  6. finalize_node (custom, per-candidate gate): for each candidate —
     fit confidence >= threshold AND capacity verified -> fire
     notify_candidate(); otherwise the candidate is logged to
     role_matches with notified=False and simply appears on the
     manager's board (Section 8.2 step 6/7).

The run-level AgentRunOutput is built manually here (not via the
shared build_agent_run_output) because Vertical 2 fires potentially
MANY actions per run (one per notified candidate), while the shared
helper only wraps the singular AgentState["action_taken"].
"""

import json
from typing import TypedDict

from langgraph.graph import StateGraph, END

from app.core.orchestration import embed_node, start_run, AgentState
from app.core.retrieval import search_with_retry
from app.core.llm_gateway import call_llm
from app.core.tool_registry import get_tools_for_vertical, execute_tool
from app.core.logging_service import (
    log_decision,
    complete_agent_run,
)
from app.core.db import get_connection
from app.core.documents import resolve_document_text
from app.schemas.agent_contract import (
    AgentRunInput,
    AgentRunOutput,
    ActionTaken,
)

# Ensure Internal Mobility tools are registered (import triggers the
# @tool decorators).
import app.verticals.internal_mobility.tools  # noqa: F401

# Known departments in the synthetic corpus — the extraction prompt
# constrains output to these so the structural pre-filter actually
# matches employees.department values.
KNOWN_DEPARTMENTS = ["Engineering", "Design", "Data Science"]


class InternalMobilityAgentState(AgentState):
    """
    LangGraph state schema for the internal_mobility graph (Section 8.2).

    Extends the shared AgentState with the Vertical-2 working fields.
    These MUST be declared here: LangGraph `StateGraph` only persists a
    node's returned keys into the next node's state if the key exists in
    the state schema, and the shared AgentState deliberately carries only
    the generic pipeline fields.
    """
    requirements: dict
    role_id: str
    prefiltered_employee_ids: list[str]
    actions_taken: list[dict]

# Threshold above which a candidate is eligible for autonomous
# notification (Section 12.2 — tuned per vertical; starts at 0.7).
DEFAULT_CONFIDENCE_THRESHOLD = 0.7

_ROLE_TITLE_FALLBACK = "Open Role"


# ---------------------------------------------------------------
# Stage 1 — extract structural requirements from the role description
# ---------------------------------------------------------------

_REQUIREMENTS_SYSTEM_PROMPT = f"""You extract recruitment requirements from an open-role
description posted by a hiring manager.

The company's known departments are exactly: {", ".join(KNOWN_DEPARTMENTS)}.

Respond with ONLY strict JSON, no other text, in this exact shape:
{{"department": "<one of the known departments, exactly as capitalized above, or '' if the role does not clearly belong to one>", "min_experience": <integer, the minimum years of experience required by the role, 0 if not specified>, "query_text": "<a concise, keyword-rich paraphrase of the role's core skills and responsibilities, written to surface matching employee portfolios>"}}"""


def extract_requirements(text: str) -> dict:
    """
    Calls the LLM to pull the role's structural requirements
    (department, min_experience) plus a richer search query out of
    the free-text role description.

    Fail-safe: on any parse error returns sensible defaults so a
    transient LLM hiccup never crashes the pipeline — the role still
    gets matched on whatever structure can be recovered.
    """
    response = call_llm(
        messages=[
            {"role": "system", "content": _REQUIREMENTS_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.0,
    )
    raw = (response.get("content") or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()

    try:
        parsed = json.loads(raw)
        department = parsed.get("department") or ""
        if department not in KNOWN_DEPARTMENTS:
            department = ""
        min_experience = int(parsed.get("min_experience") or 0)
        query_text = str(parsed.get("query_text") or text).strip() or text
    except (json.JSONDecodeError, TypeError, ValueError):
        department = ""
        min_experience = 0
        query_text = text

    return {
        "department": department,
        "min_experience": max(min_experience, 0),
        "query_text": query_text,
    }


def parse_requirements_node(state: InternalMobilityAgentState) -> InternalMobilityAgentState:
    requirements = extract_requirements(state["input_text"])
    state["requirements"] = requirements
    return state


# ---------------------------------------------------------------
# Stage 2 — store the role relationally (never embedded)
# ---------------------------------------------------------------

def _derive_role_title(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:120]
    return _ROLE_TITLE_FALLBACK


def register_role_node(state: InternalMobilityAgentState) -> InternalMobilityAgentState:
    req = state["requirements"]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO roles (title, description, department, min_experience, location)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    _derive_role_title(state["input_text"]),
                    state["input_text"],
                    req["department"] or None,
                    req["min_experience"] or 0,
                    None,
                ),
            )
            role_id = cur.fetchone()["id"]
        conn.commit()
    finally:
        conn.close()
    state["role_id"] = str(role_id)
    return state


# ---------------------------------------------------------------
# Stage 4 — struct-filter + retrieve within the filtered pool
# ---------------------------------------------------------------

def _reformulate_query(query_text: str, results: list[dict]) -> str:
    """
    Section 8.2 step 4 / Section 3.3 stage 3: if the first semantic
    pass ranked weak matches, the LLM broadens the query text once.
    The structural (department/min_experience) filter is untouched —
    only the search phrasing broadens.
    """
    response = call_llm(
        messages=[
            {
                "role": "system",
                "content": (
                    "The initial semantic search for internal employee portfolios "
                    "returned weak matches. Rephrase the query to be broader, "
                    "focusing on the underlying skills and responsibilities likely "
                    "to appear in a candidate's profile. Respond with ONLY strict "
                    'JSON: {"query": "<broadened query>"}'
                ),
            },
            {"role": "user", "content": query_text},
        ],
        temperature=0.2,
    )
    raw = (response.get("content") or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        return str(json.loads(raw).get("query") or query_text)
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return query_text


def _prefilter_employee_ids(department: str, min_experience: int) -> list[str]:
    """
    Section 8.2 step 2 — strict structural pre-filter on employees.
    department '' means "no department constraint"; capacity is
    deliberately NOT a filter here.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if department:
                cur.execute(
                    """
                    SELECT id FROM employees
                    WHERE department = %s AND years_experience >= %s;
                    """,
                    (department, min_experience),
                )
            else:
                cur.execute(
                    "SELECT id FROM employees WHERE years_experience >= %s;",
                    (min_experience,),
                )
            return [str(r["id"]) for r in cur.fetchall()]
    finally:
        conn.close()


def retrieve_node(state: InternalMobilityAgentState) -> InternalMobilityAgentState:
    req = state["requirements"]
    employee_ids = _prefilter_employee_ids(req["department"], req["min_experience"])
    state["prefiltered_employee_ids"] = employee_ids

    if not employee_ids:
        log_decision(
            state["run_id"],
            "retrieval",
            {
                "top_score": None,
                "num_results": 0,
                "retried": False,
                "prefiltered_pool_size": 0,
                "department_filter": req["department"],
                "min_experience_filter": req["min_experience"],
                "note": "Structural pre-filter returned no candidates; semantic search skipped.",
            },
        )
        state["retrieval_results"] = []
        state["retrieval_retried"] = False
        return state

    results, retried = search_with_retry(
        query_text=req["query_text"],
        vertical=state["vertical"],
        source_type=state["source_type"],
        confidence_threshold=state["confidence_threshold"],
        reformulate_query_fn=_reformulate_query,
        extra_filter_sql="AND source_id = ANY(%s::uuid[])",
        extra_filter_params=([employee_ids],),
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
            "prefiltered_pool_size": len(employee_ids),
        },
    )
    return state


# ---------------------------------------------------------------
# Stage 5 — LLM ranks candidates within the retrieved pool
# ---------------------------------------------------------------

_MAX_TOOL_ROUNDS = 4

_RANKING_SYSTEM_PROMPT = """You are an internal mobility matching agent. You are given an
open-role description and a set of retrieved internal employee profiles (ranked first by a
vector search). Rank the best internal fits for the role.

For each candidate you consider a fit, output:
  - employee_id: the profile's employee_id
  - rank: 1 = best fit, then 2, 3, ...
  - rationale: the strategic reason this employee fits the role
  - skill_gaps: explicit skills the role demands that this employee appears to lack
  - confidence: a float 0.0-1.0 measuring how confident you are this candidate is a strong match

You may call check_capacity(employee_id) to audit a frontrunner's current workload — but do
NOT exclude a candidate for being busy; it is only an availability badge, never a filter.

Respond with ONLY strict JSON, no other text, in this exact shape:
{"summary": "<one-sentence summary of the matching landscape>", "candidates": [
  {"employee_id": "uuid", "rank": 1, "rationale": "...", "skill_gaps": ["..."], "confidence": 0.9}
]}"""


def reason_node(state: InternalMobilityAgentState) -> InternalMobilityAgentState:
    req = state["requirements"]
    context_lines = ["OPEN ROLE:", state["input_text"],
                     "\nREQUIREMENTS:", f"department={req['department'] or 'any'}",
                     f", min_experience={req['min_experience']}",
                     "\n\nRETRIEVED CANDIDATE PROFILES:"]
    for r in state.get("retrieval_results", []):
        meta = r.get("metadata") or {}
        context_lines.append(
            f"\n- employee_id={r['source_id']} (employee: {meta.get('employee_name', '?')} "
            f"| similarity={r['similarity']:.3f})\n  {r['chunk_text']}"
        )
    context_text = "\n".join(context_lines)

    messages = [
        {"role": "system", "content": _RANKING_SYSTEM_PROMPT},
        {"role": "user", "content": context_text},
    ]
    tools = get_tools_for_vertical(state["vertical"])

    response = call_llm(messages=messages, tools=tools)

    for _ in range(_MAX_TOOL_ROUNDS):
        if not response["tool_calls"]:
            break
        for tc in response["tool_calls"]:
            tc_id = tc.get("id") or f"call_{len(messages)}"
            log_decision(state["run_id"], "tool_call", tc)
            result = execute_tool(state["vertical"], tc["name"], tc["arguments"])
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tc_id,
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
                }],
            })
            messages.append({"role": "tool", "content": str(result), "tool_call_id": tc_id})
        response = call_llm(messages=messages, tools=tools)

    state["llm_content"] = response["content"]
    state["tool_calls"] = response["tool_calls"]

    log_decision(state["run_id"], "llm_reasoning", {"content": response["content"]})
    return state


# ---------------------------------------------------------------
# Stage 6 — per-candidate confidence + capacity gate
# ---------------------------------------------------------------

def _parse_candidates(content: str | None) -> list[dict]:
    if not content:
        return []
    raw = content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(raw)
        candidates = parsed.get("candidates") or []
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return []

    normalized = []
    for i, c in enumerate(candidates):
        if not isinstance(c, dict) or not c.get("employee_id"):
            continue
        try:
            rank = int(c.get("rank", i + 1))
            confidence = float(c.get("confidence", 0.0))
        except (TypeError, ValueError):
            rank, confidence = i + 1, 0.0
        normalized.append({
            "employee_id": str(c["employee_id"]),
            "rank": rank,
            "rationale": str(c.get("rationale") or ""),
            "skill_gaps": [str(g) for g in (c.get("skill_gaps") or [])],
            "confidence": max(0.0, min(1.0, confidence)),
        })
    normalized.sort(key=lambda c: (c["rank"], -c["confidence"]))
    return normalized


def _capacity_verified(employee_id: str) -> bool:
    """
    Optional tool lookup at the gate (Section 8.2: capacity is a
    badge, not a filter). A candidate is "capacity verified" unless
    employee_workload explicitly reports full utilization — unknown
    workload data must never hide a candidate.
    """
    result = execute_tool("internal_mobility", "check_capacity", {"employee_id": employee_id})
    available = result.get("available")
    return available is not False


def finalize_node(state: InternalMobilityAgentState) -> InternalMobilityAgentState:
    from app.verticals.internal_mobility.tools import record_role_match

    candidates = _parse_candidates(state.get("llm_content"))

    if not candidates:
        return _escalate(state, reason="No candidates were produced by the ranking LLM.")

    state["confidence"] = candidates[0]["confidence"]
    role_id = state["role_id"]
    actions: list[dict] = []

    for c in candidates:
        emp_id = c["employee_id"]
        if c["confidence"] >= state["confidence_threshold"] and _capacity_verified(emp_id):
            result = execute_tool(
                "internal_mobility",
                "notify_candidate",
                {
                    "employee_id": emp_id,
                    "role_id": role_id,
                    "run_id": state["run_id"],
                    "rank": c["rank"],
                    "rationale": c["rationale"],
                    "confidence": c["confidence"],
                    "message": (
                        f"You've been identified as a strong internal match for an open role. "
                        f"{c['rationale']}"
                    ),
                },
            )
            actions.append({"action_name": "notify_candidate", "result": result, **c})
        else:
            match_id = record_role_match(
                run_id=state["run_id"],
                role_id=role_id,
                employee_id=emp_id,
                rank=c["rank"],
                rationale=c["rationale"],
                confidence=c["confidence"],
                notified=False,
            )
            actions.append(
                {
                    "action_name": "record_role_match",
                    "result": {
                        "match_id": match_id,
                        "employee_id": emp_id,
                        "role_id": role_id,
                        "notified": False,
                        "reason": (
                            "fit confidence below threshold"
                            if c["confidence"] < state["confidence_threshold"]
                            else "short-term capacity not verified"
                        ),
                    },
                    **c,
                }
            )

    state["actions_taken"] = actions
    for action in actions:
        log_decision(state["run_id"], "action", action)

    state["escalated"] = False
    state["escalation_reason"] = None
    complete_agent_run(state["run_id"], status="completed", confidence=state["confidence"])
    return state


def _escalate(state: InternalMobilityAgentState, reason: str) -> InternalMobilityAgentState:
    from app.core.logging_service import create_escalation

    state["confidence"] = 0.0
    state["escalated"] = True
    state["escalation_reason"] = reason
    state["actions_taken"] = list(state.get("actions_taken") or [])
    create_escalation(state["run_id"], reason=reason)
    log_decision(state["run_id"], "escalation", {"reason": reason})
    complete_agent_run(state["run_id"], status="escalated", confidence=0.0)
    return state


# ---------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------

def build_internal_mobility_graph():
    graph = StateGraph(InternalMobilityAgentState)
    graph.add_node("parse_requirements", parse_requirements_node)
    graph.add_node("register_role", register_role_node)
    graph.add_node("embed", embed_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("reason", reason_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("parse_requirements")
    graph.add_edge("parse_requirements", "register_role")
    graph.add_edge("register_role", "embed")
    graph.add_edge("embed", "retrieve")
    graph.add_edge("retrieve", "reason")
    graph.add_edge("reason", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


def run_internal_mobility_vertical(agent_input: AgentRunInput) -> AgentRunOutput:
    """
    Entry point registered in app.core.vertical_registry — invoked by
    the shared POST /agent-runs endpoints. Consumes AgentRunInput and
    returns a validated AgentRunOutput.

    Input modes: input_document_id (file upload, resolved via
    app.core.documents) or input_payload={"text": ...} (pasted role
    description).
    """
    if agent_input.input_document_id:
        input_text = resolve_document_text(agent_input.input_document_id)
    elif agent_input.input_payload and "text" in agent_input.input_payload:
        input_text = agent_input.input_payload["text"]
    else:
        raise ValueError(
            "The internal_mobility vertical requires either input_document_id "
            "or input_payload={'text': ...}."
        )

    run_id = start_run(
        vertical=agent_input.vertical,
        trigger_type=agent_input.trigger_type.value,
        input_document_id=agent_input.input_document_id,
    )

    initial_state: AgentState = {
        "run_id": run_id,
        "vertical": agent_input.vertical,
        "source_type": "employee_profile",
        "input_text": input_text,
        "system_prompt": _RANKING_SYSTEM_PROMPT,
        "confidence_threshold": DEFAULT_CONFIDENCE_THRESHOLD,
    }

    graph = build_internal_mobility_graph()
    final_state = graph.invoke(initial_state)

    # No runtime persistence for Vertical 2 (Section 6.4 carve-out
    # only covers Verticals 1, 3, 4) — role descriptions are stored
    # relationally (in `roles`) and never embedded.

    actions_taken = [
        ActionTaken(
            action_name=a["action_name"],
            target_id=a.get("employee_id"),
            detail={
                "result": a.get("result"),
                "rank": a.get("rank"),
                "rationale": a.get("rationale"),
                "skill_gaps": a.get("skill_gaps"),
                "confidence": a.get("confidence"),
            },
        )
        for a in final_state.get("actions_taken", [])
    ]

    return AgentRunOutput(
        run_id=final_state["run_id"],
        status="escalated" if final_state["escalated"] else "completed",
        confidence=final_state.get("confidence", 0.0),
        actions_taken=actions_taken,
        escalated=final_state["escalated"],
        escalation_reason=final_state.get("escalation_reason"),
    )


from app.core.vertical_registry import register_vertical

register_vertical("internal_mobility", run_internal_mobility_vertical)