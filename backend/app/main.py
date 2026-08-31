from fastapi import FastAPI
from app.api import agent_runs, escalations, notifications, admin, evaluation, submissions
from fastapi.middleware.cors import CORSMiddleware
import app.verticals.dummy.tools
import app.verticals.dummy.graph  

app = FastAPI(title="Agentic RAG Platform - Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],    allow_credentials=True,
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