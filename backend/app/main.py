from fastapi import FastAPI

app = FastAPI(title="Agentic RAG Platform - Backend")

@app.get("/health")
def health_check():
    return {"status": "ok"}