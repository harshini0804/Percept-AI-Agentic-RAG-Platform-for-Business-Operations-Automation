"""
Tests for app.core.llm_gateway. No real network calls — the Groq
client itself is monkeypatched, so these run instantly and need no
GROQ_API_KEY.
"""

import httpx
import pytest
from groq import BadRequestError

from app.core.llm_gateway import call_llm, _format_tools_for_groq


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeToolCallFunction:
    def __init__(self, name, arguments_json):
        self.name = name
        self.arguments = arguments_json


class _FakeToolCall:
    def __init__(self, name, arguments_json):
        self.function = _FakeToolCallFunction(name, arguments_json)


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


def _make_bad_request_error(message: str) -> BadRequestError:
    """
    Builds a real groq.BadRequestError with a message shaped exactly
    like the SDK's own error construction (Section: see
    _make_status_error_from_response in groq's base client) — the
    real phantom-tool-call bug's message looks like:
    "Error code: 400 - {'error': {..., 'code': 'tool_use_failed'}}"
    """
    fake_http_response = httpx.Response(
        status_code=400, request=httpx.Request("POST", "https://api.groq.com/x")
    )
    return BadRequestError(message, response=fake_http_response, body=None)


def test_call_llm_returns_plain_text_response(monkeypatch):
    fake_client = type("FakeClient", (), {})()
    fake_client.chat = type("FakeChat", (), {})()
    fake_client.chat.completions = type("FakeCompletions", (), {})()
    fake_client.chat.completions.create = lambda **kwargs: _FakeResponse(
        _FakeMessage(content="Hello there.")
    )

    monkeypatch.setattr("app.core.llm_gateway._get_client", lambda: fake_client)

    result = call_llm(messages=[{"role": "user", "content": "hi"}])
    assert result == {"content": "Hello there.", "tool_calls": []}


def test_call_llm_returns_tool_calls(monkeypatch):
    fake_client = type("FakeClient", (), {})()
    fake_client.chat = type("FakeChat", (), {})()
    fake_client.chat.completions = type("FakeCompletions", (), {})()
    fake_client.chat.completions.create = lambda **kwargs: _FakeResponse(
        _FakeMessage(
            content=None,
            tool_calls=[_FakeToolCall("get_ticket_status", '{"incident_id": "INC-042"}')],
        )
    )

    monkeypatch.setattr("app.core.llm_gateway._get_client", lambda: fake_client)

    tools = [{"name": "get_ticket_status", "description": "...", "parameters": {}}]
    result = call_llm(messages=[{"role": "user", "content": "check it"}], tools=tools)

    assert result["content"] is None
    assert result["tool_calls"] == [
        {"name": "get_ticket_status", "arguments": {"incident_id": "INC-042"}}
    ]


def test_call_llm_recovers_from_gpt_oss_phantom_tool_bug(monkeypatch):
    """
    Regression test for the real bug hit during development: gpt-oss
    models on Groq sometimes emit an internal reasoning artifact that
    gets misparsed as a call to a tool that was never offered (seen
    with phantom names "commentary" and "json"). call_llm should
    catch this specific signature and transparently retry once
    without tools, succeeding on the second attempt.
    """
    phantom_error = _make_bad_request_error(
        "Error code: 400 - {'error': {'message': \"Tool call validation failed: "
        "attempted to call tool 'json' which was not in request.tools\", "
        "'type': 'invalid_request_error', 'code': 'tool_use_failed'}}"
    )
    success_response = _FakeResponse(
        _FakeMessage(content='{"confidence": 0.9, "should_act": true, "reason": "ok"}')
    )

    call_count = {"n": 0}

    def fake_create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call: tools were passed, and the model triggers the bug.
            assert "tools" in kwargs
            raise phantom_error
        # Second call (the retry): tools must have been stripped out.
        assert "tools" not in kwargs
        assert "tool_choice" not in kwargs
        return success_response

    fake_client = type("FakeClient", (), {})()
    fake_client.chat = type("FakeChat", (), {})()
    fake_client.chat.completions = type("FakeCompletions", (), {})()
    fake_client.chat.completions.create = fake_create

    monkeypatch.setattr("app.core.llm_gateway._get_client", lambda: fake_client)

    tools = [{"name": "some_tool", "description": "...", "parameters": {}}]
    result = call_llm(messages=[{"role": "user", "content": "test"}], tools=tools)

    assert call_count["n"] == 2
    assert result["content"] == '{"confidence": 0.9, "should_act": true, "reason": "ok"}'


def test_call_llm_reraises_unrelated_bad_request_errors(monkeypatch):
    """A genuine 400 (e.g. a malformed request unrelated to the
    phantom-tool bug) must NOT be silently swallowed."""
    unrelated_error = _make_bad_request_error(
        "Error code: 400 - {'error': {'message': 'model does not exist', "
        "'type': 'invalid_request_error', 'code': 'model_not_found'}}"
    )

    fake_client = type("FakeClient", (), {})()
    fake_client.chat = type("FakeChat", (), {})()
    fake_client.chat.completions = type("FakeCompletions", (), {})()

    def fake_create(**kwargs):
        raise unrelated_error

    fake_client.chat.completions.create = fake_create
    monkeypatch.setattr("app.core.llm_gateway._get_client", lambda: fake_client)

    with pytest.raises(BadRequestError, match="model_not_found"):
        call_llm(messages=[{"role": "user", "content": "test"}])


def test_call_llm_without_tools_never_triggers_phantom_bug_check(monkeypatch):
    """When no tools are passed, any BadRequestError should re-raise
    immediately — the phantom-tool-bug retry only makes sense when
    tools were actually offered in the first place."""
    error = _make_bad_request_error("Error code: 400 - some other issue")

    fake_client = type("FakeClient", (), {})()
    fake_client.chat = type("FakeChat", (), {})()
    fake_client.chat.completions = type("FakeCompletions", (), {})()
    fake_client.chat.completions.create = lambda **kwargs: (_ for _ in ()).throw(error)

    monkeypatch.setattr("app.core.llm_gateway._get_client", lambda: fake_client)

    with pytest.raises(BadRequestError):
        call_llm(messages=[{"role": "user", "content": "test"}], tools=None)


def test_format_tools_for_groq_shape():
    tools = [{"name": "t1", "description": "desc", "parameters": {"type": "object"}}]
    formatted = _format_tools_for_groq(tools)
    assert formatted == [
        {
            "type": "function",
            "function": {"name": "t1", "description": "desc", "parameters": {"type": "object"}},
        }
    ]