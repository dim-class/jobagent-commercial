"""Every copy of the candidate ceiling must agree.

`MAX_CANDIDATE_CAP` lives in four places - the backend service, the request
schema, the extension worker and the console's own handshake validator - and
they only ever fail together when they disagree. On 2026-09-07 the console's
copy was still 20 while the rest were 60, so `assessConsoleConnection`
declared a perfectly healthy worker invalid the moment it reported a batch
with `candidateCap: 60`, the console held `connection` as null, and every
button returned at its first line with nothing on screen. Nothing errored;
the page simply stopped responding.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.schemas.supervised_session import SessionCreate
from app.services.supervised_sessions import MAX_CANDIDATE_CAP

ROOT = Path(__file__).resolve().parents[2]


def _int_after(path: Path, pattern: str) -> int:
    match = re.search(pattern, path.read_text(encoding="utf-8"))
    assert match, f"{pattern} not found in {path}"
    return int(match.group(1))


def test_the_request_schema_matches_the_service_ceiling():
    field = SessionCreate.model_fields["candidate_cap"]
    upper = next(m.le for m in field.metadata if getattr(m, "le", None) is not None)
    assert upper == MAX_CANDIDATE_CAP


def test_the_extension_worker_matches_the_service_ceiling():
    assert _int_after(
        ROOT / "extension/src/background.ts", r"RUNNER_MAX_CANDIDATES = (\d+)"
    ) == MAX_CANDIDATE_CAP


def test_the_popup_runner_matches_the_service_ceiling():
    assert _int_after(
        ROOT / "extension/src/runner.ts", r"RUNNER_MAX_CANDIDATES = (\d+)"
    ) == MAX_CANDIDATE_CAP


def test_the_console_handshake_validator_matches_the_service_ceiling():
    """The one that was wrong, and the one whose failure is silent."""
    assert _int_after(
        ROOT / "frontend/src/pages/consoleExtension.ts",
        r"MAX_CONSOLE_CANDIDATE_CAP = (\d+)",
    ) == MAX_CANDIDATE_CAP


def test_no_bare_twenty_is_left_guarding_a_candidate_cap():
    """A hardcoded 20 beside `candidateCap` is how this happened."""
    for rel in ("frontend/src/pages/consoleExtension.ts", "extension/src/runner.ts"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"candidateCap\s*>\s*20", text), rel


def test_the_worker_accepts_every_filter_the_backend_whitelists():
    """`isConsoleSearchUrl` predates filters and allowed only `city`+`query`,
    so a task carrying `experience=101,104` was refused as
    「已变化或不是待处理搜索任务」 - a message about the task's state, for a URL
    whose shape the worker simply did not recognise. The two lists move
    together."""
    from app.services.boss_search_filters import ALLOWED_FILTERS

    text = (ROOT / "extension/src/background.ts").read_text(encoding="utf-8")
    block = re.search(r"const CONSOLE_SEARCH_FILTERS = \[(.*?)\]", text, re.S)
    assert block, "CONSOLE_SEARCH_FILTERS not found"
    listed = set(re.findall(r"'([A-Za-z]+)'", block.group(1)))
    assert listed == set(ALLOWED_FILTERS), listed ^ set(ALLOWED_FILTERS)


def test_the_worker_still_refuses_a_filter_value_that_is_not_a_code():
    """The URL is about to be navigated to, so the value's shape is checked on
    both sides - a filter value is a code or a comma-separated list of them,
    never free text."""
    text = (ROOT / "extension/src/background.ts").read_text(encoding="utf-8")
    assert "const CONSOLE_FILTER_VALUE" in text
    assert "CONSOLE_FILTER_VALUE.test(" in text, "declared but never applied"
