"""No real API key may sit in a shareable file.

`.env` is gitignored and holds the real key - that is correct and this test
never reads it. Everything else in the repo is shareable: source, docs,
fixtures, scripts, and `.env.example`, which exists precisely to be committed.

A real key has landed in `.env.example` twice during development (v0.6 and
v0.7), so this is a test rather than a habit.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Directories that are either generated, vendored, or deliberately private.
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "data",          # the local database and browser profile live here
    "browser_profiles",
}

SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".yaml", ".yml",
    ".ps1", ".sh", ".txt", ".html", ".css", ".cfg", ".ini", ".toml", ".example",
}

EXTRA_FILES = {".env.example", ".gitignore"}

#: An OpenAI key: the `T3BlbkFJ` infix is present in every real one, and the
#: leading segment is long. Deliberately narrow so obvious placeholders
#: ("sk-your-key-here") and the test literal in test_health.py do not match.
REAL_KEY = re.compile(r"sk-[A-Za-z0-9_-]{20,}T3BlbkFJ[A-Za-z0-9_-]{20,}")

#: A looser shape, used only for files that should carry no key at all.
LONG_KEY_SHAPE = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{40,}")


def shareable_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example":
            continue  # the real key belongs here, and this test never reads it
        if path.suffix in SUFFIXES or path.name in EXTRA_FILES:
            files.append(path)
    return files


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):  # pragma: no cover - binary/locked
        return ""


def test_the_scan_actually_finds_files():
    """Guards the test itself: a silent zero-file scan would pass forever."""
    files = shareable_files()
    assert len(files) > 50, f"expected to scan the repo, found {len(files)} files"
    names = {f.name for f in files}
    assert "README.md" in names
    assert ".env.example" in names


def test_no_real_openai_key_in_any_shareable_file():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in shareable_files()
        if REAL_KEY.search(read(path))
    ]
    assert not offenders, f"a real-looking OpenAI key is committed in: {offenders}"


def test_env_example_holds_only_a_placeholder():
    """The template is committed, so it must never carry a working key."""
    content = read(REPO_ROOT / ".env.example")
    assert "OPENAI_API_KEY=" in content, "the template still documents the variable"
    assert not LONG_KEY_SHAPE.search(content), ".env.example contains a real-looking key"


def test_env_is_gitignored():
    ignored = read(REPO_ROOT / ".gitignore").splitlines()
    assert ".env" in [line.strip() for line in ignored]


@pytest.mark.parametrize(
    "sample",
    [
        "sk-proj-s7c7cnv65yp4lhZEaDXmLftQnmcgTQt_yU0NO8FYUotTwRqQTPY2ODQsAgN1"
        "BvCZvPuHKMZJMaT3BlbkFJu9894uiyM5C3x-76rupxamJVTJ1OtTJgtLYOzRaFSZ7wgb",
    ],
)
def test_the_pattern_would_catch_a_real_key(sample):
    """A detector that matches nothing is worse than no detector."""
    assert REAL_KEY.search(sample)
    assert LONG_KEY_SHAPE.search(sample)


@pytest.mark.parametrize(
    "sample",
    [
        "sk-your-key-here",
        "sk-should-never-appear-in-a-response",
        "OPENAI_API_KEY=",
        "sk-test",
    ],
)
def test_placeholders_do_not_trip_the_detector(sample):
    assert not REAL_KEY.search(sample)


def test_the_key_is_never_returned_by_the_api(client, monkeypatch):
    """End-to-end: no endpoint may echo the configured key."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-appear-in-a-response")

    for path in ("/health", "/api/settings", "/api/analytics/resumes"):
        body = client.get(path).text
        assert "sk-should-never-appear" not in body, f"{path} leaked the key"


# --------------------------------------------------------------------------
# compensation (v0.9)
# --------------------------------------------------------------------------


