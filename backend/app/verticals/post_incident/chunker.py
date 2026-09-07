"""
Section-aware postmortem chunker (Section 8.1) — splits incident
postmortem documents on their known structural headers so each chunk
retains full semantic context for its section.

Postmortems in the staging folder follow a consistent format:
    # Incident: [Title]
    Date: ...
    Service: ...
    Severity: ...
    ## Summary
    ## Timeline
    ## Root Cause
    ## Impact
    ## Mitigation
    ## Lessons Learned / Action Items

Each section is kept as ONE chunk (not split further) because sections
like "Root Cause" and "Mitigation" are semantically inseparable — a
paragraph from the middle of a root-cause narrative loses meaning
without the surrounding context.

The document header (title + metadata lines before the first ##) is
prepended to EVERY chunk, so retrieval hits always carry the incident's
identifying context (service name, date, severity) even if only one
section matched.
"""

import re
from typing import Optional


# Matches markdown section headers: ## Header or ### Header
_SECTION_RE = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)


def _extract_header(text: str) -> tuple[str, str]:
    """
    Splits the document into (header, body).

    Header = everything before the first ## section.  Contains the
    incident title (# Incident: ...) plus metadata lines (Date,
    Service, Severity).

    Body = everything from the first ## section onward.
    """
    first_section = _SECTION_RE.search(text)
    if first_section is None:
        return text.strip(), ""
    split_pos = first_section.start()
    return text[:split_pos].strip(), text[split_pos:].strip()


def section_chunker(text: str) -> list[str]:
    """
    Section-aware chunker for postmortem documents.

    1. Extracts the header (title + metadata) from the document.
    2. Splits the body on ## / ### headers into discrete sections.
    3. Prepends the header to each section chunk.
    4. Falls back to paragraph splitting if no section headers are
       detected (handles unstructured / free-form postmortems).
    """
    if not text or not text.strip():
        return []

    header, body = _extract_header(text)

    if not body:
        # No ## headers found — fall back to paragraph splitting
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        # Prepend header to each paragraph if it's not already the header
        if len(paragraphs) <= 1:
            return paragraphs if paragraphs else [text.strip()]
        return [f"{header}\n\n{p}" if p != header else p for p in paragraphs]

    # Split body on section headers, keeping the header line with its body
    section_splits = _SECTION_RE.split(body)

    # _SECTION_RE.split produces: [pre-match, level, title, body, level, title, body, ...]
    # First element is any text before the first ##, usually empty.
    chunks: list[str] = []
    i = 1  # skip element 0 (pre-first-section text, usually empty)
    while i < len(section_splits):
        level = section_splits[i]           # e.g. "##"
        title = section_splits[i + 1]       # e.g. "Root Cause"
        section_body = section_splits[i + 2].strip() if (i + 2) < len(section_splits) else ""
        section_text = f"{level} {title}\n{section_body}".strip()

        # Prepend header so every chunk carries incident context
        chunk = f"{header}\n\n{section_text}"
        chunks.append(chunk)
        i += 3

    return chunks if chunks else [text.strip()]
