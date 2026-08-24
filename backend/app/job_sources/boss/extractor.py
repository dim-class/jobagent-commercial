"""Read one BOSS job-detail page into a :class:`RawJobPosting`.

Pure extraction: it takes a Playwright ``Page`` that the human already opened
and returns structured fields. It never navigates, clicks, logs in, or touches
any control on the page.

Design notes:
  * every selector comes from :mod:`.selectors` - none are inlined here;
  * each field tries several candidates and tolerates all of them missing;
  * only ``title`` and ``raw_description`` are required for a usable capture;
  * the description is read from the job-description container, not from
    ``document.body``, so navigation, footers, recommended jobs and chat
    history stay out of the stored JD.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.async_api import Error as PlaywrightError, Page

from app.core.logging import get_logger, log_event
from app.job_sources.base import RawJobPosting
from app.job_sources.boss import selectors as sel

logger = get_logger(__name__)

#: Hard ceiling so a pathological page cannot blow up the database or a prompt.
MAX_DESCRIPTION_CHARS = 20_000
#: Below this a "description" is almost certainly a spinner or an error page.
MIN_DESCRIPTION_CHARS = 30
#: Below this the whole document is effectively blank.
MIN_BODY_CHARS = 40

_LOCATOR_TIMEOUT_MS = 2_000


class NotAJobPageError(Exception):
    """The current page is not a recognisable BOSS job detail page."""


class VerificationRequiredError(Exception):
    """BOSS is showing a verification interstitial. The human must clear it."""


class PageEmptyError(Exception):
    """The page rendered nothing.

    In practice this means the recruitment site declined to render content in
    an automation-controlled browser. We report that plainly; defeating such a
    restriction is out of scope by policy (see CLAUDE.md).
    """


@dataclass(slots=True)
class ExtractionReport:
    """What we managed to read, for logging and the capture response."""

    fields_found: list[str]
    fields_missing: list[str]
    description_chars: int


# --------------------------------------------------------------------------
# low-level helpers
# --------------------------------------------------------------------------


async def _first_text(page: Page, candidates: tuple[str, ...]) -> str | None:
    """Text of the first candidate selector that matches something non-empty."""
    for selector in candidates:
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            text = await locator.inner_text(timeout=_LOCATOR_TIMEOUT_MS)
        except PlaywrightError:
            continue
        cleaned = _collapse(text)
        if cleaned:
            return cleaned
    return None


async def _all_texts(page: Page, candidates: tuple[str, ...]) -> list[str]:
    """Texts of every element matched by the first candidate that hits."""
    for selector in candidates:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            if count == 0:
                continue
            out: list[str] = []
            for index in range(min(count, 12)):
                try:
                    text = await locator.nth(index).inner_text(timeout=_LOCATOR_TIMEOUT_MS)
                except PlaywrightError:
                    continue
                cleaned = _collapse(text)
                if cleaned:
                    out.append(cleaned)
            if out:
                return out
        except PlaywrightError:
            continue
    return []


def _collapse(text: str | None) -> str:
    """Trim and squeeze runs of blank lines/spaces, preserving line structure."""
    if not text:
        return ""
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    blank = 0
    for line in lines:
        if line:
            blank = 0
            out.append(line)
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip()


# --------------------------------------------------------------------------
# field parsing
# --------------------------------------------------------------------------


def parse_external_id(url: str) -> str | None:
    """``/job_detail/abc123.html`` -> ``abc123``."""
    for pattern in sel.JOB_URL_PATTERNS:
        match = pattern.search(url or "")
        if match:
            return match.group("id")
    return None


def looks_like_job_url(url: str) -> bool:
    lowered = (url or "").lower()
    if any(hint in lowered for hint in sel.NON_JOB_PATH_HINTS) and not parse_external_id(url):
        return False
    return parse_external_id(url) is not None


def parse_info_tags(tags: list[str]) -> tuple[str | None, str | None, str | None]:
    """Split the tag strip into (city, experience, education).

    Input looks like ``["北京 朝阳区 · 3-5年 · 本科"]`` or already-split
    ``["北京", "3-5年", "本科"]``. Both shapes are handled.
    """
    parts: list[str] = []
    for tag in tags:
        for piece in sel.TAG_SPLIT_RE.split(tag):
            piece = piece.strip(" ·|｜\n")
            if piece:
                parts.append(piece)

    city = experience = education = None
    for part in parts:
        if education is None:
            for token in sel.EDUCATION_TOKENS:
                if token in part:
                    education = token
                    break
            if education is not None:
                continue
        if experience is None:
            match = sel.EXPERIENCE_RE.search(part)
            if match:
                experience = match.group(0).replace(" ", "")
                continue
        if city is None and part:
            # The first leftover token is the location, e.g. "北京 朝阳区".
            city = part.split()[0]
    return city, experience, education


async def detect_verification(page: Page) -> bool:
    """True when BOSS is asking the human to verify.

    Detection only. We never solve, bypass or auto-dismiss a challenge - the
    caller surfaces a message asking the user to complete it themselves.
    """
    url = (page.url or "").lower()
    if any(hint in url for hint in sel.VERIFICATION_HINTS):
        return True
    try:
        title = (await page.title() or "").lower()
    except PlaywrightError:
        title = ""
    if any(hint.lower() in title for hint in sel.VERIFICATION_HINTS):
        return True
    for hint in sel.VERIFICATION_HINTS:
        if not hint.isascii():
            try:
                if await page.get_by_text(hint, exact=False).count() > 0:
                    return True
            except PlaywrightError:
                continue
    return False


async def _description(page: Page) -> str:
    """Read the JD body from its container, minus obvious noise."""
    for selector in sel.DESCRIPTION:
        try:
            locator = page.locator(selector)
            if await locator.count() == 0:
                continue
            chunks: list[str] = []
            for index in range(min(await locator.count(), 6)):
                try:
                    text = await locator.nth(index).inner_text(timeout=_LOCATOR_TIMEOUT_MS)
                except PlaywrightError:
                    continue
                cleaned = _collapse(text)
                if cleaned:
                    chunks.append(cleaned)
            body = _collapse("\n\n".join(chunks))
            if len(body) >= MIN_DESCRIPTION_CHARS:
                return body[:MAX_DESCRIPTION_CHARS]
        except PlaywrightError:
            continue
    return ""


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


async def _body_is_empty(page: Page) -> bool:
    """True when the document has essentially no rendered text."""
    try:
        text = await page.locator("body").first.inner_text(timeout=_LOCATOR_TIMEOUT_MS)
    except PlaywrightError:
        return True
    return len(_collapse(text)) < MIN_BODY_CHARS


async def extract_job(page: Page, *, url: str | None = None) -> tuple[RawJobPosting, ExtractionReport]:
    """Read the job the human currently has open.

    Raises :class:`VerificationRequiredError` or :class:`NotAJobPageError`
    rather than saving something useless.
    """
    page_url = url or page.url or ""

    if await detect_verification(page):
        raise VerificationRequiredError

    if await _body_is_empty(page):
        raise PageEmptyError

    title = await _first_text(page, sel.TITLE)
    description = await _description(page)

    if not title or not description:
        # Distinguish "wrong page" from "right page, markup drifted": if the
        # URL is not a job-detail URL it is simply the wrong page.
        raise NotAJobPageError

    company = await _first_text(page, sel.COMPANY)
    salary = await _first_text(page, sel.SALARY)
    city, experience, education = parse_info_tags(await _all_texts(page, sel.INFO_TAGS))

    extra: dict[str, str] = {}
    for key, candidates in (
        ("company_industry", sel.COMPANY_INDUSTRY),
        ("company_size", sel.COMPANY_SIZE),
        ("recruiter_name", sel.RECRUITER_NAME),
    ):
        value = await _first_text(page, candidates)
        if value:
            extra[key] = value

    posting = RawJobPosting(
        title=title,
        company=company or "",
        raw_description=description,
        city=city,
        salary_text=salary,
        experience_text=experience,
        education_text=education,
        source_url=page_url,
        external_id=parse_external_id(page_url),
        extra=extra,
    )

    found = [
        name
        for name, value in (
            ("title", title),
            ("company", company),
            ("city", city),
            ("salary_text", salary),
            ("experience_text", experience),
            ("education_text", education),
        )
        if value
    ]
    missing = [
        name
        for name in ("company", "city", "salary_text", "experience_text", "education_text")
        if name not in found
    ]
    report = ExtractionReport(
        fields_found=found, fields_missing=missing, description_chars=len(description)
    )

    # Never log page content - counts and field names only.
    log_event(
        logger,
        "browser.extracted",
        source="boss",
        external_id=posting.external_id,
        jd_chars=report.description_chars,
        found=",".join(found),
        missing=",".join(missing) or "-",
    )
    return posting, report
