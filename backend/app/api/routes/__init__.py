"""API route modules."""

from fastapi import APIRouter

from app.api.routes import (
    analytics,
    application,
    browser,
    console,
    dashboard,
    decision,
    extension,
    health,
    interviews,
    jobs,
    offers,
    quick_capture,
    recruiter,
    resumes,
    settings,
    supervised_sessions,
    tasks,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(resumes.router)
api_router.include_router(jobs.router)
# Registered after jobs.router so /api/jobs/{id}/... workflow paths
# sit alongside the existing job routes.
api_router.include_router(application.job_router)
api_router.include_router(application.queue_router)
api_router.include_router(interviews.router)
api_router.include_router(offers.router)
# After offers: v1.0 decision support reads the same offers and adds nothing
# to their lifecycle.
api_router.include_router(decision.router)
api_router.include_router(quick_capture.router)
# The Chrome extension POC. Loopback-only; reuses job_intake for
# every write, so a detected job is indistinguishable downstream.
api_router.include_router(extension.router)
# M4a session scaffolding - no navigation. Loopback-only, same as extension.router.
api_router.include_router(supervised_sessions.router)
api_router.include_router(recruiter.router)
api_router.include_router(browser.router)
api_router.include_router(analytics.router)
api_router.include_router(dashboard.router)
api_router.include_router(settings.router)
api_router.include_router(tasks.router)
# M2 attention subview: reads the routers above, adds no new persistence.
api_router.include_router(console.router)

__all__ = ["api_router"]
