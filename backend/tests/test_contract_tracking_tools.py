"""
Tests for app.verticals.contract_tracking.tools. Uses the real test
database (see tests/conftest.py) — these tools do direct SQL, so
mocking the DB would test nothing meaningful.
"""

import pytest

from app.core.db import get_connection
from app.core.embeddings import upsert_embedding
from app.core.tool_registry import get_tools_for_vertical
from app.verticals.contract_tracking.tools import (
    get_surrounding_clauses,
    search_similar_contracts,
    create_calendar_reminder,
    flag_for_manual_review,
)


@pytest.fixture
def contract_id() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO contracts (vendor_name) VALUES (%s) RETURNING id;",
                ("Acme Vendor Co",),
            )
            new_id = str(cur.fetchone()["id"])
        conn.commit()
        return new_id
    finally:
        conn.close()


@pytest.fixture
def obligation_id(contract_id) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO obligations (contract_id, description, obligation_date, type, confidence)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (contract_id, "Renew within 30 days of expiry", "2027-01-15", "renewal", 0.9),
            )
            new_id = str(cur.fetchone()["id"])
        conn.commit()
        return new_id
    finally:
        conn.close()


def test_tools_are_registered_for_contract_tracking():
    names = {t["name"] for t in get_tools_for_vertical("contract_tracking")}
    assert names == {
        "get_surrounding_clauses",
        "search_similar_contracts",
        "create_calendar_reminder",
        "flag_for_manual_review",
    }


def test_get_surrounding_clauses_finds_matching_clause(contract_id):
    upsert_embedding(
        vertical="contract_tracking",
        source_type="contract_clause",
        chunk_text="Either party may terminate with 90 days written notice.",
        source_id=contract_id,
        metadata={"clause_number": "4.2", "title": "Termination"},
    )

    result = get_surrounding_clauses(contract_id=contract_id, clause_number="4.2")

    assert result["found"] is True
    assert result["title"] == "Termination"
    assert "90 days" in result["text"]


def test_get_surrounding_clauses_returns_not_found_for_missing_clause(contract_id):
    result = get_surrounding_clauses(contract_id=contract_id, clause_number="99")
    assert result == {"found": False, "clause_number": "99", "text": None}


def test_get_surrounding_clauses_does_not_match_other_contracts(contract_id):
    """A clause_number match in a DIFFERENT contract must never leak in —
    this is a same-document lookup, not a cross-contract search."""
    upsert_embedding(
        vertical="contract_tracking",
        source_type="contract_clause",
        chunk_text="Unrelated clause from a different contract.",
        source_id="00000000-0000-0000-0000-000000000000",
        metadata={"clause_number": "4.2", "title": "Other"},
    )

    result = get_surrounding_clauses(contract_id=contract_id, clause_number="4.2")
    assert result["found"] is False


def test_search_similar_contracts_returns_ranked_matches(contract_id):
    upsert_embedding(
        vertical="contract_tracking",
        source_type="contract_clause",
        chunk_text="Confidential information must not be disclosed to third parties.",
        source_id=contract_id,
        metadata={"clause_number": "3", "title": "Confidentiality"},
    )

    result = search_similar_contracts(query_text="non-disclosure of confidential data", top_k=3)

    assert len(result["matches"]) >= 1
    assert result["matches"][0]["clause_number"] == "3"
    assert "similarity" in result["matches"][0]


def test_create_calendar_reminder_marks_obligation(obligation_id):
    result = create_calendar_reminder(obligation_id=obligation_id)

    assert result["reminder_created"] is True
    assert result["obligation_id"] == obligation_id

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT reminder_created FROM obligations WHERE id = %s;", (obligation_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    assert row["reminder_created"] is True


def test_create_calendar_reminder_raises_for_unknown_obligation():
    with pytest.raises(ValueError, match="No obligation found"):
        create_calendar_reminder(obligation_id="00000000-0000-0000-0000-000000000000")


def test_flag_for_manual_review_creates_escalation(existing_run_id, obligation_id):
    result = flag_for_manual_review(
        obligation_id=obligation_id,
        run_id=existing_run_id,
        reason="Extraction confidence below threshold.",
    )

    assert result["escalated"] is True
    assert result["obligation_id"] == obligation_id

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reason, pending_action FROM escalations WHERE id = %s;",
                (result["escalation_id"],),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row["reason"] == "Extraction confidence below threshold."
    assert row["pending_action"]["tool_name"] == "create_calendar_reminder"
    assert row["pending_action"]["arguments"] == {"obligation_id": obligation_id}
