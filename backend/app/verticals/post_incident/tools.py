"""
Post-Incident Knowledge Synthesis — Domain Tools (Section 8.1)

Three tools registered for vertical="post_incident":

READ tools (called during LLM reasoning to gather context):
  - lookup_incidents_by_service: queries the incidents table by service
    name — lets the LLM check "has this service had issues before?"
  - get_incident_details: fetches full detail for a specific incident ID

WRITE tool (fired through the confidence gate if confidence >= threshold):
  - create_incident_ticket: inserts into incident_tickets with linked
    historical incident IDs — this is the autonomous remediation action
"""

import json
from psycopg2.extras import Json
from app.core.tool_registry import tool
from app.core.db import get_connection


# -------------------------------------------------------------------
# Read Tool 1 — lookup_incidents_by_service
# -------------------------------------------------------------------

@tool(
    vertical="post_incident",
    name="lookup_incidents_by_service",
    description=(
        "Query historical incidents by microservice name. Returns a list "
        "of past incidents (id, title, root_cause_tag, date) for the "
        "given service. Use this to check whether a service has had "
        "recurring issues."
    ),
    parameters={
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": "The microservice or system name to look up, e.g. 'payments-service'.",
            }
        },
        "required": ["service"],
    },
    tool_type="read",
)
def lookup_incidents_by_service(service: str) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, root_cause_tag, service, date
                FROM incidents
                WHERE LOWER(service) = LOWER(%s)
                ORDER BY date DESC
                LIMIT 20;
                """,
                (service,),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": str(r["id"]),
                    "title": r["title"],
                    "root_cause_tag": r["root_cause_tag"],
                    "service": r["service"],
                    "date": r["date"].isoformat() if r["date"] else None,
                }
                for r in rows
            ]
    finally:
        conn.close()


# -------------------------------------------------------------------
# Read Tool 2 — get_incident_details
# -------------------------------------------------------------------

@tool(
    vertical="post_incident",
    name="get_incident_details",
    description=(
        "Fetch full details for a specific historical incident by its "
        "UUID. Returns the incident's title, root cause tag, service, "
        "date, and linked document ID."
    ),
    parameters={
        "type": "object",
        "properties": {
            "incident_id": {
                "type": "string",
                "description": "The UUID of the incident to look up.",
            }
        },
        "required": ["incident_id"],
    },
    tool_type="read",
)
def get_incident_details(incident_id: str) -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, root_cause_tag, service, date, doc_id
                FROM incidents
                WHERE id = %s;
                """,
                (incident_id,),
            )
            row = cur.fetchone()
            if row is None:
                return {"error": f"No incident found with id '{incident_id}'."}
            return {
                "id": str(row["id"]),
                "title": row["title"],
                "root_cause_tag": row["root_cause_tag"],
                "service": row["service"],
                "date": row["date"].isoformat() if row["date"] else None,
                "doc_id": str(row["doc_id"]) if row["doc_id"] else None,
            }
    finally:
        conn.close()


# -------------------------------------------------------------------
# Write Tool — create_incident_ticket
# -------------------------------------------------------------------

@tool(
    vertical="post_incident",
    name="create_incident_ticket",
    description=(
        "Create a remediation / follow-up ticket for a detected incident "
        "pattern. Provide a descriptive title and optionally link it to "
        "historical incident IDs that share the same root cause. This is "
        "the autonomous action the agent fires when confidence is above "
        "the threshold."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Descriptive title for the remediation ticket.",
            },
            "linked_incident_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "UUIDs of historical incidents linked to this ticket.",
            },
            "run_id": {
                "type": "string",
                "description": "UUID of the current agent run triggering this ticket.",
            },
        },
        "required": ["title"],
    },
    tool_type="write",
)
def create_incident_ticket(
    title: str,
    linked_incident_ids: list[str] | None = None,
    run_id: str | None = None,
) -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            ids = [str(x) for x in linked_incident_ids] if linked_incident_ids else []
            cur.execute(
                """
                INSERT INTO incident_tickets (run_id, title, linked_incident_ids, status)
                VALUES (%s, %s, %s::uuid[], %s)
                RETURNING id;
                """,
                (run_id, title, ids, "open"),
            )
            ticket_id = cur.fetchone()["id"]
        conn.commit()
        return {
            "ticket_id": str(ticket_id),
            "title": title,
            "status": "open",
            "run_id": run_id,
        }
    finally:
        conn.close()
