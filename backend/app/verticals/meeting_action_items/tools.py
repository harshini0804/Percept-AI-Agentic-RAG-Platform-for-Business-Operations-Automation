"""
Meeting Action-Item Enforcement (Vertical 4, Section 8.4) — tools.

Both tools here are write tools whose invocation is driven by
followup.py's deterministic state machine (verdict + nudge_count +
escalated), not by an LLM's own free choice — matching every other
vertical's pattern: the LLM reasons/judges, code decides what action
that judgment leads to (see action_gate_node for the same pattern in
the shared orchestration engine). They're still registered via @tool
like any other vertical's tools, for consistency with the shared
registry and the evaluation harness's tool-call metrics.

run_id is always supplied by the calling code (never by an LLM's
tool-call arguments) — it's system context needed for the
notifications FK, not something a tool-calling LLM should be
deciding. Only action_item_id is exposed in the tool's schema.

Manager resolution (escalate_to_manager) uses a static, local
owner -> manager mapping — a deliberate simplification (Option B,
confirmed): the schema has no employee/manager table, and adding one
for a single notification field was judged not worth a shared schema
change. Falls back to a clearly-synthetic placeholder for any owner
not in the map, so this never raises on an unmapped name.

Invocation note for followup.py (Trigger 2, PR 3): since these tools
are never offered to an LLM's own tool-call choice (the action-firing
decision is followup.py's own deterministic state machine), the
caller must construct the full arguments dict itself before calling
execute_tool() — e.g.
    execute_tool("meeting_action_items", "send_nudge",
                 {"run_id": run_id, "action_item_id": item_id})
— the same pattern the dummy vertical's finalize_node already uses
for action_tool_args, rather than passing an LLM's raw tool-call
output straight through.
"""

from app.core.tool_registry import tool
from app.core.db import get_connection
from app.core.logging_service import create_notification

# Static synthetic owner -> manager mapping (Option B). Extend this
# as synthetic seed data introduces new owner names (Phase: seed data
# PR). Not a real org chart — purely for realistic-looking demo
# notifications.
MANAGER_MAP = {
    "alex": "jordan.lee@example.com",
    "priya": "sam.chen@example.com",
    "morgan": "casey.patel@example.com",
    "jamie": "riley.nguyen@example.com",
}


def _fetch_action_item(action_item_id: str) -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM action_items WHERE id = %s;", (action_item_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise ValueError(f"No action_item found with id '{action_item_id}'.")
    return row


def _resolve_manager(owner: str) -> str:
    return MANAGER_MAP.get(owner.lower(), f"manager-of-{owner}@example.com")


@tool(
    vertical="meeting_action_items",
    name="send_nudge",
    description=(
        "Send a reminder notification to an action item's owner, and "
        "increment its nudge count."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action_item_id": {"type": "string", "description": "The action item's id"}
        },
        "required": ["action_item_id"],
    },
    tool_type="write",
)
def send_nudge(run_id: str, action_item_id: str) -> dict:
    item = _fetch_action_item(action_item_id)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE action_items SET nudge_count = nudge_count + 1 WHERE id = %s "
                "RETURNING nudge_count;",
                (action_item_id,),
            )
            new_count = cur.fetchone()["nudge_count"]
        conn.commit()
    finally:
        conn.close()

    recipient = f"{item['owner']}@example.com"
    message = f"Reminder: your action item is still open — \"{item['description']}\""
    create_notification(run_id, recipient=recipient, message=message)

    return {"action_item_id": action_item_id, "nudge_count": new_count, "notified": recipient}


@tool(
    vertical="meeting_action_items",
    name="escalate_to_manager",
    description=(
        "Escalate an overdue, already-nudged action item to the owner's "
        "manager, and mark it as escalated."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action_item_id": {"type": "string", "description": "The action item's id"}
        },
        "required": ["action_item_id"],
    },
    tool_type="write",
)
def escalate_to_manager(run_id: str, action_item_id: str) -> dict:
    item = _fetch_action_item(action_item_id)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE action_items SET escalated = TRUE WHERE id = %s;",
                (action_item_id,),
            )
        conn.commit()
    finally:
        conn.close()

    manager_email = _resolve_manager(item["owner"])
    message = (
        f"Escalation: {item['owner']}'s action item is overdue and unresolved "
        f"after repeated reminders — \"{item['description']}\""
    )
    create_notification(run_id, recipient=manager_email, message=message)

    return {"action_item_id": action_item_id, "escalated": True, "notified": manager_email}