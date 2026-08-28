"""
Tool-Calling Framework (Section 3.1, 7.2)

A shared registry every vertical's read/write tools plug into. Tools
are plain Python functions (per Section 3.1: "not external API
integrations") decorated with @tool, which:
  1. Registers the function under (vertical, tool_name).
  2. Records the metadata (description, parameters) needed to
     describe the tool to an LLM tool-calling API.

The orchestration engine calls:
  - get_tools_for_vertical(vertical) before the LLM reasoning step,
    to know what tools are available.
  - execute_tool(vertical, tool_name, arguments) after the LLM
    decides to call one.
"""

from typing import Callable, Literal, Any
from functools import wraps

# Internal registry: {(vertical, tool_name): {"func": ..., "schema": {...}}}
_registry: dict[tuple[str, str], dict[str, Any]] = {}


def tool(
    vertical: str,
    name: str,
    description: str,
    parameters: dict,
    tool_type: Literal["read", "write"] = "read",
):
    """
    Decorator for registering a vertical's tool function.

    Args:
        vertical: one of the four vertical identifiers (Section 4.3).
        name: the tool's name as the LLM will refer to it.
        description: shown to the LLM to help it decide when to call this.
        parameters: JSON-schema-style dict describing the function's
            arguments, e.g.:
            {
                "type": "object",
                "properties": {
                    "incident_id": {"type": "string"}
                },
                "required": ["incident_id"]
            }
        tool_type: "read" or "write" — metadata only, used by the
            evaluation harness (Section 11) to track tool-call
            frequency by type. Not enforced at the framework level.
    """
    def decorator(func: Callable) -> Callable:
        key = (vertical, name)
        if key in _registry:
            raise ValueError(
                f"Tool '{name}' is already registered for vertical '{vertical}'."
            )

        _registry[key] = {
            "func": func,
            "schema": {
                "name": name,
                "description": description,
                "parameters": parameters,
                "tool_type": tool_type,
            },
        }

        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    return decorator


def get_tools_for_vertical(vertical: str) -> list[dict]:
    """
    Returns the LLM-facing schema (name, description, parameters) for
    every tool registered under this vertical. This is what gets
    passed to the LLM API call (Claude/Groq, Section 2) as the
    available tools for this run.
    """
    return [
        entry["schema"]
        for (tool_vertical, _), entry in _registry.items()
        if tool_vertical == vertical
    ]


def execute_tool(vertical: str, tool_name: str, arguments: dict) -> Any:
    """
    Called after the LLM decides to invoke a tool. Looks up the
    actual registered function and calls it with the given arguments.

    Raises KeyError if no such tool is registered for this vertical —
    this should never happen if get_tools_for_vertical was used
    correctly to build the LLM's tool list, but fails loudly rather
    than silently if it does.
    """
    key = (vertical, tool_name)
    if key not in _registry:
        raise KeyError(
            f"No tool named '{tool_name}' registered for vertical '{vertical}'."
        )

    func = _registry[key]["func"]
    return func(**arguments)