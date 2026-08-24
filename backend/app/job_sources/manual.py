"""ManualJobSource - the only source implemented in v0.1.

The "collector" is the human: they copy a JD out of a recruiting site and paste
it into the 添加岗位 form. Everything downstream (normalization, hashing,
duplicate detection, analysis) is identical to what a future automated
collector will use, which is the whole point of the abstraction.
"""

from __future__ import annotations

from app.job_sources.base import JobSearchQuery, JobSource, RawJobPosting


class ManualJobSource(JobSource):
    name = "manual"
    implemented = True

    def search_jobs(self, query: JobSearchQuery) -> list[RawJobPosting]:
        """Manual input has nothing to search - the user supplies the posting."""
        return []

    def get_job(self, external_id: str) -> RawJobPosting | None:
        """Manual postings have no source-native id to fetch by."""
        return None

    def from_form(
        self,
        *,
        title: str,
        company: str,
        raw_description: str,
        city: str | None = None,
        salary_text: str | None = None,
        experience_text: str | None = None,
        education_text: str | None = None,
        source_url: str | None = None,
        external_id: str | None = None,
    ) -> RawJobPosting:
        """Build a posting from the 添加岗位 form payload."""
        return RawJobPosting(
            title=title,
            company=company,
            raw_description=raw_description,
            city=city,
            salary_text=salary_text,
            experience_text=experience_text,
            education_text=education_text,
            source_url=source_url,
            external_id=external_id,
        )


manual_source = ManualJobSource()
