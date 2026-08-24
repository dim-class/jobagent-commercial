"""JobSource - the seam future collectors plug into.

v0.1 only ships :class:`~app.job_sources.manual.ManualJobSource`. The interface
exists now so that adding a Playwright-driven collector in v0.2/v0.3 is a new
file rather than a refactor of the API layer and the matcher.

Rules that apply to every future adapter (see also CLAUDE.md):
  * the user drives the login; we never store recruitment-site passwords;
  * no CAPTCHA solving, no anti-bot evasion, no stealth fingerprinting;
  * respect the site's rate limits and terms;
  * ``AI recommends -> human approves -> browser executes``; a source may
    *collect*, it may never *apply*.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.services.job_normalizer import NormalizedJob, normalize_job


@dataclass(slots=True)
class RawJobPosting:
    """Whatever a source managed to scrape/receive, before normalization."""

    title: str
    company: str
    raw_description: str
    city: str | None = None
    salary_text: str | None = None
    experience_text: str | None = None
    education_text: str | None = None
    source_url: str | None = None
    external_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class JobSearchQuery:
    keywords: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    limit: int = 20


class JobSource(ABC):
    """A place jobs come from."""

    #: Value stored in ``Job.source``; must match ``JobSourceName``.
    name: str = "base"

    #: False for adapters that are documented but not implemented yet.
    implemented: bool = False

    @abstractmethod
    def search_jobs(self, query: JobSearchQuery) -> list[RawJobPosting]:
        """Return postings matching ``query``. May be a no-op for manual input."""

    @abstractmethod
    def get_job(self, external_id: str) -> RawJobPosting | None:
        """Fetch one posting by its source-native id."""

    def normalize_job(self, posting: RawJobPosting) -> NormalizedJob:
        """Deterministic normalization shared by every source."""
        return normalize_job(
            company=posting.company,
            title=posting.title,
            raw_description=posting.raw_description,
            city=posting.city,
            salary_text=posting.salary_text,
            experience_text=posting.experience_text,
            education_text=posting.education_text,
            source_url=posting.source_url,
        )


class BrowserJobSource(JobSource):
    """A source that reads the page a human already has open (v0.2+).

    This is an *additive* refinement of :class:`JobSource`, not a replacement:
    normalization, hashing and persistence are inherited unchanged, so a
    browser-captured job is indistinguishable downstream from a pasted one.

    Implementations must remain read-only. Collecting what the user is already
    looking at is in scope; logging in, navigating on their behalf, paginating
    search results, or pressing any apply/message control is not.
    """

    #: Registrable domains this adapter can read.
    hosts: tuple[str, ...] = ()

    @abstractmethod
    def supports_url(self, url: str) -> bool:
        """True when ``url`` is a job detail page this adapter can read."""

    @abstractmethod
    async def capture_from_page(self, page: Any) -> RawJobPosting:
        """Read a live Playwright ``Page`` into a posting."""
