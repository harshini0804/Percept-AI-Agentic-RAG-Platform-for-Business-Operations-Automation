"""
LLM Gateway (Section 2, 3.1) — a swappable wrapper around the LLM
provider, so no other code in the project talks to Groq/Claude
directly. Switching providers later means changing only this file.

Two-key support: if GROQ_API_KEY_2 is set, contract_tracking's
clause extraction alternates between key 0 (chunking + odd clauses)
and key 1 (even clauses), halving each key's per-minute token
consumption. All other callers use key_index=0 (the default) and
are completely unaffected — backward compatible.

Retry/backoff: when a key hits a 429 rate limit, the gateway waits
the duration Groq suggests in the error response (parsed from the
error message), then retries on the same key. If that retry also
hits a 429, it falls back to the other key before giving up. This
means transient rate limits are handled transparently without ever
surfacing a raw API error to the escalation UI.
"""

import os
import re
import time
import json
from groq import Groq, BadRequestError, RateLimitError

# Key 0: primary key (required)
# Key 1: secondary key (optional — if not set, key_index=1 falls back to key 0)
_clients: dict[int, Groq | None] = {0: None, 1: None}

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# Retry/backoff configuration — env-configurable per Section 12.2
MAX_RETRIES = int(os.getenv("GROQ_MAX_RETRIES", "2"))
DEFAULT_BACKOFF_SECONDS = float(os.getenv("GROQ_DEFAULT_BACKOFF_SECONDS", "2.0"))


def _get_client(key_index: int = 0) -> Groq:
    """
    Returns a cached Groq client for the given key index (0 or 1).
    If key 1 is requested but GROQ_API_KEY_2 is not set, falls back
    to key 0 — so contract_tracking's alternating-key logic degrades
    gracefully to single-key mode in dev/test environments where only
    one key is configured.
    """
    global _clients

    if key_index == 1:
        api_key_2 = os.getenv("GROQ_API_KEY_2")
        if not api_key_2:
            # No second key configured — fall back to key 0 silently.
            key_index = 0
        elif _clients[1] is None:
            _clients[1] = Groq(api_key=api_key_2)

    if key_index == 0 and _clients[0] is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Check backend/.env and docker-compose.yml."
            )
        _clients[0] = Groq(api_key=api_key)

    return _clients[key_index]


def _parse_retry_after(error_message: str) -> float:
    """
    Groq's 429 responses include a suggested wait duration in the
    error message, e.g. "Please try again in 577.5ms". Parse and
    return that duration in seconds, or DEFAULT_BACKOFF_SECONDS if
    we can't find it.
    """
    # Match patterns like "577.5ms", "1.2s", "2s"
    ms_match = re.search(r"try again in ([\d.]+)ms", error_message, re.IGNORECASE)
    if ms_match:
        return float(ms_match.group(1)) / 1000.0

    s_match = re.search(r"try again in ([\d.]+)s", error_message, re.IGNORECASE)
    if s_match:
        return float(s_match.group(1))

    return DEFAULT_BACKOFF_SECONDS


def _call_with_retry(kwargs: dict, key_index: int) -> object:
    """
    Calls Groq's chat completions API with retry/backoff on 429.

    Strategy:
    1. Try on key_index.
    2. On 429: parse suggested wait from error, sleep, retry same key.
    3. If still 429 after MAX_RETRIES on same key: try the other key once.
    4. If that also 429s: re-raise so the caller's error handling takes over.

    All other errors (400 BadRequestError, etc.) are raised immediately
    without retry — they're not transient and retrying won't help.
    """
    other_key = 1 - key_index  # 0→1 or 1→0

    for attempt in range(MAX_RETRIES + 1):
        current_key = key_index if attempt < MAX_RETRIES else other_key
        client = _get_client(current_key)

        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            if attempt < MAX_RETRIES:
                wait = _parse_retry_after(str(e))
                time.sleep(wait)
                continue
            # Last attempt also hit rate limit — try the other key once
            if attempt == MAX_RETRIES - 1:
                continue
            raise
        except BadRequestError:
            raise  # never retry bad requests


def call_llm(
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    key_index: int = 0,
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

    `key_index` selects which API key to use (0 = primary, 1 =
    secondary). Defaults to 0 — all existing callers that don't pass
    this argument are completely unaffected. contract_tracking's
    clause extraction passes alternating key_index values to spread
    token consumption across both keys.

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
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    if tools:
        kwargs["tools"] = _format_tools_for_groq(tools)
        kwargs["tool_choice"] = "auto"

    try:
        response = _call_with_retry(kwargs, key_index)
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
            response = _call_with_retry(kwargs, key_index)
        else:
            raise

    message = response.choices[0].message

    tool_calls = []
    if message.tool_calls:
        for tc in message.tool_calls:
            tool_calls.append({
                "id": getattr(tc, "id", None),
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
