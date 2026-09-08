"""
Section 6.4's one deliberate exception: for contract_tracking,
scheduled ingestion and the analysis trigger are the SAME event — a
newly arriving contract is simultaneously new reference material and
something that needs extraction immediately (Section 8.3, Notes).

This deliberately does NOT call app.core.ingestion.ingest_staging_folder()
for contract_tracking, even though every other vertical's scheduled
ingestion goes through it. Reason: ingest_staging_folder() embeds
using the generic default_paragraph_chunker and generic
{"file_path": ...} metadata — but Vertical 3's KB requires clause-
level chunks tagged with {clause_number, title} (Section 4.3), which
only run_contract_tracking_vertical()'s own pipeline produces
(PR #7). Running both would double-embed each contract, once
correctly (clause-level, via run_contract_tracking_vertical) and once
incorrectly (paragraph-level, via ingest_staging_folder) — bypassing
the generic path entirely avoids that.

Hash-based change detection is reused as-is from app.core.ingestion
(_compute_hash / _get_last_synced_hash / _update_sync_state) rather
than reimplemented, so a contract already ingested and unchanged is
still correctly skipped on every scheduler tick, exactly like every
other vertical.
"""

from pathlib import Path

import app.core.ingestion as ingestion
from app.core.ingestion import extract_text, _compute_hash, _get_last_synced_hash, _update_sync_state
from app.schemas.agent_contract import AgentRunInput, TriggerType
from app.verticals.contract_tracking.graph import run_contract_tracking_vertical

VERTICAL_NAME = "contract_tracking"


def run_scheduled_contract_ingestion() -> dict:
    """
    Scans /uploads/staging/contract_tracking/ for new or changed
    files. For each one: extracts its text, runs the FULL
    contract_tracking vertical (clause chunking, extraction,
    tool calls, per-obligation gating, and KB persistence — all in
    one call, since that pipeline already does its own embedding),
    and records the file's hash so it's skipped on future ticks.

    Returns a summary dict shaped like ingest_staging_folder's, for
    consistency with the log line main.py's run_scheduled_ingestion
    already prints for every other vertical.
    """
    folder = ingestion.STAGING_ROOT / VERTICAL_NAME
    summary = {"processed": [], "skipped": [], "errors": []}

    if not folder.exists():
        summary["errors"].append(f"Staging folder does not exist: {folder}")
        return summary

    for file_path in sorted(folder.iterdir()):
        if not file_path.is_file() or file_path.name.startswith("."):
            continue

        relative_path = str(file_path.relative_to(ingestion.STAGING_ROOT))

        try:
            current_hash = _compute_hash(file_path)
            last_hash = _get_last_synced_hash(VERTICAL_NAME, relative_path)

            if current_hash == last_hash:
                summary["skipped"].append(relative_path)
                continue

            contract_text = extract_text(file_path)

            agent_input = AgentRunInput(
                vertical=VERTICAL_NAME,
                trigger_type=TriggerType.SCHEDULED_INGESTION,
                input_payload={"text": contract_text},
            )
            run_contract_tracking_vertical(agent_input)

            _update_sync_state(VERTICAL_NAME, relative_path, current_hash)
            summary["processed"].append(relative_path)

        except Exception as e:
            summary["errors"].append(f"{relative_path}: {str(e)}")

    return summary
