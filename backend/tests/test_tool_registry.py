"""
Tests for app.core.tool_registry (Section 3.1, 7.2).

Pure in-memory — no database or network needed. Each test uses a
unique vertical name so tests can't collide with each other via the
registry's shared, module-level, never-cleared dict.
"""

import pytest
from app.core.tool_registry import tool, get_tools_for_vertical, execute_tool


def test_register_and_retrieve_tool_schema():
    @tool(
        vertical="test_registry_v1",
        name="sample_tool",
        description="A sample tool.",
        parameters={"type": "object", "properties": {}},
        tool_type="read",
    )
    def sample_tool():
        return {"ok": True}

    schemas = get_tools_for_vertical("test_registry_v1")
    assert len(schemas) == 1
    assert schemas[0]["name"] == "sample_tool"
    assert schemas[0]["description"] == "A sample tool."
    assert schemas[0]["tool_type"] == "read"


def test_execute_tool_calls_the_real_function():
    @tool(
        vertical="test_registry_v2",
        name="add_numbers",
        description="Adds two numbers.",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
        tool_type="read",
    )
    def add_numbers(a, b):
        return {"sum": a + b}

    result = execute_tool("test_registry_v2", "add_numbers", {"a": 2, "b": 3})
    assert result == {"sum": 5}


def test_get_tools_for_unregistered_vertical_returns_empty_list():
    assert get_tools_for_vertical("test_registry_nonexistent") == []


def test_execute_unregistered_tool_raises_keyerror():
    with pytest.raises(KeyError, match="not.*registered"):
        execute_tool("test_registry_v3", "does_not_exist", {})


def test_duplicate_registration_raises_valueerror():
    @tool(
        vertical="test_registry_v4",
        name="dup_tool",
        description="First registration.",
        parameters={"type": "object", "properties": {}},
    )
    def first():
        return {}

    with pytest.raises(ValueError, match="already registered"):
        @tool(
            vertical="test_registry_v4",
            name="dup_tool",
            description="Second registration, should fail.",
            parameters={"type": "object", "properties": {}},
        )
        def second():
            return {}


def test_same_tool_name_different_vertical_is_allowed():
    """Tools are keyed by (vertical, name) — the same name should be
    reusable across different verticals without collision."""

    @tool(
        vertical="test_registry_v5a",
        name="shared_name",
        description="v5a's version.",
        parameters={"type": "object", "properties": {}},
    )
    def tool_a():
        return {"from": "v5a"}

    @tool(
        vertical="test_registry_v5b",
        name="shared_name",
        description="v5b's version.",
        parameters={"type": "object", "properties": {}},
    )
    def tool_b():
        return {"from": "v5b"}

    assert execute_tool("test_registry_v5a", "shared_name", {}) == {"from": "v5a"}
    assert execute_tool("test_registry_v5b", "shared_name", {}) == {"from": "v5b"}