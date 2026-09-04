"""
Tests for app.verticals.contract_tracking.chunking. call_llm is
monkeypatched at the point it's imported into this module (matching
the convention in test_orchestration.py) — no real network calls.
"""

import json

import pytest

from app.verticals.contract_tracking.chunking import split_contract_into_clauses


def _fake_llm(content: str):
    return lambda **kwargs: {"content": content, "tool_calls": []}


def test_split_contract_into_clauses_happy_path(monkeypatch):
    fake_response = json.dumps(
        [
            {"clause_number": "1", "title": "Term and Termination", "text": "This agreement..."},
            {"clause_number": "2", "title": "Confidentiality", "text": "Each party shall..."},
        ]
    )
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm", _fake_llm(fake_response)
    )

    clauses = split_contract_into_clauses("some raw contract text")

    assert len(clauses) == 2
    assert clauses[0] == {
        "clause_number": "1",
        "title": "Term and Termination",
        "text": "This agreement...",
    }
    assert clauses[1]["clause_number"] == "2"


def test_split_contract_into_clauses_strips_code_fences(monkeypatch):
    fenced = "```json\n" + json.dumps([{"clause_number": "1", "title": "T", "text": "X"}]) + "\n```"
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm", _fake_llm(fenced)
    )

    clauses = split_contract_into_clauses("text")

    assert clauses == [{"clause_number": "1", "title": "T", "text": "X"}]


def test_split_contract_into_clauses_defaults_missing_clause_number(monkeypatch):
    """If the LLM omits clause_number, we assign one based on position
    (1-indexed) rather than failing the whole contract."""
    fake_response = json.dumps([{"title": "Untitled", "text": "some clause text"}])
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm", _fake_llm(fake_response)
    )

    clauses = split_contract_into_clauses("text")

    assert clauses[0]["clause_number"] == "1"


def test_split_contract_into_clauses_raises_on_invalid_json(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _fake_llm("not json at all"),
    )

    with pytest.raises(ValueError, match="invalid JSON"):
        split_contract_into_clauses("text")


def test_split_contract_into_clauses_raises_on_empty_array(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm", _fake_llm("[]")
    )

    with pytest.raises(ValueError, match="non-empty"):
        split_contract_into_clauses("text")


def test_split_contract_into_clauses_raises_on_missing_text_field(monkeypatch):
    fake_response = json.dumps([{"clause_number": "1", "title": "Oops"}])
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm", _fake_llm(fake_response)
    )

    with pytest.raises(ValueError, match="missing required 'text' field"):
        split_contract_into_clauses("text")


def test_split_contract_into_clauses_raises_on_non_list_json(monkeypatch):
    monkeypatch.setattr(
        "app.verticals.contract_tracking.chunking.call_llm",
        _fake_llm(json.dumps({"clause_number": "1", "text": "not a list"})),
    )

    with pytest.raises(ValueError, match="non-empty"):
        split_contract_into_clauses("text")
