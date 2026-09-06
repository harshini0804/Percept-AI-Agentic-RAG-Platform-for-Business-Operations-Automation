"""
End-to-End Mock Integration Tests for Vertical 1: Post-Incident Knowledge Synthesis.

Verifies all agentic branching decisions without requiring a live Postgres instance
or live Groq API credits:
1. High Confidence Flow -> Autonomous Ticket Creation (Confidence >= 0.70)
2. Low Retrieval Score Flow -> Query Broadening Retry (Score < 0.45)
3. Low Confidence Flow -> Human-in-the-Loop Escalation (Confidence < 0.70)
4. Runtime Persistence -> Automatic chunking & knowledge base update
"""

import os
import sys
import json
from unittest.mock import patch, MagicMock

# Suppress TF and Protobuf conflicts on host
os.environ["USE_TF"] = "0"
os.environ["TRANSFORMERS_NO_TF"] = "1"
os.environ["USE_TORCH"] = "1"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.verticals.post_incident.graph import (
    build_post_incident_graph,
    post_incident_reformulate_query,
    post_incident_retrieve_node,
    persist_analyzed_postmortem,
    RETRIEVAL_SIMILARITY_THRESHOLD,
    ACTION_CONFIDENCE_THRESHOLD,
    POST_INCIDENT_SYSTEM_PROMPT,
)
from app.core.orchestration import AgentState


def test_autonomous_ticket_creation_flow():
    """When confidence >= 0.70, an engineering ticket is created autonomously."""
    print("Running test_autonomous_ticket_creation_flow...")
    
    mock_llm_response = {
        "content": json.dumps({
            "confidence": 0.88,
            "root_cause_tag": "connection_pool_exhaustion",
            "linked_incident_ids": ["11111111-1111-1111-1111-111111111111"],
            "ticket_title": "Fix HikariCP connection pool exhaustion in payments-service",
            "should_create_ticket": True,
            "reasoning": "Recurring issue with pool leaks observed across 2 historical payments incidents.",
        }),
        "tool_calls": [],
    }

    mock_retrieval = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "similarity": 0.82,
            "chunk_text": "## Root Cause\nHikariCP pool exhaustion caused by missing finally block.",
            "metadata": {"service": "payments-service"},
        }
    ]

    with patch("app.verticals.post_incident.graph.embed_node", side_effect=lambda s: s), \
         patch("app.core.orchestration.embed_text", return_value=[0.1] * 384), \
         patch("app.verticals.post_incident.graph.search_with_retry", return_value=(mock_retrieval, False)), \
         patch("app.core.orchestration.call_llm", return_value=mock_llm_response), \
         patch("app.core.orchestration.execute_tool", return_value={"ticket_id": "test-ticket-123", "status": "open"}), \
         patch("app.verticals.post_incident.graph.persist_analyzed_postmortem"), \
         patch("app.core.orchestration.log_decision"), \
         patch("app.core.orchestration.complete_agent_run"), \
         patch("app.verticals.post_incident.graph.log_decision"):

        graph = build_post_incident_graph()
        initial_state: AgentState = {
            "run_id": "test-run-001",
            "vertical": "post_incident",
            "source_type": "postmortem",
            "input_text": "# Incident: Payments Outage\nService: payments-service\n## Root Cause\nPool leaked.",
            "system_prompt": POST_INCIDENT_SYSTEM_PROMPT,
            "confidence_threshold": ACTION_CONFIDENCE_THRESHOLD,
        }

        final_state = graph.invoke(initial_state)

        assert final_state["confidence"] == 0.88
        assert final_state["escalated"] is False
        assert final_state["action_taken"] is not None
        assert final_state["action_taken"]["action_name"] == "create_incident_ticket"
        assert final_state["action_taken"]["result"]["ticket_id"] == "test-ticket-123"
        print("  PASS: test_autonomous_ticket_creation_flow")


