"""
End-to-end integration tests for the internal_mobility vertical
(Section 8.2). Exercises the REAL database and the REAL embedding
model (sentence-transformers downloads/caches on first use — same
network dependency as test_dummy_vertical_integration.py). Only the
LLM calls are mocked: extract_requirements, _reformulate_query, and
the ranking reason_node all go through graph.call_llm, so a single
scripted fake targeted at the graph module covers the whole pipeline.
"""

import json

import pytest

from app.core.db import get_connection
from app.core.embeddings import upsert_embedding
from app.verticals.internal_mobility.graph import (
    run_internal_mobility_vertical,
    KNOWN_DEPARTMENTS,
)
from app.schemas.agent_contract import AgentRunInput, TriggerType


@pytest.fixture(autouse=True)
def _register_via_import():
    # The graph module registers the vertical at import time; importing
    # it here (in the test file) already guarantees registration.
    import app.verticals.internal_mobility.graph  # noqa: F401


def _seed_engineer(department: str = "Engineering", years: int = 6, name: str = "Ayesha Rao",
                   profile: str = "Python, FastAPI, PostgreSQL, Kafka. Led payments microservices migration.") -> str:
    """Inserts one employee + workload + embedded profile, returns employee id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO employees (name, department, years_experience, location, profile_text)
                VALUES (%s, %s, %s, 'Bangalore', %s)
                RETURNING id;
                """,
                (name, department, years, profile),
            )
            emp_id = str(cur.fetchone()["id"])
            cur.execute(
                """
                INSERT INTO employee_workload (employee_id, utilization_pct, free_by_date)
                VALUES (%s, 60, NULL);
                """,
                (emp_id,),
            )
        conn.commit()
    finally:
        conn.close()

    upsert_embedding(
        vertical="internal_mobility",
        source_type="employee_profile",
        chunk_text=profile,
        source_id=emp_id,
        metadata={"employee_name": name, "department": department},
    )
    return emp_id


def _fake_llm_scripted(requirements: dict, ranking_content: str):
    """
    Builds a call_llm stand-in that returns:
      - extract_requirements -> requirements JSON (prompt contains
        "known departments are exactly")
      - _reformulate_query -> {"query": ...} JSON (prompt contains
        "weak matches")
      - reason_node -> ranking JSON (everything else, e.g. the
        RANKING_SYSTEM_PROMPT)
    """
    req_json = json.dumps(requirements)
    broadened_json = json.dumps({"query": f"{requirements['query_text']} skills experience"})

    def fake(messages, tools=None, **kwargs):
        if not messages:
            return {"content": "no messages", "tool_calls": []}
        system = messages[0].get("content") or ""
        if "known departments are exactly" in system:
            return {"content": req_json, "tool_calls": []}
        if "weak matches" in system:
            return {"content": broadened_json, "tool_calls": []}
        return {"content": ranking_content, "tool_calls": []}

    return fake


def test_internal_mobility_notifies_confident_available_candidate(monkeypatch):
    emp_id = _seed_engineer()
    assert KNOWN_DEPARTMENTS

    fake_llm = _fake_llm_scripted(
        requirements={"department": "Engineering", "min_experience": 3,
                      "query_text": "backend engineer python postgres"},
        ranking_content=json.dumps({
            "summary": "Found one strong backend candidate.",
            "candidates": [
                {"employee_id": emp_id, "rank": 1,
                 "rationale": "Deep payments backend experience.",
                 "skill_gaps": ["golang"], "confidence": 0.92},
            ],
        }),
    )
    monkeypatch.setattr("app.verticals.internal_mobility.graph.call_llm", fake_llm)

    agent_input = AgentRunInput(
        vertical="internal_mobility",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "Staff Backend Engineer for the payments team. Python, PostgreSQL, Kafka."},
    )
    output = run_internal_mobility_vertical(agent_input)

    assert output.status == "completed"
    assert output.escalated is False
    assert output.confidence == pytest.approx(0.92)
    assert len(output.actions_taken) == 1
    assert output.actions_taken[0].action_name == "notify_candidate"
    assert output.actions_taken[0].target_id == emp_id

    # role_matches row recorded with notified=True + a notification exists.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT notified FROM role_matches WHERE run_id = %s;", (output.run_id,))
            match = cur.fetchone()
            cur.execute("SELECT recipient FROM notifications WHERE run_id = %s;", (output.run_id,))
            note = cur.fetchone()
    finally:
        conn.close()
    assert match is not None
    assert match["notified"] is True
    assert note is not None
    assert note["recipient"] == "ayesha.rao@example.com"


