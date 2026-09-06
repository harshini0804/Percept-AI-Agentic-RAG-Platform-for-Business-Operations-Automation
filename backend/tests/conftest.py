"""
Shared pytest fixtures for the backend test suite.

IMPORTANT: DATABASE_URL must be set BEFORE any `app.*` module is
imported anywhere in the test session — app/core/db.py reads it into
a module-level constant at import time, not per-call. This file is
always imported first by pytest (before test module collection), so
setting it here, before any `from app...` import below, is what makes
every test correctly target the test database instead of accidentally
touching the real dev database or failing to resolve the `postgres`
hostname (which only exists inside the Docker Compose network).

Local runs: point DATABASE_URL at a dedicated test database (NOT your
dev database), e.g.:
    postgresql://rag_user:rag_password@localhost:5432/rag_platform_test
Create it once with:
    docker exec -it rag_postgres psql -U rag_user -d rag_platform \
        -c "CREATE DATABASE rag_platform_test;"

CI: DATABASE_URL is set as a job-level env var pointing at the
Postgres service container (see .github/workflows/ci.yml) — the
os.environ.setdefault below never overrides that.
"""

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://rag_user:rag_password@localhost:5432/rag_platform_test",
)

import pathlib
import psycopg2
import pytest

from app.core.db import get_connection
from app.core.logging_service import create_agent_run

SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[1] / "db" / "schema.sql"

# Tables to wipe between tests, in FK-safe (children-first) order.
# Keeping this explicit (rather than a blanket TRUNCATE ... CASCADE)
# means a forgotten new table here fails loudly via a leftover-row
# test failure instead of silently going unreset.
TABLES_IN_DELETE_ORDER = [
    "agent_decisions",
    "escalations",
    "notifications",
    "incident_tickets",
    "role_matches",
    "employee_workload",
    "obligations",
    "action_items",
    "agent_runs",
    "embeddings",
    "kb_sync_state",
    "incidents",
    "employees",
    "roles",
    "contracts",
    "meetings",
    "documents",
]


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema():
    """
    Runs once per test session. Creates the schema if the test
    database is empty (checked via a marker table), so a fresh test
    database "just works" without a manual setup step beyond
    creating the (empty) database itself.
    """
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'documents'
                );
                """
            )
            schema_exists = cur.fetchone()[0]

            if not schema_exists:
                sql = SCHEMA_PATH.read_text(encoding="utf-8")
                cur.execute(sql)
    finally:
        conn.close()

    yield


@pytest.fixture(autouse=True)
def _clean_tables():
    """
    Runs before EVERY test. Deletes all rows (not the tables
    themselves) so each test starts from an empty, known state,
    without needing to recreate the schema per test (slow).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for table in TABLES_IN_DELETE_ORDER:
                cur.execute(f"DELETE FROM {table};")
        conn.commit()
    finally:
        conn.close()

    yield


@pytest.fixture
def existing_run_id() -> str:
    """
    Creates a real agent_runs row and returns its id. Several shared-
    core functions (log_decision, create_escalation, create_notification)
    have a NOT NULL foreign key to agent_runs.id, so tests exercising
    them need a valid run to attach to.
    """
    return create_agent_run(vertical="dummy", trigger_type="upload")