def test_hitl_escalation_flow():
    """When confidence < 0.70, the run routes to the human escalation queue."""
    print("Running test_hitl_escalation_flow...")

    mock_llm_response = {
        "content": json.dumps({
            "confidence": 0.45,
            "root_cause_tag": "unknown_anomaly",
            "linked_incident_ids": [],
            "ticket_title": "",
            "should_create_ticket": False,
            "reasoning": "New, unobserved failure mechanism with no clear historical precedent.",
        }),
        "tool_calls": [],
    }

    mock_retrieval = [
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "similarity": 0.52,
            "chunk_text": "Unrelated postmortem chunk.",
            "metadata": {},
        }
    ]

    with patch("app.verticals.post_incident.graph.embed_node", side_effect=lambda s: s), \
         patch("app.core.orchestration.embed_text", return_value=[0.1] * 384), \
         patch("app.verticals.post_incident.graph.search_with_retry", return_value=(mock_retrieval, False)), \
         patch("app.core.orchestration.call_llm", return_value=mock_llm_response), \
         patch("app.core.orchestration.create_escalation") as mock_esc, \
         patch("app.verticals.post_incident.graph.persist_analyzed_postmortem"), \
         patch("app.core.orchestration.log_decision"), \
         patch("app.core.orchestration.complete_agent_run"), \
         patch("app.verticals.post_incident.graph.log_decision"):

        graph = build_post_incident_graph()
        initial_state: AgentState = {
            "run_id": "test-run-002",
            "vertical": "post_incident",
            "source_type": "postmortem",
            "input_text": "# Incident: Quantum Glitch\nService: quantum-core\n## Root Cause\nCosmic ray bitflip.",
            "system_prompt": POST_INCIDENT_SYSTEM_PROMPT,
            "confidence_threshold": ACTION_CONFIDENCE_THRESHOLD,
        }

        final_state = graph.invoke(initial_state)

        assert final_state["confidence"] == 0.45
        assert final_state["escalated"] is True
        assert final_state["action_taken"] is None
        assert "unknown_anomaly" in final_state["escalation_reason"]
        mock_esc.assert_called_once()
        print("  PASS: test_hitl_escalation_flow")


def test_retrieval_query_broadening_retry():
    """When top retrieval score < 0.45, query broadening triggers and retries."""
    print("Running test_retrieval_query_broadening_retry...")

    state: AgentState = {
        "run_id": "test-run-003",
        "vertical": "post_incident",
        "source_type": "postmortem",
        "input_text": "Obscure server crash with cryptic error code 0x88921.",
        "system_prompt": POST_INCIDENT_SYSTEM_PROMPT,
        "confidence_threshold": ACTION_CONFIDENCE_THRESHOLD,
    }

    # Pass 1 yields low similarity (0.28 < 0.45), Pass 2 yields better similarity (0.65)
    pass1_results = [{"similarity": 0.28, "chunk_text": "vague match"}]
    pass2_results = [{"similarity": 0.65, "chunk_text": "broadened root cause match"}]

    def mock_search_impl(query_text, *args, **kwargs):
        if "broadened" in query_text:
            return pass2_results
        return pass1_results

    with patch("app.core.retrieval.search_embeddings", side_effect=mock_search_impl), \
         patch("app.verticals.post_incident.graph.call_llm", return_value={"content": "broadened memory leak failure"}), \
         patch("app.verticals.post_incident.graph.log_decision"):

        updated_state = post_incident_retrieve_node(state)

        assert updated_state["retrieval_retried"] is True
        assert len(updated_state["retrieval_results"]) == 1
        assert updated_state["retrieval_results"][0]["similarity"] == 0.65
        print("  PASS: test_retrieval_query_broadening_retry")


def test_runtime_persistence_called():
    """Newly analyzed postmortems are automatically chunked and persisted into embeddings."""
    print("Running test_runtime_persistence_called...")

    sample_postmortem = """# Incident: Token Cascade Failure
Date: 2024-08-10
Service: auth-gateway
Severity: P1

## Summary
Gateway dropped connections during token refresh storms.

## Root Cause
Redis connection pool saturation caused by unthrottled refresh requests.
"""

    with patch("app.verticals.post_incident.graph.get_connection") as mock_conn, \
         patch("app.verticals.post_incident.graph.upsert_embedding") as mock_upsert:

        # Mock relational insert
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": "mock-incident-uuid"}
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        persist_analyzed_postmortem(
            input_text=sample_postmortem,
            run_id="test-run-004",
            root_cause_tag="token_cascade_failure",
        )

        # Should have inserted incident and chunked + embedded into vector KB
        assert mock_cursor.execute.called
        assert mock_upsert.call_count >= 2  # At least Summary and Root Cause chunks
        print("  PASS: test_runtime_persistence_called")


if __name__ == "__main__":
    print("=== Starting Vertical 1 E2E Mock Tests ===\n")
    test_autonomous_ticket_creation_flow()
    test_hitl_escalation_flow()
    test_retrieval_query_broadening_retry()
    test_runtime_persistence_called()
    print("\n=== All E2E Mock Tests Passed PASSED ===")
