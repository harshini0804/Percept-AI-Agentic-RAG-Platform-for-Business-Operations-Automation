"""
Internal Mobility & Skill-Gap Matching Agent (Vertical 2, Section 8.2) —
local seed data loader, wired into backend/seed.py.
"""

import json
from pathlib import Path

from app.core.db import get_connection
from app.core.embeddings import upsert_embedding

SEED_DATA_ROOT = Path(__file__).resolve().parents[3] / "seed_data" / "internal_mobility"


def seed_internal_mobility() -> None:
    """
    Dedicated seeding for the Internal Mobility vertical (Section 8.2).

    Unlike the other verticals', Vertical 2's corpus is fundamentally
    RELATIONAL: the SQL pre-filter (Section 8.2 step 2) needs
    employees.department / employees.years_experience rows, the LLM's
    check_capacity() needs employee_workload rows, and role_matches
    rows refer back to employees/roles ids. So instead of copying
    files to the staging folder and letting ingest_staging_folder
    embed raw chunks (which would lose the source_id linkage to
    employees), this function:

      1. Reads a committed structured manifest
         (seed_data/internal_mobility/employees.json).
      2. Populates employees, roles, and employee_workload
         relationally (idempotently — re-running never duplicates).
      3. Embeds each employee's profile_text into the KB with
         source_id = the employee's id and
         source_type = 'employee_profile', so the graph's
         filtered semantic search (scoped to pre-filtered employee
         ids) can find them.

    Roles are stored relationally and NEVER embedded (Section 8.2
    decision mechanics: "Job listings are stored relationally and are
    never embedded").
    """
    manifest_path = SEED_DATA_ROOT / "employees.json"
    if not manifest_path.exists():
        print(f"  No internal_mobility manifest at {manifest_path}, skipping.")
        return

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    internal_mobility_root = manifest_path.parent

    def profile_text(emp: dict) -> str:
        # Source of truth for each profile's text is its committed
        # .txt corpus file (referenced by profile_file), matching the
        # dummy/other verticals' file-based seed_data convention. A
        # fallback profile_text key is accepted but not required.
        file_name = emp.get("profile_file")
        if file_name:
            text = (internal_mobility_root / file_name).read_text(encoding="utf-8")
        else:
            text = emp.get("profile_text", "")
        return text.strip()

    # ---- Phase 1: employees (relational) ----
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for emp in manifest["employees"]:
                cur.execute("SELECT id FROM employees WHERE name = %s;", (emp["name"],))
                if cur.fetchone() is not None:
                    continue
                cur.execute(
                    """
                    INSERT INTO employees (name, department, years_experience, location, profile_text)
                    VALUES (%s, %s, %s, %s, %s);
                    """,
                    (emp["name"], emp["department"], emp["years_experience"], emp["location"], profile_text(emp)),
                )
        conn.commit()
    finally:
        conn.close()
    print(f"  Synced {len(manifest['employees'])} employee relational row(s).")

    name_to_id: dict[str, str] = {}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for emp in manifest["employees"]:
                cur.execute("SELECT id FROM employees WHERE name = %s;", (emp["name"],))
                row = cur.fetchone()
                name_to_id[emp["name"]] = str(row["id"])
    finally:
        conn.close()

    # ---- Phase 2: employee_profile embeddings (source_id-link racked to employees) ----
    embedded = 0
    for emp in manifest["employees"]:
        emp_id = name_to_id[emp["name"]]

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM embeddings
                    WHERE vertical = 'internal_mobility'
                      AND source_type = 'employee_profile'
                      AND source_id = %s
                    LIMIT 1;
                    """,
                    (emp_id,),
                )
                already_embedded = cur.fetchone() is not None
        finally:
            conn.close()

        if already_embedded:
            continue

        embedded += 1
        upsert_embedding(
            vertical="internal_mobility",
            source_type="employee_profile",
            chunk_text=profile_text(emp),
            source_id=emp_id,
            metadata={
                "employee_name": emp["name"],
                "department": emp["department"],
            },
        )
    print(f"  Embedded {embedded} employee profile(s) into the KB.")

    # ---- Phase 3: roles (relational only, never embedded) ----
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for role in manifest.get("roles", []):
                cur.execute(
                    "SELECT id FROM roles WHERE title = %s AND description = %s;",
                    (role["title"], role.get("description")),
                )
                if cur.fetchone() is not None:
                    continue
                cur.execute(
                    """
                    INSERT INTO roles (title, description, department, min_experience, location)
                    VALUES (%s, %s, %s, %s, %s);
                    """,
                    (role["title"], role["description"], role["department"], role["min_experience"], role["location"]),
                )
        conn.commit()
    finally:
        conn.close()
    print(f"  Synced {len(manifest.get('roles', []))} role row(s) (relational only).")

    # ---- Phase 4: employee_workload (capacity signal) ----
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for wl in manifest.get("employee_workload", []):
                employee_id = name_to_id.get(wl["employee_name"])
                if employee_id is None:
                    continue
                cur.execute(
                    "SELECT id FROM employee_workload WHERE employee_id = %s;",
                    (employee_id,),
                )
                if cur.fetchone() is not None:
                    continue
                cur.execute(
                    """
                    INSERT INTO employee_workload (employee_id, utilization_pct, free_by_date)
                    VALUES (%s, %s, %s);
                    """,
                    (employee_id, wl.get("utilization_pct"), wl.get("free_by_date")),
                )
        conn.commit()
    finally:
        conn.close()
    print(f"  Synced {len(manifest.get('employee_workload', []))} workload row(s).")