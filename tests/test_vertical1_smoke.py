"""
Smoke test for Vertical 1: Post-Incident Knowledge Synthesis.

Tests that can run WITHOUT Docker (no DB, no Groq, no LangGraph runtime):
1. Chunker logic (section splitting, header prepend, fallback)
2. Module structure (files exist, imports resolve within the package)
3. Tool registrations (decorators fire correctly)
4. Graph assembly (StateGraph compiles without runtime deps)
"""

import sys
import os

os.environ["USE_TF"] = "0"
os.environ["TRANSFORMERS_NO_TF"] = "1"
os.environ["USE_TORCH"] = "1"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# ---------------------------------------------------------------
# Test 1: Chunker logic
# ---------------------------------------------------------------

from app.verticals.post_incident.chunker import section_chunker, _extract_header


def test_section_chunker_structured():
    """Structured postmortem with ## headers splits into sections."""
    doc = """# Incident: Test Failure
Date: 2024-01-01
Service: test-service
Severity: P2

## Summary
This is the summary section.

## Root Cause
This is the root cause.

## Impact
Some impact details.
"""
    chunks = section_chunker(doc)
    assert len(chunks) == 3, f"Expected 3 chunks, got {len(chunks)}"

    # Every chunk should contain the header
    for i, chunk in enumerate(chunks):
        assert "# Incident: Test Failure" in chunk, f"Chunk {i} missing header"
        assert "Service: test-service" in chunk, f"Chunk {i} missing service metadata"

    # Check correct section assignment
    assert "## Summary" in chunks[0]
    assert "## Root Cause" in chunks[1]
    assert "## Impact" in chunks[2]
    print("  PASS: test_section_chunker_structured")


def test_section_chunker_unstructured():
    """Unstructured text (no ## headers) falls back to paragraph splitting."""
    doc = """Just a plain text incident report.

Second paragraph with more details.

Third paragraph with conclusions.
"""
    chunks = section_chunker(doc)
    assert len(chunks) == 3, f"Expected 3 chunks, got {len(chunks)}"
    print("  PASS: test_section_chunker_unstructured")


def test_section_chunker_single_block():
    """Single block of text with no headers or paragraphs."""
    doc = "Single line incident description with no structure."
    chunks = section_chunker(doc)
    assert len(chunks) == 1, f"Expected 1 chunk, got {len(chunks)}"
    assert chunks[0] == doc
    print("  PASS: test_section_chunker_single_block")


def test_extract_header():
    """Header extraction separates metadata from body."""
    doc = """# Incident: Pool Leak
Date: 2024-01-01
Service: payments

## Summary
Details here.
"""
    header, body = _extract_header(doc)
    assert "# Incident: Pool Leak" in header
    assert "Date: 2024-01-01" in header
    assert "## Summary" in body
    assert "## Summary" not in header
    print("  PASS: test_extract_header")


def test_real_postmortem_files():
    """All 5 sample postmortems in staging produce valid chunks."""
    staging_dir = os.path.join(
        os.path.dirname(__file__), "..", "uploads", "staging", "post_incident"
    )
    files = sorted(f for f in os.listdir(staging_dir) if f.endswith(".txt"))
    assert len(files) == 5, f"Expected 5 postmortem files, got {len(files)}: {files}"

    for fname in files:
        path = os.path.join(staging_dir, fname)
        text = open(path, encoding="utf-8").read()
        chunks = section_chunker(text)

        assert len(chunks) >= 3, f"{fname}: Expected >= 3 chunks, got {len(chunks)}"
        assert all(
            text.split("\n")[0] in c for c in chunks
        ), f"{fname}: Not all chunks have the header prepended"

    print(f"  PASS: test_real_postmortem_files ({len(files)} files)")


# ---------------------------------------------------------------
# Test 2: Module structure
# ---------------------------------------------------------------

def test_module_imports():
    """All post_incident modules import without errors."""
    # chunker (already imported above)
    from app.verticals.post_incident import chunker
    assert hasattr(chunker, "section_chunker")

    # tools (this triggers @tool decorators)
    from app.verticals.post_incident import tools
    assert hasattr(tools, "lookup_incidents_by_service")
    assert hasattr(tools, "get_incident_details")
    assert hasattr(tools, "create_incident_ticket")

    # graph
    from app.verticals.post_incident import graph
    assert hasattr(graph, "run_post_incident")
    assert hasattr(graph, "build_post_incident_graph")
    assert hasattr(graph, "finalize_node")
    assert hasattr(graph, "POST_INCIDENT_SYSTEM_PROMPT")

    print("  PASS: test_module_imports")


# ---------------------------------------------------------------
# Test 3: Tool registrations
# ---------------------------------------------------------------

def test_tools_registered():
    """All 3 post_incident tools are in the shared registry."""
    from app.core.tool_registry import get_tools_for_vertical

    tools = get_tools_for_vertical("post_incident")
    tool_names = {t["name"] for t in tools}

    expected = {"lookup_incidents_by_service", "get_incident_details", "create_incident_ticket"}
    assert tool_names == expected, f"Expected {expected}, got {tool_names}"

    # Verify tool types
    tool_map = {t["name"]: t for t in tools}
    assert tool_map["lookup_incidents_by_service"]["tool_type"] == "read"
    assert tool_map["get_incident_details"]["tool_type"] == "read"
    assert tool_map["create_incident_ticket"]["tool_type"] == "write"

    print("  PASS: test_tools_registered")


# ---------------------------------------------------------------
# Test 4: Vertical registration
# ---------------------------------------------------------------

def test_vertical_registered():
    """post_incident is registered in the vertical registry."""
    from app.core.vertical_registry import get_registered_verticals

    verticals = get_registered_verticals()
    assert "post_incident" in verticals, f"post_incident not in {verticals}"
    print("  PASS: test_vertical_registered")


# ---------------------------------------------------------------
# Test 5: Graph compiles
# ---------------------------------------------------------------

def test_graph_compiles():
    """StateGraph compiles without runtime dependencies."""
    from app.verticals.post_incident.graph import build_post_incident_graph

    compiled = build_post_incident_graph()
    assert compiled is not None
    print("  PASS: test_graph_compiles")


# ---------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------

if __name__ == "__main__":
    print("=== Vertical 1 Smoke Tests ===\n")

    print("[Chunker Logic]")
    test_section_chunker_structured()
    test_section_chunker_unstructured()
    test_section_chunker_single_block()
    test_extract_header()
    test_real_postmortem_files()

    print("\n[Module Structure]")
    test_module_imports()

    print("\n[Tool Registrations]")
    test_tools_registered()

    print("\n[Vertical Registration]")
    test_vertical_registered()

    print("\n[Graph Compilation]")
    test_graph_compiles()

    print("\n=== All tests passed PASSED ===")
