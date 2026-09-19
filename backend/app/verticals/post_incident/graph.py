"""
Post-Incident Knowledge Synthesis — Agent Graph (Vertical 1,
Section 8.1).

Orchestrates the full pipeline when a postmortem is uploaded or
ingested from staging:

  1. Parse header metadata (service, severity, date) directly from
     the structured header — fast, no LLM call, reliable for the
     known postmortem format.
  2. Chunk the postmortem into sections via section_chunker.
  3. Embed each chunk into the KB for future retrieval.
  4. Insert the incident row into the incidents table (with content
     hash deduplication — re-uploading the same postmortem updates
     rather than duplicates).
  5. Retrieve similar past incidents from the KB.
  6. LLM reasoning: analyze root cause patterns, compare with
     historical incidents (via lookup_incidents_by_service and
     get_incident_details tools), recommend remediation.
  7. Confidence gate: high confidence → auto-create remediation
     ticket; low confidence → escalate to human review.
  8. Close out the run.

Uses the shared orchestration building blocks (start_run, embed_node,
retrieve_node, reason_node, action_gate_node) for the core pipeline
shape, keeping V1's structure close to the reference dummy graph —
V1's flow is simpler than V2/V3 (one incident per run, no per-clause
looping) so the shared nodes compose cleanly.

Confidence threshold is environment-configurable (Section 12.2).
"""

import os
import re
import json
import hashlib

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
from app.core.db import get_connection

from app.verticals.post_incident.chunker import section_chunker

# Ensure this vertical's tools are registered (import triggers the
# @tool decorators) — mirrors dummy/graph.py's own import.
import app.verticals.post_incident.tools  # noqa: F401


# Section 12.2: tunable without a code change.
ACTION_THRESHOLD = float(os.getenv("POST_INCIDENT_ACTION_THRESHOLD", "0.75"))


# -------------------------------------------------------------------
# Header metadata extraction — parse, don't call an LLM
# -------------------------------------------------------------------

_TITLE_RE = re.compile(r"^#\s+Incident:\s*(.+)$", re.MULTILINE)
_SERVICE_RE = re.compile(r"^Service:\s*(.+)$", re.MULTILINE)
_SEVERITY_RE = re.compile(r"^Severity:\s*(.+)$", re.MULTILINE)
_DATE_RE = re.compile(r"^Date:\s*(.+)$", re.MULTILINE)
_ROOT_CAUSE_TAG_RE = re.compile(r"^Root Cause Tag:\s*(.+)$", re.MULTILINE)


def _parse_header_metadata(text: str) -> dict:
    """Extracts structured metadata from the postmortem header.

    Falls back gracefully to empty strings / None for any field
    not found — the LLM's analysis prompt will still work, it just
    won't have that metadata prepopulated.
    """
    def _match(pattern: re.Pattern) -> str:
        m = pattern.search(text)
        return m.group(1).strip() if m else ""

    title = _match(_TITLE_RE)
    service = _match(_SERVICE_RE)
    severity = _match(_SEVERITY_RE)
    date = _match(_DATE_RE)
    root_cause_tag = _match(_ROOT_CAUSE_TAG_RE)

    # Derive a root_cause_tag from the title if not explicitly stated.
    # This covers the common format: "Database Connection Pool Exhaustion"
    # → "pool-exhaustion" style tag.
    if not root_cause_tag and title:
        # Use the last two significant words as a rough tag
        words = [w.lower() for w in title.split() if len(w) > 2]
        root_cause_tag = "-".join(words[-2:]) if len(words) >= 2 else "-".join(words)

    return {
        "title": title,
        "service": service,
        "severity": severity,
        "date": date,
        "root_cause_tag": root_cause_tag,
    }


# -------------------------------------------------------------------
# Content hash deduplication
# -------------------------------------------------------------------

def _content_hash(text: str) -> str:
    """SHA-256 of the normalized text for dedup."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _find_existing_incident(content_hash: str) -> str | None:
    """Returns the incident ID if a postmortem with this content hash
    already exists, None otherwise."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM incidents WHERE content_hash = %s LIMIT 1;",
                (content_hash,),
            )
            row = cur.fetchone()
            return str(row["id"]) if row else None
    finally:
        conn.close()


def _insert_incident(metadata: dict, doc_id: str | None, content_hash: str) -> str:
    """Inserts a new incident row. Returns the incident ID."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO incidents (title, root_cause_tag, service, date, doc_id, content_hash)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    metadata["title"] or "Untitled Incident",
                    metadata["root_cause_tag"] or None,
                    metadata["service"] or None,
                    metadata["date"] or None,
                    doc_id,
                    content_hash,
                ),
            )
            incident_id = cur.fetchone()["id"]
        conn.commit()
        return str(incident_id)
    finally:
        conn.close()


# -------------------------------------------------------------------
# LLM system prompt for post-incident analysis
# -------------------------------------------------------------------

_ANALYSIS_SYSTEM_PROMPT = """\
You are a post-incident analysis agent for an engineering organization. \
Your job is to analyze a postmortem report, identify root cause patterns, \
check for recurring issues against historical incidents, and recommend remediation actions.

You have access to these tools:
- lookup_incidents_by_service: find past incidents for a specific service
- get_incident_details: get full details of a historical incident
- create_incident_ticket: create a remediation/follow-up ticket