def test_internal_mobility_leaves_low_confidence_candidate_unnotified(monkeypatch):
    emp_id = _seed_engineer()

    fake_llm = _fake_llm_scripted(
        requirements={"department": "Engineering", "min_experience": 3,
                      "query_text": "backend engineer"},
        ranking_content=json.dumps({
            "summary": "Candidate identified but weak fit.",
            "candidates": [
                {"employee_id": emp_id, "rank": 1,
                 "rationale": "Some overlap.",
                 "skill_gaps": ["payments", "kafka"], "confidence": 0.4},
            ],
        }),
    )
    monkeypatch.setattr("app.verticals.internal_mobility.graph.call_llm", fake_llm)

    output = run_internal_mobility_vertical(AgentRunInput(
        vertical="internal_mobility",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "Senior Backend Engineer, payments focus."},
    ))

    assert output.status == "completed"
    assert output.escalated is False
    assert output.actions_taken[0].action_name == "record_role_match"
    assert output.actions_taken[0].detail["result"]["notified"] is False

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT notified FROM role_matches WHERE run_id = %s;", (output.run_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["notified"] is False


def test_internal_mobility_blocks_busy_candidate_despite_high_confidence(monkeypatch):
    emp_id = _seed_engineer()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE employee_workload SET utilization_pct = 100 WHERE employee_id = %s;",
                (emp_id,),
            )
        conn.commit()
    finally:
        conn.close()

    fake_llm = _fake_llm_scripted(
        requirements={"department": "Engineering", "min_experience": 3,
                      "query_text": "backend engineer"},
        ranking_content=json.dumps({
            "summary": "Strong fit but currently fully utilized.",
            "candidates": [
                {"employee_id": emp_id, "rank": 1,
                 "rationale": "Perfect backend fit.",
                 "skill_gaps": [], "confidence": 0.95},
            ],
        }),
    )
    monkeypatch.setattr("app.verticals.internal_mobility.graph.call_llm", fake_llm)

    output = run_internal_mobility_vertical(AgentRunInput(
        vertical="internal_mobility",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "Staff Backend Engineer."},
    ))

    assert output.status == "completed"
    assert output.actions_taken[0].action_name == "record_role_match"
    assert output.actions_taken[0].detail["result"]["notified"] is False
    assert output.actions_taken[0].detail["result"]["reason"] == "short-term capacity not verified"

    # No notification should have been sent.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM notifications WHERE run_id = %s;", (output.run_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    assert row["n"] == 0


