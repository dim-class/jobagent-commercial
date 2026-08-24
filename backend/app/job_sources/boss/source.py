"""BossJobSource - human-driven capture from BOSS 直聘 (v0.2).

The human logs in, searches and opens a job. This adapter reads that one page.

Explicitly not implemented, and not to be implemented later:
  * ``search_jobs`` does not crawl BOSS search results. Mass collection is out
    of scope; the human chooses each posting.
  * ``get_job`` cannot fetch by id, because doing so would mean navigating on
    the user's behalf behind their login session.
Both return empty rather than pretending to work.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from playwright.async_api import Page

from app.job_sources.base import BrowserJobSource, JobSearchQuery, RawJobPosting
from app.job_sources.boss import selectors as sel
from app.job_sources.boss.extractor import (
    ExtractionReport,
    NotAJobPageError,
    PageEmptyError,
    VerificationRequiredError,
    extract_job,
    looks_like_job_url,
    parse_external_id,
)

__all__ = [
    "BossJobSource",
    "ExtractionReport",
    "NotAJobPageError",
    "PageEmptyError",
    "VerificationRequiredError",
    "boss_source",
]


class BossJobSource(BrowserJobSource):
    name = "boss"
    implemented = True
    hosts = ("zhipin.com",)

    # -- URL handling -----------------------------------------------------

    def supports_host(self, url: str) -> bool:
        """True when the URL is on a BOSS domain (any page, not just a job)."""
        try:
            host = (urlsplit(url or "").hostname or "").lower()
        except ValueError:
            return False
        return any(host == h or host.endswith("." + h) for h in self.hosts)

    def supports_url(self, url: str) -> bool:
        """True when the URL is a BOSS *job detail* page."""
        return self.supports_host(url) and looks_like_job_url(url)

    def external_id_for(self, url: str) -> str | None:
        return parse_external_id(url)

    # -- capture ----------------------------------------------------------

    async def capture_from_page(self, page: Page) -> RawJobPosting:
        posting, _report = await self.capture_with_report(page)
        return posting

    async def capture_with_report(self, page: Page) -> tuple[RawJobPosting, ExtractionReport]:
        """Read the open page, also returning which fields were found."""
        return await extract_job(page)

    # -- JobSource contract -----------------------------------------------

    def search_jobs(self, query: JobSearchQuery) -> list[RawJobPosting]:
        """Not supported by design - see the module docstring."""
        return []

    def get_job(self, external_id: str) -> RawJobPosting | None:
        """Not supported by design - see the module docstring."""
        return None

    # -- introspection used by the API layer ------------------------------

    @property
    def verification_hints(self) -> tuple[str, ...]:
        return sel.VERIFICATION_HINTS


boss_source = BossJobSource()
