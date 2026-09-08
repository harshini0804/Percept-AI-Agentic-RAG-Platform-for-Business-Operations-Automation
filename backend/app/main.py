import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler

from app.api import agent_runs, escalations, notifications, admin, evaluation, submissions
from app.api.admin import VERTICAL_SOURCE_TYPES
from app.core.ingestion import ingest_staging_folder
from app.verticals.meeting_action_items.followup import run_followup_check

import app.verticals.dummy.tools
import app.verticals.dummy.graph
import app.verticals.meeting_action_items.tools
import app.verticals.meeting_action_items.graph

import app.verticals.contract_tracking.tools
import app.verticals.contract_tracking.graph

# Section 6.3: "A shared function, called on a timer via APScheduler,
# scans each vertical's staging folder..." Interval is configurable
# since this is a dev/demo project, not production — default kept
# short (5 min) so the mechanism is easy to observe while testing.
SCHEDULED_INGESTION_INTERVAL_MINUTES = int(
    os.getenv("SCHEDULED_INGESTION_INTERVAL_MINUTES", "5")
)

# Section 8.4's Trigger 2: "a daily scheduled job re-checks every
# open, overdue action item." Deliberately NOT a literal 24-hour
# cadence here — same reasoning as the ingestion interval above: a
# short, configurable default makes this observable during dev/demo
# without waiting a full day to see it fire.
SCHEDULED_FOLLOWUP_INTERVAL_MINUTES = int(
    os.getenv("SCHEDULED_FOLLOWUP_INTERVAL_MINUTES", "10")
)

scheduler = BackgroundScheduler()


def run_scheduled_ingestion() -> None:
    """
    Scans every real vertical's staging folder and ingests anything
    new or changed — the automatic counterpart to the manual
    POST /admin/resync/{vertical} button, reusing the exact same
    ingest_staging_folder() function and the same vertical ->
    source_type mapping admin.py already defines.

    Only "dummy" is excluded here (not a real vertical with real
    scheduled ingestion needs, per its own docstrings elsewhere) —
    every real vertical listed in VERTICAL_SOURCE_TYPES is scanned.

    Failures for one vertical are caught and logged, not allowed to
    stop the other verticals' ingestion in the same run.
    """
    for vertical, source_type in VERTICAL_SOURCE_TYPES.items():
        try:
            summary = ingest_staging_folder(vertical=vertical, source_type=source_type)
            if summary["processed"] or summary["errors"]:
                print(f"[scheduled ingestion] {vertical}: {summary}")
        except Exception as e:
            print(f"[scheduled ingestion] ERROR for vertical '{vertical}': {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(
        run_scheduled_ingestion,
        "interval",
        minutes=SCHEDULED_INGESTION_INTERVAL_MINUTES,
        id="scheduled_ingestion",
    )
    scheduler.add_job(
        run_followup_check,
        "interval",
        minutes=SCHEDULED_FOLLOWUP_INTERVAL_MINUTES,
        id="scheduled_meeting_action_items_followup",
    )
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Agentic RAG Platform - Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_runs.router)
app.include_router(escalations.router)
app.include_router(notifications.router)
app.include_router(admin.router)
app.include_router(evaluation.router)
app.include_router(submissions.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}