def test_internal_mobility_mixed_outcomes_per_candidate(monkeypatch):
    """One confident-available candidate (notified) + one weak (recorded unnotified)."""
    good_emp = _seed_engineer(name="Ayesha Rao",
                              profile="Python, FastAPI, PostgreSQL, Kafka. Payments microservices lead.")
    weak_emp = _seed_engineer(name="Tom Wilson",
                              profile="Python, FastAPI, Elasticsearch. Junior backend developer.")

    fake_llm = _fake_llm_scripted(
        requirements={"department": "Engineering", "min_experience": 3,
                      "query_text": "backend engineer"},
        ranking_content=json.dumps({
            "summary": "One strong, one weak.",
            "candidates": [
                {"employee_id": good_emp, "rank": 1,
                 "rationale": "Excellent payments backend fit.",
                 "skill_gaps": [], "confidence": 0.93},
                {"employee_id": weak_emp, "rank": 2,
                 "rationale": "Partial fit, missing payments depth.",
                 "skill_gaps": ["payments", "kafka"], "confidence": 0.5},
            ],
        }),
    )
    monkeypatch.setattr("app.verticals.internal_mobility.graph.call_llm", fake_llm)

    output = run_internal_mobility_vertical(AgentRunInput(
        vertical="internal_mobility",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "Staff Backend Engineer — payments domain."},
    ))

    assert output.status == "completed"
    assert len(output.actions_taken) == 2

    notified = [a for a in output.actions_taken if a.action_name == "notify_candidate"]
    recorded = [a for a in output.actions_taken if a.action_name == "record_role_match"]
    assert len(notified) == 1
    assert notified[0].target_id == good_emp
    assert len(recorded) == 1
    assert recorded[0].target_id == weak_emp

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT employee_id, notified FROM role_matches WHERE run_id = %s ORDER BY rank;",
                (output.run_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    notified = {r["employee_id"] for r in rows if r["notified"]}
    unnotified = {r["employee_id"] for r in rows if not r["notified"]}
    assert notified == {good_emp}
    assert unnotified == {weak_emp}


def test_internal_mobility_escalates_when_ranking_is_empty(monkeypatch):
    _seed_engineer()

    fake_llm = _fake_llm_scripted(
        requirements={"department": "Engineering", "min_experience": 3,
                      "query_text": "backend engineer"},
        ranking_content=json.dumps({"summary": "No candidates matched.", "candidates": []}),
    )
    monkeypatch.setattr("app.verticals.internal_mobility.graph.call_llm", fake_llm)

    output = run_internal_mobility_vertical(AgentRunInput(
        vertical="internal_mobility",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "Extremely niche quantum computing role."},
    ))

    assert output.status == "escalated"
    assert output.escalated is True
    assert output.escalation_reason is not None


def test_internal_mobility_logs_single_retrieval_decision_with_real_results(monkeypatch):
    """The dashboard reads the first step_type == 'retrieval' decision; the
    pre-filter stub previously masked the real search numbers with
    num_results=0. Now a successful search logs exactly one retrieval
    decision carrying the actual top_score / num_results."""
    emp_id = _seed_engineer()

    fake_llm = _fake_llm_scripted(
        requirements={"department": "Engineering", "min_experience": 3,
                      "query_text": "backend engineer python postgres"},
        ranking_content=json.dumps({
            "summary": "One strong candidate.",
            "candidates": [
                {"employee_id": emp_id, "rank": 1,
                 "rationale": "Deep backend fit.",
                 "skill_gaps": [], "confidence": 0.9},
            ],
        }),
    )
    monkeypatch.setattr("app.verticals.internal_mobility.graph.call_llm", fake_llm)

    output = run_internal_mobility_vertical(AgentRunInput(
        vertical="internal_mobility",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "Staff Backend Engineer, Python PostgreSQL Kafka."},
    ))

    assert output.status == "completed"
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT detail FROM agent_decisions "
                "WHERE run_id = %s AND step_type = 'retrieval' ORDER BY created_at;",
                (output.run_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    detail = rows[0]["detail"]
    assert detail["num_results"] >= 1
    assert detail["top_score"] is not None
    assert detail["top_score"] > 0


def test_internal_mobility_sanitizes_recipient_email(monkeypatch):
    emp_id = _seed_engineer(name="O'Brien  García")

    fake_llm = _fake_llm_scripted(
        requirements={"department": "Engineering", "min_experience": 3,
                      "query_text": "backend engineer"},
        ranking_content=json.dumps({
            "summary": "One strong candidate.",
            "candidates": [
                {"employee_id": emp_id, "rank": 1,
                 "rationale": "Deep backend fit.",
                 "skill_gaps": [], "confidence": 0.95},
            ],
        }),
    )
    monkeypatch.setattr("app.verticals.internal_mobility.graph.call_llm", fake_llm)

    output = run_internal_mobility_vertical(AgentRunInput(
        vertical="internal_mobility",
        trigger_type=TriggerType.UPLOAD,
        input_payload={"text": "Staff Backend Engineer."},
    ))

    assert output.status == "completed"
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT recipient FROM notifications WHERE run_id = %s;", (output.run_id,))
            note = cur.fetchone()
    finally:
        conn.close()
    assert note is not None
    assert note["recipient"] == "o.brien.garcia@example.com"