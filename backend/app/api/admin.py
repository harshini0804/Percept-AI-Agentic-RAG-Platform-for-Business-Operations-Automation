"""
Admin API (Category A) — powers the Admin panel shared UI screen
(Section 5): "a 'Resync KB' button per vertical, plus sync history
(files processed, last run time)."
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.db import get_connection
from app.core.ingestion import ingest_staging_folder

router = APIRouter(prefix="/admin", tags=["admin"])

# Matches Section 4.3's vertical values and each vertical's primary
# source_type from Section 8 (the type stored during scheduled/staging
# ingestion, not the runtime-persistence source_types like action_item).
VERTICAL_SOURCE_TYPES = {
    "post_incident": "postmortem",
    "internal_mobility": "employee_profile",
    "contract_tracking": "contract_clause",
    "meeting_action_items": "action_item",
}


class ResyncResponse(BaseModel):
    vertical: str
    processed: list[str]
    skipped: list[str]
    errors: list[str]


class SyncHistoryEntry(BaseModel):
    vertical: str
    file_path: str
    content_hash: str
    last_synced_at: str


@router.post("/resync/{vertical}", response_model=ResyncResponse)
def resync_vertical(vertical: str):
    """Manually triggers ingestion for one vertical's staging folder."""
    if vertical not in VERTICAL_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown vertical '{vertical}'. Must be one of: {list(VERTICAL_SOURCE_TYPES)}",
        )

    source_type = VERTICAL_SOURCE_TYPES[vertical]
    summary = ingest_staging_folder(vertical=vertical, source_type=source_type)

    return ResyncResponse(vertical=vertical, **summary)


@router.get("/sync-history", response_model=list[SyncHistoryEntry])
def get_sync_history(vertical: str | None = None, limit: int = 50):
    """Recent kb_sync_state entries — files processed and when."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if vertical:
                cur.execute(
                    """
                    SELECT vertical, file_path, content_hash, last_synced_at
                    FROM kb_sync_state
                    WHERE vertical = %s
                    ORDER BY last_synced_at DESC
                    LIMIT %s;
                    """,
                    (vertical, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT vertical, file_path, content_hash, last_synced_at
                    FROM kb_sync_state
                    ORDER BY last_synced_at DESC
                    LIMIT %s;
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
            return [
                SyncHistoryEntry(
                    vertical=r["vertical"],
                    file_path=r["file_path"],
                    content_hash=r["content_hash"],
                    last_synced_at=r["last_synced_at"].isoformat(),
                )
                for r in rows
            ]
    finally:
        conn.close()