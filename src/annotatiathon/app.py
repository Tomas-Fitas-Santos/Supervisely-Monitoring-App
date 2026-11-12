"""FastAPI application and versioned endpoints. No credentials or demo data embedded."""
from fastapi import FastAPI, Response

from .activities_api import router as activities_router
from .attendance_api import router as attendance_router
from .auth import router as auth_router
from .closeout_api import router as closeout_router
from .coverage_api import router as coverage_router
from .database import database_is_ready
from .datasets_api import router as datasets_router
from .mounted_datasets import router as mounted_datasets_router
from .events_api import router as events_router
from .full_reset_api import router as full_reset_router
from .labeling_api import router as labeling_router
from .manual_jobs_api import router as manual_jobs_router
from .monitoring_api import router as monitoring_router
from .native_export_api import router as native_export_router
from .native_operations_api import router as native_operations_router
from .native_sync_api import router as native_router
from .organiser_setup_api import router as organiser_setup_router
from .people_api import router as people_router
from .personal_settings_api import router as personal_settings_router
from .registration_api import router as registration_router
from .review_dashboard_api import router as review_dashboard_router
from .review_queue_api import router as review_queue_router
from .reviews_api import router as reviews_router
from .scoring_api import router as scoring_router
from .settings_api import router as settings_router
from .staff_api import router as staff_router
from .teams_api import router as teams_router
from .tickets_api import router as tickets_router

app = FastAPI(
    title="Nightjar Annotatiathon Operations",
    version="0.11.0",
    description="Local-first staff API; competition operations will follow verified contracts.",
)
app.include_router(auth_router)
app.include_router(registration_router)
app.include_router(organiser_setup_router)
app.include_router(people_router)
app.include_router(coverage_router)
app.include_router(teams_router)
app.include_router(settings_router)
app.include_router(personal_settings_router)
app.include_router(attendance_router)
app.include_router(staff_router)
app.include_router(tickets_router)
app.include_router(events_router)
app.include_router(full_reset_router)
app.include_router(activities_router)
app.include_router(native_router)
app.include_router(native_operations_router)
app.include_router(native_export_router)
app.include_router(monitoring_router)
app.include_router(datasets_router)
app.include_router(mounted_datasets_router)
app.include_router(labeling_router)
app.include_router(manual_jobs_router)
app.include_router(reviews_router)
app.include_router(review_dashboard_router)
app.include_router(review_queue_router)
app.include_router(scoring_router)
app.include_router(closeout_router)


@app.get("/health/live", tags=["health"])
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness(response: Response) -> dict[str, str]:
    if not database_is_ready():
        response.status_code = 503
        return {"status": "unavailable"}
    return {"status": "ok"}
