"""
Seed script (Section 9.2, point 17) — "copies each vertical's
committed synthetic data into its staging folder and runs the shared
ingestion function once, populating the KB and relational tables for
local development."

Usage (from inside the backend container, or any environment with
DATABASE_URL and STAGING_ROOT correctly configured):

    python seed.py

Safe to re-run: ingest_staging_folder()'s hash-based change detection
(Section 6.3) means files already seeded and unchanged are skipped,
not re-embedded, on subsequent runs.
"""

import json
import shutil
from pathlib import Path

from app.core.db import get_connection
from app.core.embeddings import upsert_embedding
from app.core.ingestion import ingest_staging_folder, STAGING_ROOT

# Synthetic data committed to the repo under backend/seed_data/,
# copied into each vertical's staging folder before ingestion. Only
# "dummy" has real seed data right now (Section 6.1's synthetic
# corpora for the four real verticals get added by each vertical
# owner once their own vertical is built).
SEED_DATA_ROOT = Path(__file__).parent / "seed_data"

# internal_mobility is handled by seed_internal_mobility() (dedicated
# structured seeding — it must NOT go through the generic copy-to-
# staging path, see that function's docstring), so it is intentionally
# absent from VERTICALS_TO_SEED, which only drives the generic
# staging-folder path.
VERTICALS_TO_SEED = [
    {"vertical": "dummy", "source_type": "postmortem"},
]


def seed_vertical(vertical: str, source_type: str) -> None:
    source_dir = SEED_DATA_ROOT / vertical
    target_dir = STAGING_ROOT / vertical

    if not source_dir.exists():
        print(f"  No seed data found for '{vertical}' at {source_dir}, skipping.")
        return

    target_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for file in sorted(source_dir.iterdir()):
        if file.is_file():
            shutil.copy(file, target_dir / file.name)
            copied += 1
    print(f"  Copied {copied} file(s) into {target_dir}")

    summary = ingest_staging_folder(vertical=vertical, source_type=source_type)
    print(f"  Ingested: processed={len(summary['processed'])}, "
          f"skipped={len(summary['skipped'])}, errors={summary['errors']}")


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
    manifest_path = SEED_DATA_ROOT / "internal_mobility" / "employees.json"
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
    embedded = sum(
        1 for emp in manifest["employees"] if emp["name"] in name_to_id
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


def main():
    print("Seeding knowledge base from committed synthetic data...")
    for entry in VERTICALS_TO_SEED:
        print(f"\n{entry['vertical']}:")
        seed_vertical(entry["vertical"], entry["source_type"])

    print("\ninternal_mobility:")
    seed_internal_mobility()

    print("\nSeeding complete.")


if __name__ == "__main__":
    main()