from fastapi import FastAPI
from app.api import agent_runs, escalations, notifications, admin, evaluation, submissions

import app.verticals.dummy.tools
import app.verticals.dummy.graph  

app = FastAPI(title="Agentic RAG Platform - Backend")

app.include_router(agent_runs.router)
app.include_router(escalations.router)
app.include_router(notifications.router)
app.include_router(admin.router)
app.include_router(evaluation.router)
app.include_router(submissions.router)



@app.get("/health")
def health_check():
    return {"status": "ok"}