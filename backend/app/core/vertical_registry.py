"""
Vertical Registry (Section 7, point 11) — lets each vertical register
its own "run" entry point (e.g. run_dummy_vertical), so the shared
POST /agent-runs endpoint can invoke any vertical generically without
hardcoding which verticals exist. Same registration pattern as
tool_registry.py.
"""

from typing import Callable, Any

# {vertical_name: run_function}
_vertical_registry: dict[str, Callable[..., dict]] = {}


def register_vertical(vertical: str, run_fn: Callable[..., dict]) -> None:
    """
    Registers a vertical's entry-point function. Called once per
    vertical, typically right after the function definition in that
    vertical's graph.py — mirroring how @tool registers at import
    time in tool_registry.py.
    """
    if vertical in _vertical_registry:
        raise ValueError(f"Vertical '{vertical}' is already registered.")
    _vertical_registry[vertical] = run_fn


def get_registered_verticals() -> list[str]:
    """Returns the names of all currently registered verticals."""
    return list(_vertical_registry.keys())


def run_vertical(vertical: str, input_text: str) -> dict[str, Any]:
    """
    Looks up and invokes the given vertical's run function. Raises
    KeyError if no such vertical is registered — same fail-loud
    pattern as tool_registry.execute_tool().
    """
    if vertical not in _vertical_registry:
        raise KeyError(
            f"No vertical named '{vertical}' is registered. "
            f"Registered verticals: {get_registered_verticals()}"
        )
    return _vertical_registry[vertical](input_text)