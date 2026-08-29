"""
Dummy vertical tools — not a real vertical, exists only to prove the
shared orchestration pieces compose into a working LangGraph pipeline
end to end (Section 10, Phase 1 checkpoint).
"""

from app.core.tool_registry import tool


@tool(
    vertical="dummy",
    name="log_dummy_action",
    description="Records that the dummy action was taken.",
    parameters={
        "type": "object",
        "properties": {"note": {"type": "string"}},
        "required": ["note"],
    },
    tool_type="write",
)
def log_dummy_action(note: str) -> dict:
    return {"logged": True, "note": note}