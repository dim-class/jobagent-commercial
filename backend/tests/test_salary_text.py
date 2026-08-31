"""Unreadable salary strings must never be stored, shown, or trusted.

Offline only: fixture strings and the local database. Nothing here contacts a
recruitment site, runs OCR, or calls a model.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.job_sources.base import RawJobPosting
from app.job_sources.manual import manual_source
from app.models import Job
from app.services import extension_intake, job_intake, salary_backfill
from app.services.salary_text import is_valid_salary_text, sanitize_salary_text

SAMPLES = json.loads(
    (Path(__file__).parent / "fixtures/salary_text_samples.json").read_text(encoding="utf-8")
)
UNREADABLE = SAMPLES["unreadable"]
READABLE = SAMPLES["readable"]

#: One real observed value: "50-80K" drawn from the obfuscation font.
BOXED = UNREADABLE[0]


@pytest.mark.parametrize("value", UNREADABLE)
def test_boxed_and_placeholder_salaries_are_rejected(value: str) -> None:
    assert is_valid_salary_text(value) is False
    assert sanitize_salary_text(value) is None


@pytest.mark.parametrize("value", READABLE)
def test_real_salaries_are_still_accepted(value: str) -> None:
    assert is_valid_salary_text(value) is True
    assert sanitize_salary_text(value) == value


def test_empty_salary_is_not_valid() -> None:
    assert is_valid_salary_text(None) is False
    assert is_valid_salary_text("") is False


# --------------------------------------------------------------------------
# canonical intake
# --------------------------------------------------------------------------


def posting(number: int, *, salary: str | None) -> RawJobPosting:
    return RawJobPosting(
        title=f"岗位{number}",
        company=f"公司{number}",
        raw_description="负责 Kubernetes 集群运维与 Terraform 基础设施交付，" * 4,
        city="北京",
        salary_text=salary,
        source_url=f"https://www.zhipin.com/job_detail/job{number}.html",
        external_id=f"job{number}",
    )


def save(db, number: int, *, salary: str | None, enrich: bool = True):
    return job_intake.save_posting(
        db,
        posting(number, salary=salary),
        source=manual_source,
        source_name="boss",
        enrich_missing_salary=enrich,
    )


def test_intake_never_stores_a_boxed_salary(db) -> None:
    outcome = save(db, 1, salary=BOXED)
    assert outcome.created
    assert outcome.job.salary_text is None


def test_intake_replaces_a_stored_boxed_salary_with_a_real_one(db) -> None:
    job = save(db, 2, salary="20-30K").job
    job.salary_text = BOXED  # what the pre-fix capture had already written
    db.commit()

    outcome = save(db, 2, salary="16-19K")
    assert outcome.duplicate
    assert outcome.enriched_fields == ["salary_text"]
    assert outcome.job.salary_text == "16-19K"


def test_filling_a_missing_salary_invalidates_analyses_scored_without_it(db, active_resume) -> None:
    """`analysis_cache_key` is built from `content_hash` (company + title + JD),
    so a later salary never changes it - yet `scoring.py` reads the salary. A
    job analysed while its salary was unknown would otherwise serve that stale
    score from cache forever."""
    from app.models import JobAnalysis, Verdict

    job = save(db, 10, salary=None).job
    assert job.salary_text is None
    db.add(
        JobAnalysis(
            job_id=job.id, resume_id=active_resume.id, model="test-model-fast", prompt_version="v1",
            cache_key="stale-key-scored-without-a-salary", overall_score=55,
            verdict=Verdict.maybe, result_json={"overall_score": 55},
        )
    )
    db.commit()

    outcome = save(db, 10, salary="16-19K")
    assert outcome.duplicate
    assert outcome.enriched_fields == ["salary_text"]
    assert outcome.invalidated_analyses == 1
    assert db.query(JobAnalysis).filter(JobAnalysis.job_id == job.id).count() == 0
    assert any("请重新分析" in (event.notes or "") for event in outcome.job.events)


def test_an_unchanged_salary_never_drops_a_cached_analysis(db, active_resume) -> None:
    """The 115 jobs that already have a salary must not be re-billed."""
    from app.models import JobAnalysis, Verdict

    job = save(db, 11, salary="16-19K").job
    db.add(
        JobAnalysis(
            job_id=job.id, resume_id=active_resume.id, model="test-model-fast", prompt_version="v1",
            cache_key="key-scored-with-the-real-salary", overall_score=80,
            verdict=Verdict.apply, result_json={"overall_score": 80},
        )
    )
    db.commit()

    outcome = save(db, 11, salary="99-100K")
    assert outcome.enriched_fields == []
    assert outcome.invalidated_analyses == 0
    assert db.query(JobAnalysis).filter(JobAnalysis.job_id == job.id).count() == 1


def test_intake_never_overwrites_a_reliable_salary(db) -> None:
    save(db, 3, salary="16-19K")
    outcome = save(db, 3, salary="99-100K")
    assert outcome.duplicate
    assert outcome.enriched_fields == []
    assert outcome.job.salary_text == "16-19K"


def test_a_boxed_recapture_cannot_erase_a_reliable_salary(db) -> None:
    save(db, 4, salary="16-19K")
    outcome = save(db, 4, salary=BOXED)
    assert outcome.duplicate
    assert outcome.enriched_fields == []
    assert outcome.job.salary_text == "16-19K"


# --------------------------------------------------------------------------
# extension candidates
# --------------------------------------------------------------------------


class Candidate:
    def __init__(self, salary: str | None, warnings: list[str] | None = None) -> None:
        self.warnings = warnings or []
        self.title = "云平台工程师"
        self.company = "示例公司"
        self.description = "负责 Kubernetes 集群运维与 Terraform 基础设施交付，" * 4
        self.city = "北京"
        self.salary_text = salary
        self.experience_text = "3-5年"
        self.education_text = "本科"
        self.source_url = "https://www.zhipin.com/job_detail/ext1.html"
        self.external_id = "ext1"
        self.matched_selectors: dict[str, str] = {}


def test_extension_candidate_with_a_boxed_salary_reads_as_missing(db) -> None:
    verdict = extension_intake.inspect(db, Candidate(BOXED))
    assert verdict.status == "new"
    assert extension_intake.to_posting(Candidate(BOXED)).salary_text is None
    assert any("字体混淆" in warning for warning in verdict.warnings)


OCR_MISS = "本地薪资 OCR 未采用：unavailable"


def test_a_salary_ocr_miss_is_recorded_on_the_job_instead_of_being_dropped(db) -> None:
    """The extension computes why OCR did not fill the salary and sends it; the
    intake used to build its own warning list and throw that reason away, which
    is how 15 jobs arrived with no salary and no explanation anywhere."""
    outcome = extension_intake.import_one(db, Candidate(None, warnings=[OCR_MISS]))
    assert outcome.job.salary_text is None

    notes = [event.notes for event in outcome.job.events]
    assert any(OCR_MISS in (note or "") for note in notes)
    assert any("薪资待回填" in (note or "") for note in notes)


def test_a_candidate_with_a_readable_salary_records_no_ocr_excuse(db) -> None:
    outcome = extension_intake.import_one(db, Candidate("16-19K"))
    assert outcome.job.salary_text == "16-19K"
    notes = [event.notes or "" for event in outcome.job.events]
    assert not any("OCR 未采用" in note for note in notes)


def test_the_ocr_reason_also_surfaces_in_the_read_only_preview(db) -> None:
    verdict = extension_intake.inspect(db, Candidate(None, warnings=[OCR_MISS]))
    assert OCR_MISS in verdict.warnings


def test_extension_duplicate_with_a_boxed_stored_salary_is_enrichable(db) -> None:
    imported = extension_intake.import_one(db, Candidate(BOXED))
    imported.job.salary_text = BOXED
    db.commit()

    verdict = extension_intake.inspect(db, Candidate("16-19K"))
    assert verdict.status == "duplicate"
    assert verdict.enrichable_fields == ["salary_text"]

    verdict = extension_intake.inspect(db, Candidate(BOXED))
    assert verdict.enrichable_fields == []


# --------------------------------------------------------------------------
# the bounded backfill plan
# --------------------------------------------------------------------------


def add_job(db, number: int, *, salary: str | None) -> Job:
    job = Job(
        source="boss",
        external_id=f"job{number}",
        source_url=f"https://www.zhipin.com/job_detail/job{number}.html",
        company=f"公司{number}",
        title=f"岗位{number}",
        city="北京",
        salary_text=salary,
        raw_description=f"JD {number}",
        normalized_description=f"jd {number}",
        content_hash=hashlib.sha256(f"job-{number}".encode()).hexdigest(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_boxed_salary_jobs_re_enter_the_backfill_plan(db) -> None:
    boxed = add_job(db, 1, salary=BOXED)
    empty = add_job(db, 2, salary=None)
    add_job(db, 3, salary="16-19K")

    value = salary_backfill.plan(db)
    assert value["total_jobs"] == 3
    assert value["salary_present"] == 1
    assert value["salary_missing"] == 2
    assert [job.id for job in value["items"]] == [boxed.id, empty.id]


def test_a_boxed_salary_cannot_be_recorded_as_a_successful_backfill(db) -> None:
    boxed = add_job(db, 1, salary=BOXED)
    value = salary_backfill.plan(db)
    run = salary_backfill.create_run(
        db, job_ids=[boxed.id], expected_fingerprint=value["fingerprint"], confirmed=True
    )
    run = salary_backfill.start_or_resume(db, run.id)
    run = salary_backfill.claim_next(db, run.id)
    assert run.current_job_id == boxed.id

    with pytest.raises(Exception) as excinfo:
        salary_backfill.record_item(
            db, run.id, job_id=boxed.id, outcome="updated", reason=None
        )
    assert "canonical intake" in str(getattr(excinfo.value, "message", excinfo.value))

    boxed.salary_text = "16-19K"
    db.commit()
    run = salary_backfill.record_item(
        db, run.id, job_id=boxed.id, outcome="updated", reason=None
    )
    assert run.updated_jobs == 1
