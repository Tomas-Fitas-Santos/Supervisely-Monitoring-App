"""FastAPI application and versioned endpoints. No credentials or demo data embedded."""
from fastapi import FastAPI, Response

from .auth import router as auth_router
from .database import database_is_ready
from .people_api import router as people_router
from .settings_api import router as settings_router

app = FastAPI(
    title="Nightjar Annotatiathon Operations",
    version="0.2.0",
    description="Local-first staff API; competition operations will follow verified contracts.",
)
app.include_router(auth_router)
app.include_router(people_router)
app.include_router(settings_router)


@app.get("/health/live", tags=["health"])
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness(response: Response) -> dict[str, str]:
    if not database_is_ready():
        response.status_code = 503
        return {"status": "unavailable"}
    return {"status": "ok"}
