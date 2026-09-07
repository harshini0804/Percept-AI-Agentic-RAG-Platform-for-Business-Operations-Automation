"""
Tests for app.verticals.post_incident.chunker.

Verifies:
1. Section-aware splitting by markdown headers (##).
2. Header metadata extraction and preservation across all chunks.
3. Fallback paragraph splitting for unstructured text.
4. Single-block fallback handling.
"""

import pytest
from app.verticals.post_incident.chunker import section_chunker, _extract_header


def test_extract_header_structured():
    doc = """# Incident: Database Connection Pool Exhaustion
Service: payments-service
Date: 2024-03-15
Severity: P1

## Summary
The payments service suffered an outage due to connection starvation.
"""
    header, body = _extract_header(doc)
    assert "# Incident: Database Connection Pool Exhaustion" in header
    assert "Service: payments-service" in header
    assert "Date: 2024-03-15" in header
    assert "## Summary" in body
    assert "## Summary" not in header


def test_section_chunker_structured_prepends_header():
    doc = """# Incident: Redis Memory Leak
Service: cache-cluster
Date: 2024-07-10
Severity: P2

## Timeline
14:00 Alert triggered
14:15 Node restarted

## Root Cause
Eviction policy was disabled leading to OOM.

## Resolution
Enabled volatile-lru eviction.
"""
    chunks = section_chunker(doc)
    assert len(chunks) == 3

    # Verify that header is prepended to each section chunk
    for chunk in chunks:
        assert "# Incident: Redis Memory Leak" in chunk
        assert "Service: cache-cluster" in chunk

    assert "## Timeline" in chunks[0]
    assert "## Root Cause" in chunks[1]
    assert "## Resolution" in chunks[2]


def test_section_chunker_unstructured_falls_back_to_paragraphs():
    doc = """This is paragraph one of an unstructured postmortem writeup.

This is paragraph two detailing what the on-call engineer observed.

This is paragraph three explaining the hotfix applied.
"""
    chunks = section_chunker(doc)
    assert len(chunks) == 3
    assert "paragraph one" in chunks[0]
    assert "paragraph two" in chunks[1]
    assert "paragraph three" in chunks[2]


def test_section_chunker_single_block():
    doc = "Single line postmortem writeup without paragraphs or headers."
    chunks = section_chunker(doc)
    assert len(chunks) == 1
    assert chunks[0] == doc


def test_section_chunker_empty_input():
    chunks = section_chunker("")
    assert chunks == []
