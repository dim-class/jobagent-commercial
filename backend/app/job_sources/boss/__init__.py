"""BOSS 直聘 adapter (v0.2) - human-driven capture from a visible browser.

The user logs in, searches and opens a posting themselves; this package reads
that one page. No login automation, no credential storage, no CAPTCHA handling,
no search-result crawling, and nothing that applies or messages a recruiter.

Layout:
    selectors.py  every BOSS selector, centralised - the only file to edit
                  when the site's markup changes
    extractor.py  Playwright Locator reads -> RawJobPosting
    source.py     BossJobSource, the JobSource/BrowserJobSource implementation
"""

from app.job_sources.boss.source import BossJobSource, boss_source

__all__ = ["BossJobSource", "boss_source"]
