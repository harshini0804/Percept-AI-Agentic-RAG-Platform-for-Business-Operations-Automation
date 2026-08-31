from fastapi import FastAPI
from app.api import agent_runs, escalations

import app.verticals.dummy.tools

app = FastAPI(title="Agentic RAG Platform - Backend")

app.include_router(agent_runs.router)
app.include_router(escalations.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}