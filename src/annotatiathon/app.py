"""HTTP application entry point. No sample users, data or fake external operations."""
from fastapi import FastAPI, Response

from .database import database_is_ready

app = FastAPI(
    title="Annotatiathon Platform",
    version="0.1.0",
    description="Local-first foundation; event APIs are added behind verified acceptance tests.",
)


@app.get("/health/live", tags=["health"])
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness(response: Response) -> dict[str, str]:
    if not database_is_ready():
        response.status_code = 503
        return {"status": "unavailable"}
    return {"status": "ok"}
