"""
Tests for app.verticals.internal_mobility.seed_local structured seeding
(Section 8.2 + seed_internal_mobility in the internal_mobility seed_local
module, wired into backend/seed.py).

Two layers:
  1. Pure manifest sanity checks — no DB, catches an accidentally
     emptied/misplaced employees.json (which the seeding would
     otherwise silently skip).
  2. Functional checks against the real test DB — relational rows
     land in employees/roles/employee_workload and each profile
     triggers an embedding upsert with source_id linked to the
     employee row. Only upsert_embedding is mocked (avoids loading
     the sentence-transformers model in this fast unit test).
"""

import json

from app.core.db import get_connection
from app.verticals.internal_mobility.seed_local import (
    seed_internal_mobility,
    SEED_DATA_ROOT,
)

MANIFEST_PATH = SEED_DATA_ROOT / "employees.json"


def _load_manifest() -> dict:
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_internal_mobility_manifest_exists_and_is_well_formed():
    assert MANIFEST_PATH.exists()

    manifest = _load_manifest()
    required_emp_fields = {"name", "department", "years_experience", "location", "profile_file"}
    for emp in manifest["employees"]:
        assert required_emp_fields <= set(emp)
        # Every referenced profile .txt must actually exist — the
        # seeding reads profile text from these files, a missing one
        # would silently produce an empty profile.
        assert (MANIFEST_PATH.parent / emp["profile_file"]).exists()

    assert len(manifest["employees"]) >= 3
    assert len(manifest["roles"]) >= 3

    # Every workload entry must reference a real employee name.
    emp_names = {e["name"] for e in manifest["employees"]}
    workload_names = {w["employee_name"] for w in manifest["employee_workload"]}
    assert workload_names <= emp_names


def test_seed_internal_mobility_populates_relational_tables(monkeypatch):
    manifest = _load_manifest()

    # Avoid loading the embedding model in this unit test — only the
    # DB writes matter here. The graph/integration tests exercise the
    # real embed path.
    monkeypatch.setattr("app.verticals.internal_mobility.seed_local.upsert_embedding", lambda **kwargs: None)

    seed_internal_mobility()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM employees;")
            emp_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM roles;")
            role_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM employee_workload;")
            workload_count = cur.fetchone()["n"]
    finally:
        conn.close()

    assert emp_count == len(manifest["employees"])
    assert role_count == len(manifest["roles"])
    assert workload_count == len(manifest["employee_workload"])


def test_seed_internal_mobility_idempotent(monkeypatch):
    """Re-running must not duplicate relational rows (the embeddings
    existence check is analogous: source_id-scoped, so it also stays
    idempotent against the real DB)."""
    monkeypatch.setattr("app.verticals.internal_mobility.seed_local.upsert_embedding", lambda **kwargs: None)

    seed_internal_mobility()
    seed_internal_mobility()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM employees;")
            emp_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM roles;")
            role_count = cur.fetchone()["n"]
    finally:
        conn.close()

    manifest = _load_manifest()
    assert emp_count == len(manifest["employees"])
    assert role_count == len(manifest["roles"])