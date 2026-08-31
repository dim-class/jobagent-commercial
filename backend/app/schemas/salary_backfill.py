"""Schemas for the explicit, bounded missing-salary maintenance workflow."""
from datetime import datetime

from pydantic import BaseModel, Field


class SalaryBackfillPlanItem(BaseModel):
    job_id: int
    company: str
    title: str
    source_url: str


class SalaryBackfillPlanOut(BaseModel):
    total_jobs: int
    salary_present: int
    salary_missing: int
    eligible_jobs: int
    ineligible_jobs: int
    fingerprint: str
    items: list[SalaryBackfillPlanItem]


class SalaryBackfillCreateRequest(BaseModel):
    confirmed: bool = Field(strict=True)
    fingerprint: str = Field(min_length=64, max_length=64)
    job_ids: list[int] = Field(min_length=1, max_length=100)


class SalaryBackfillItemOut(SalaryBackfillPlanItem):
    position: int
    state: str
    reason: str | None = None


class SalaryBackfillRunOut(BaseModel):
    id: int
    state: str
    total_jobs: int
    processed_jobs: int
    updated_jobs: int
    unavailable_jobs: int
    failed_jobs: int
    session_processed: int
    session_cap: int
    current_job_id: int | None
    paused_reason: str | None
    last_action: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    items: list[SalaryBackfillItemOut]


class SalaryBackfillItemResultRequest(BaseModel):
    job_id: int = Field(gt=0)
    outcome: str = Field(pattern="^(updated|unavailable|failed)$")
    reason: str | None = Field(default=None, max_length=128)


class SalaryBackfillPauseRequest(BaseModel):
    reason: str = Field(pattern="^(login_required|verification|user_pause|worker_error)$")


class SalaryBackfillAuthorizeRemainingRequest(BaseModel):
    confirmed: bool = Field(strict=True)
