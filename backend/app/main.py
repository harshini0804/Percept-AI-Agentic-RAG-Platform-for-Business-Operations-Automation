from fastapi import FastAPI
from app.api import agent_runs

app = FastAPI(title="Agentic RAG Platform - Backend")

app.include_router(agent_runs.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}