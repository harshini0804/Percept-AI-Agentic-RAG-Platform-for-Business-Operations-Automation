"""
Tests for app.core.vertical_registry (Section 7, point 11).

Pure in-memory — no database or network needed.
"""

import pytest
from app.core.vertical_registry import (
    register_vertical,
    get_registered_verticals,
    run_vertical,
)


def test_register_and_run_vertical():
    def fake_run(input_text):
        return {"received": input_text, "escalated": False}

    register_vertical("test_vreg_v1", fake_run)

    assert "test_vreg_v1" in get_registered_verticals()
    result = run_vertical("test_vreg_v1", "hello")
    assert result == {"received": "hello", "escalated": False}


def test_run_unregistered_vertical_raises_keyerror_with_helpful_message():
    with pytest.raises(KeyError, match="No vertical named 'test_vreg_nonexistent'"):
        run_vertical("test_vreg_nonexistent", "input")


def test_duplicate_vertical_registration_raises_valueerror():
    register_vertical("test_vreg_v2", lambda x: {})

    with pytest.raises(ValueError, match="already registered"):
        register_vertical("test_vreg_v2", lambda x: {})