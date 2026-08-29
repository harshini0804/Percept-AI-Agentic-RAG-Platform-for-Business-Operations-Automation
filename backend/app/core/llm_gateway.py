"""
LLM Gateway (Section 2, 3.1) — a swappable wrapper around the LLM
provider, so no other code in the project talks to Groq/Claude
directly. Switching providers later means changing only this file.
"""

import os
import json
from groq import Groq

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

    response = client.chat.completions.create(**kwargs)
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