class _Capture(logging.Handler):
    """Collect formatted log lines from one logger.

    ``caplog`` does not see these: ``core.logging.setup_logging`` clears the
    root handlers when ``app.main`` is imported at collection time. Attaching
    directly keeps the assertions below meaningful rather than vacuous.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        kv = getattr(record, "kv", {}) or {}
        self.lines.append(f"{record.getMessage()} {kv!r}")

    @property
    def text(self) -> str:
        return chr(10).join(self.lines)


@contextmanager
def capture(*logger_names: str):
    handler = _Capture()
    loggers = [logging.getLogger(name) for name in logger_names]
    previous = [(lg, lg.level) for lg in loggers]
    try:
        for lg in loggers:
            lg.addHandler(handler)
            lg.setLevel(logging.DEBUG)
        yield handler
    finally:
        for lg, level in previous:
            lg.removeHandler(handler)
            lg.setLevel(level)


def test_compensation_is_never_written_to_a_log(db):
    """Salary, bonus and equity figures must not reach the log.

    Log lines carry offer ids and status transitions; the numbers stay in the
    database and in the API responses that need them.
    """
    from app.schemas.offer import (
        AcceptOfferRequest,
        CompensationFields,
        CounterRequest,
        OfferCreateRequest,
    )
    from app.services import offer_management

    from tests.test_offer_management import applied_job

    job = applied_job(db)

    with capture(
        "app.services.offer_management", "app.services.application_workflow"
    ) as logs:
        offer = offer_management.create_offer(
            db,
            job.id,
            OfferCreateRequest(
                confirmed=True,
                initial=CompensationFields(
                    base_salary_annual=317_531,
                    bonus_target=64_729,
                    signing_bonus=21_483,
                    stock_value=412_907,
                    salary_text_original="年薪31.7531万，签字费2.1483万",
                ),
                notes="非常私密的 Offer 备注",
            ),
        )
        offer_management.record_counter(
            db, offer.id, CounterRequest(base_salary_annual=358_211)
        )
        offer_management.accept_offer(db, offer.id, AcceptOfferRequest(confirmed=True))

    logged = logs.text
    # The capture must actually have captured, or the rest proves nothing.
    assert "offer.created" in logged
    assert "offer.accepted" in logged

    for secret in ("317531", "64729", "21483", "412907", "358211"):
        assert secret not in logged, f"a compensation figure reached the log: {secret}"
    assert "非常私密的 Offer 备注" not in logged
    assert "31.7531" not in logged, "the pasted offer wording must not be logged"


def test_the_offer_log_still_carries_useful_identifiers(db):
    """Privacy must not mean logging nothing at all."""
    from app.schemas.offer import CompensationFields, OfferCreateRequest
    from app.services import offer_management

    from tests.test_offer_management import applied_job

    job = applied_job(db)
    with capture("app.services.offer_management") as logs:
        offer = offer_management.create_offer(
            db,
            job.id,
            OfferCreateRequest(
                confirmed=True,
                initial=CompensationFields(base_salary_annual=300_000),
            ),
        )

    assert f"'offer_id': {offer.id}" in logs.text
    assert "'currency': 'CNY'" in logs.text


# --------------------------------------------------------------------------
# decision support (v1.0)
# --------------------------------------------------------------------------


def test_decision_preferences_are_never_written_to_a_log(db):
    """Weights, ratings, rates, targets and notes stay out of the log.

    They describe what the user privately values and what they will settle for
    - arguably more sensitive than the salary figures themselves.
    """
    from app.services import decision_support

    from tests.test_offer_management import applied_job, make_offer

    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})

    with capture("app.services.decision_support") as logs:
        decision_support.update_profile(
            db,
            weights={"compensation": 7317, "visa_support": 4192},
            deal_breakers=[
                {"kind": "minimum_guaranteed_cash", "value": 318_264},
                {"kind": "required_location", "value": "东京都涩谷区"},
            ],
            fx_rates={"JPY": 0.048271},
            notes="其实我最在意的是能不能远程",
        )
        decision_support.upsert_assessment(
            db,
            offer.id,
            ratings={"career_growth": 2, "work_life_balance": 5},
            notes="面试官提到经常加班",
            minimum_total_cash=287_913,
            target_total_cash=412_556,
        )
        result, profile, facts = decision_support.compare(
            db, [offer.id, make_offer(db, applied_job(db)).id]
        )
        decision_support.save_snapshot(
            db, name="对比", result=result, profile=profile, facts_by_offer=facts
        )

    logged = logs.text
    # The capture must actually have captured, or the rest proves nothing.
    assert "decision.profile_updated" in logged
    assert "decision.assessment_saved" in logged
    assert "decision.snapshot_saved" in logged

    for secret in ("7317", "4192", "318264", "0.048271", "287913", "412556"):
        assert secret not in logged, f"a private preference reached the log: {secret}"
    assert "东京都涩谷区" not in logged
    assert "其实我最在意的是能不能远程" not in logged
    assert "面试官提到经常加班" not in logged


def test_the_decision_log_still_carries_useful_identifiers(db):
    """Privacy must not mean logging nothing at all."""
    from app.services import decision_support

    from tests.test_offer_management import applied_job, make_offer

    offer = make_offer(db, applied_job(db), initial={"base_salary_annual": 300_000})

    with capture("app.services.decision_support") as logs:
        decision_support.upsert_assessment(db, offer.id, ratings={"role_fit": 4})

    assert f"'offer_id': {offer.id}" in logs.text
    assert "'rated_dimensions': 1" in logs.text
