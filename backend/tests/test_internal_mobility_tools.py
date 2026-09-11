"""
Tests for app.verticals.internal_mobility.tools. Uses the real test
database (see tests/conftest.py) — these tools do direct SQL, so
mocking the DB would test nothing meaningful.
"""

import pytest

from app.core.db import get_connection
from app.core.tool_registry import get_tools_for_vertical
from app.core.logging_service import create_agent_run
from app.verticals.internal_mobility.tools import (
    record_role_match,
    check_capacity,
    notify_candidate,
    _employee_email,
    _resolve_recipient,
)


@pytest.fixture
def employee_id() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO employees (name, department, years_experience, location, profile_text)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
                """,
                ("Ayesha Rao", "Engineering", 6, "Bangalore", "Senior backend engineer."),
            )
            new_id = str(cur.fetchone()["id"])
        conn.commit()
        return new_id
    finally:
        conn.close()


@pytest.fixture
def role_id() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO roles (title, description, department, min_experience, location)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
                """,
                ("Staff Backend Engineer", "Backend role", "Engineering", 5, "Bangalore"),
            )
            new_id = str(cur.fetchone()["id"])
        conn.commit()
        return new_id
    finally:
        conn.close()


def test_tools_are_registered_for_internal_mobility():
    names = {t["name"] for t in get_tools_for_vertical("internal_mobility")}
    assert names == {"check_capacity", "notify_candidate"}


def test_check_capacity_returns_workload(employee_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO employee_workload (employee_id, utilization_pct, free_by_date)
                VALUES (%s, %s, %s);
                """,
                (employee_id, 85, "2026-10-01"),
            )
        conn.commit()
    finally:
        conn.close()

    result = check_capacity(employee_id=employee_id)

    assert result["found"] is True
    assert result["utilization_pct"] == 85
    assert result["free_by_date"] == "2026-10-01"
    # 85% utilization => still available (util < 100)
    assert result["available"] is True


def test_check_capacity_at_full_utilization_is_not_available(employee_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO employee_workload (employee_id, utilization_pct, free_by_date)
                VALUES (%s, %s, %s);
                """,
                (employee_id, 100, None),
            )
        conn.commit()
    finally:
        conn.close()

    result = check_capacity(employee_id=employee_id)

    assert result["found"] is True
    assert result["available"] is False


def test_check_capacity_returns_not_found_for_unknown_employee():
    result = check_capacity(employee_id="00000000-0000-0000-0000-000000000000")
    assert result["found"] is False
    assert result["available"] is None


def test_resolve_recipient_fails_loudly_for_missing_employee():
    with pytest.raises(ValueError, match="cannot derive a recipient email"):
        _resolve_recipient("00000000-0000-0000-0000-000000000000")


def test_employee_email_sanitizes_name():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO employees (name, department, years_experience, location, profile_text)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
                """,
                ("O'Brien  García", "Engineering", 5, "Bangalore", "Backend engineer."),
            )
            tricky_id = str(cur.fetchone()["id"])
        conn.commit()
    finally:
        conn.close()

    assert _employee_email(tricky_id) == "o.brien.garcia@example.com"


def test_notify_candidate_creates_match_and_notification(employee_id, role_id):
    run_id = create_agent_run(vertical="internal_mobility", trigger_type="upload")

    result = notify_candidate(
        employee_id=employee_id,
        role_id=role_id,
        run_id=run_id,
        rank=1,
        rationale="Strong backend fit.",
        confidence=0.92,
        message="Great fit for Staff Backend Engineer — apply!",
    )

    assert result["notified"] is True
    assert result["employee_id"] == employee_id
    assert result["role_id"] == role_id

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rank, rationale, confidence, notified FROM role_matches
                WHERE run_id = %s AND employee_id = %s AND role_id = %s;
                """,
                (run_id, employee_id, role_id),
            )
            match = cur.fetchone()

            cur.execute(
                "SELECT recipient, message FROM notifications WHERE run_id = %s;",
                (run_id,),
            )
            note = cur.fetchone()
    finally:
        conn.close()

    assert match is not None
    assert match["rank"] == 1
    assert match["confidence"] == pytest.approx(0.92)
    assert match["notified"] is True

    assert note is not None
    assert note["recipient"] == "ayesha.rao@example.com"
    assert note["message"] == "Great fit for Staff Backend Engineer — apply!"


def test_record_role_match_logs_unnotified(employee_id, role_id):
    run_id = create_agent_run(vertical="internal_mobility", trigger_type="upload")

    match_id = record_role_match(
        run_id=run_id,
        role_id=role_id,
        employee_id=employee_id,
        rank=2,
        rationale="Decent fit, some skill gaps.",
        confidence=0.55,
        notified=False,
    )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT notified FROM role_matches WHERE id = %s;", (match_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    assert row["notified"] is False


def test_record_role_match_idempotent_on_rerun(employee_id, role_id):
    """Same (run, role, employee) should update, not duplicate."""
    run_id = create_agent_run(vertical="internal_mobility", trigger_type="upload")

    first = record_role_match(
        run_id=run_id, role_id=role_id, employee_id=employee_id,
        rank=1, rationale="fit", confidence=0.8, notified=False,
    )
    second = record_role_match(
        run_id=run_id, role_id=role_id, employee_id=employee_id,
        rank=1, rationale="fit", confidence=0.8, notified=True,
    )

    assert first == second

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM role_matches WHERE run_id = %s;",
                (run_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row["n"] == 1