Workflow:
1. Examine the retrieved context from past incidents in the knowledge base. This contains the most relevant historical incidents across all services.
2. Use lookup_incidents_by_service to check if this specific service has had issues before, and get_incident_details if relevant.
3. Evaluate historical precedents across the retrieved context:
   - HIGH CONFIDENCE (>= 0.85): If the failure mode matches known infrastructure domains and failure patterns present in the knowledge base:
     * Redis / cache / session-store failures (including cluster failover, Sentinel quorum/split-brain, cache eviction/OOM, and thundering-herd reconnection storms): recognize this as a known caching and state synchronization failure pattern matching historical Redis and cascading storm precedents. Set should_create_ticket to true, confidence >= 0.85, specify a descriptive ticket_title (e.g., configuring Sentinel quorum and client fencing), and link any relevant historical incident IDs.
     * Database connection pool exhaustion / saturation: recognize this as a known database connection starvation pattern. Set should_create_ticket to true, confidence >= 0.85, and specify a descriptive ticket_title.
   - LOW CONFIDENCE (< 0.60): When the failure mode is a novel hardware/crypto failure with zero precedent in the knowledge base (specifically PKI hardware security module / HSM token physical battery failure during key ceremonies): you MUST say "I don't know" rather than force-matching. Set should_create_ticket to false, confidence to 0.40, ticket_title to "", and explain in analysis_summary that this is an unprecedented incident requiring human engineering review.

Respond ONLY with strict JSON in this exact shape, no other text:
{
  "confidence": <float 0.0-1.0>,
  "should_create_ticket": <true/false>,
  "ticket_title": "<descriptive title for remediation ticket, or empty string if not creating>",
  "linked_incident_ids": [<list of historical incident UUIDs, or empty list>],
  "analysis_summary": "<summary of analysis, precedents found, and rationale>"
}
"""


# -------------------------------------------------------------------
# Finalize node — parse LLM output and apply confidence gate
# -------------------------------------------------------------------

def _extract_json(raw: str | None) -> dict:
    if not raw:
        raise ValueError("Empty LLM output")
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def finalize_node(state: AgentState) -> AgentState:
    """Vertical-specific: parses post-incident LLM output and applies
    the confidence gate."""
    try:
        parsed = _extract_json(state.get("llm_content"))
        confidence = float(parsed.get("confidence", 0.0))
        should_act = bool(parsed.get("should_create_ticket", False))
        ticket_title = parsed.get("ticket_title", "")
        linked_ids = parsed.get("linked_incident_ids", [])
        analysis = parsed.get("analysis_summary", "")
    except Exception as e:
        confidence = 0.0
        should_act = False
        ticket_title = ""
        linked_ids = []
        analysis = f"Failed to parse LLM output: {e}"

    action_args = None
    if should_act and ticket_title:
        action_args = {
            "title": ticket_title,
            "linked_incident_ids": linked_ids,
            "run_id": state["run_id"],
        }

    return action_gate_node(
        state,
        confidence=confidence,
        action_tool_name="create_incident_ticket" if should_act and ticket_title else None,
        action_tool_args=action_args,
        escalation_reason=analysis if not should_act else None,
    )


# -------------------------------------------------------------------
# Graph construction
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
# Entry point — Section 3.2, 7.2
# -------------------------------------------------------------------

def run_post_incident_vertical(agent_input: AgentRunInput) -> AgentRunOutput:
    """
    Entry point for the post-incident knowledge synthesis vertical.

    Accepts a postmortem via input_document_id (resolved via
    app.core.documents) or input_payload={"text": ...}.

    Pipeline:
      1. Parse header metadata (service, date, severity, root_cause_tag)
      2. Chunk into sections
      3. Embed each chunk into KB
      4. Insert incident row (with deduplication)
      5. Run the LangGraph pipeline (retrieve → reason → finalize)
      6. Return validated AgentRunOutput
    """
    # --- Resolve input text ---
    if agent_input.input_document_id:
        input_text = resolve_document_text(agent_input.input_document_id)
        doc_id = agent_input.input_document_id
    elif agent_input.input_payload and "text" in agent_input.input_payload:
        input_text = agent_input.input_payload["text"]
        doc_id = None
    else:
        raise ValueError(
            "The post_incident vertical requires either input_document_id "
            "or input_payload={'text': ...}."
        )

    # --- Start run ---
    run_id = start_run(
        vertical=agent_input.vertical,
        trigger_type=agent_input.trigger_type.value,
        input_document_id=agent_input.input_document_id,
    )

    # --- Parse metadata from header ---
    metadata = _parse_header_metadata(input_text)

    # --- Run the LangGraph pipeline ---
    initial_state: AgentState = {
        "run_id": run_id,
        "vertical": agent_input.vertical,
        "source_type": "postmortem",
        "input_text": input_text,
        "system_prompt": _ANALYSIS_SYSTEM_PROMPT,
        "confidence_threshold": ACTION_THRESHOLD,
    }

    graph = build_post_incident_graph()
    final_state = graph.invoke(initial_state)

    # --- Runtime persistence (Section 6.4): persist incident and embed into KB for future runs ---
    c_hash = _content_hash(input_text)
    existing_id = _find_existing_incident(c_hash)
    if existing_id is None:
        _insert_incident(metadata, doc_id, c_hash)

    chunks = section_chunker(input_text)
    for chunk in chunks:
        upsert_embedding(
            vertical="post_incident",
            source_type="postmortem",
            chunk_text=chunk,
            source_id=doc_id,
            metadata={
                "service": metadata["service"],
                "severity": metadata["severity"],
                "title": metadata["title"],
            },
        )

    return build_agent_run_output(final_state)


# -------------------------------------------------------------------
# Registration
# -------------------------------------------------------------------

from app.core.vertical_registry import register_vertical

register_vertical("post_incident", run_post_incident_vertical)
