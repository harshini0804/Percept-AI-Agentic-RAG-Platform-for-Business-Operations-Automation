"""
Tests for app.verticals.post_incident.tools.

Uses the real test database (see tests/conftest.py) to test SQL queries and tool execution.
"""

import uuid
import pytest
from app.core.db import get_connection
from app.core.tool_registry import get_tools_for_vertical
from app.verticals.post_incident.tools import (
    lookup_incidents_by_service,
    get_incident_details,
    create_incident_ticket,
)


@pytest.fixture
def sample_incident_id() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO incidents (title, root_cause_tag, service, date)
                VALUES (%s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    "Payments Pool Exhaustion",
                    "db_pool_exhaustion",
                    "payments-service",
                    "2024-03-15",
                ),
            )
            incident_id = str(cur.fetchone()["id"])
        conn.commit()
        return incident_id
    finally:
        conn.close()


def test_post_incident_tools_registered():
    """All three tools are registered in tool_registry under vertical='post_incident'."""
    tools = get_tools_for_vertical("post_incident")
    tool_names = [t["name"] for t in tools]

    assert "lookup_incidents_by_service" in tool_names
    assert "get_incident_details" in tool_names
    assert "create_incident_ticket" in tool_names


def test_lookup_incidents_by_service(sample_incident_id):
    """lookup_incidents_by_service retrieves incidents matching the service name."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO incidents (title, root_cause_tag, service, date)
                VALUES (%s, %s, %s, %s);
                """,
                (
                    "Auth Token Storm",
                    "token_expiry",
                    "auth-service",
                    "2024-05-22",
                ),
            )
        conn.commit()
    finally:
        conn.close()

    # Query payments-service
    results = lookup_incidents_by_service("payments-service")
    assert len(results) == 1
    assert results[0]["id"] == sample_incident_id
    assert results[0]["title"] == "Payments Pool Exhaustion"
    assert results[0]["service"] == "payments-service"

    # Query auth-service
    auth_results = lookup_incidents_by_service("auth-service")
    assert len(auth_results) == 1
    assert auth_results[0]["title"] == "Auth Token Storm"

    # Query nonexistent service
    empty_results = lookup_incidents_by_service("nonexistent-service")
    assert empty_results == []


def test_get_incident_details(sample_incident_id):
    """get_incident_details returns full metadata for an existing incident."""
    detail = get_incident_details(sample_incident_id)
    assert detail["id"] == sample_incident_id
    assert detail["title"] == "Payments Pool Exhaustion"
    assert detail["root_cause_tag"] == "db_pool_exhaustion"
    assert detail["service"] == "payments-service"
    assert detail["date"] == "2024-03-15"


def test_get_incident_details_nonexistent():
    """get_incident_details returns an error dict for nonexistent UUID."""
    fake_id = str(uuid.uuid4())
    result = get_incident_details(fake_id)
    assert "error" in result
    assert fake_id in result["error"]


def test_create_incident_ticket(existing_run_id, sample_incident_id):
    """create_incident_ticket inserts a remediation ticket row tied to the agent run."""
    result = create_incident_ticket(
        title="Automated Remediation: Scale DB Pool",
        linked_incident_ids=[sample_incident_id],
        run_id=existing_run_id,
    )

    assert result["status"] == "open"
    ticket_id = result["ticket_id"]
    assert ticket_id is not None

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, run_id, title, linked_incident_ids::text[] AS linked_incident_ids, status FROM incident_tickets WHERE id = %s;",
                (ticket_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert str(row["id"]) == ticket_id
            assert str(row["run_id"]) == existing_run_id
            assert row["title"] == "Automated Remediation: Scale DB Pool"
            assert [str(uid) for uid in row["linked_incident_ids"]] == [sample_incident_id]
            assert row["status"] == "open"
    finally:
        conn.close()


def test_create_incident_ticket_empty_linked_ids(existing_run_id):
    """create_incident_ticket succeeds when linked_incident_ids is None or empty list."""
    result = create_incident_ticket(
        title="Automated Remediation: General Restart",
        linked_incident_ids=None,
        run_id=existing_run_id,
    )

    assert result["status"] == "open"
    ticket_id = result["ticket_id"]
    assert ticket_id is not None

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, run_id, title, linked_incident_ids::text[] AS linked_incident_ids, status FROM incident_tickets WHERE id = %s;",
                (ticket_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert str(row["id"]) == ticket_id
            assert row["linked_incident_ids"] == []
    finally:
        conn.close()
