"""
LLM Gateway (Section 2, 3.1) — a swappable wrapper around the LLM
provider, so no other code in the project talks to Groq/Claude
directly. Switching providers later means changing only this file.
"""

import os
import json
from groq import Groq, BadRequestError

_client = None

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Check backend/.env and docker-compose.yml."
            )
        _client = Groq(api_key=api_key)
    return _client


def call_llm(
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
) -> dict:
    """
    Sends a chat completion request to Groq. Returns a normalized
    dict regardless of whether the model responded with plain text
    or a tool call:

        {
            "content": str | None,       # text response, if any
            "tool_calls": [              # list of tool calls, if any
                {"name": ..., "arguments": {...}}
            ],
        }

    `tools` should be the list returned by
    tool_registry.get_tools_for_vertical(), reformatted for Groq's
    OpenAI-compatible tool schema (see _format_tools_for_groq below).

    Known gpt-oss quirk (Groq-specific): the model sometimes emits
    an internal "Harmony format" reasoning/commentary channel that
    Groq misparses as an attempted call to a tool that was never
    offered (observed phantom names: "commentary", "json" — the
    name varies, so we don't match on it). This raises a 400 error
    with code "tool_use_failed" and the phrase "was not in
    request.tools". If that specific error occurs, we retry once
    with tools stripped out, letting the model just respond in
    plain text instead of attempting a tool call.
    """
    client = _get_client()

    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    if tools:
        kwargs["tools"] = _format_tools_for_groq(tools)
        kwargs["tool_choice"] = "auto"

    try:
        response = client.chat.completions.create(**kwargs)
    except BadRequestError as e:
        error_str = str(e).lower()
        is_phantom_tool_bug = (
            tools is not None
            and "tool_use_failed" in error_str
            and "was not in request.tools" in error_str
        )
        if is_phantom_tool_bug:
            kwargs.pop("tools", None)
            kwargs.pop("tool_choice", None)
            response = client.chat.completions.create(**kwargs)
        else:
            raise

    message = response.choices[0].message

    tool_calls = []
    if message.tool_calls:
        for tc in message.tool_calls:
            tool_calls.append({
                "name": tc.function.name,
                "arguments": json.loads(tc.function.arguments),
            })

    return {
        "content": message.content,
        "tool_calls": tool_calls,
    }


def _format_tools_for_groq(tools: list[dict]) -> list[dict]:
    """
    Converts tool_registry's schema format into Groq's
    OpenAI-compatible tool schema:
        {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
    """
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in tools
    ]