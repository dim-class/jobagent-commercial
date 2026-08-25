# Extension test fixtures

Hand-written HTML that *resembles* the structure of a BOSS 直聘 page, written
for this repository. They are not copies of any real page, and every company,
recruiter, salary and vacancy in them is invented.

They exist so the extension's extraction can be tested offline. No automated
test may ever contact zhipin.com.

| file | what it covers |
| --- | --- |
| `boss_search.html` | a search results page with several rendered cards |
| `boss_job_detail.html` | one job detail page, all fields present |
| `boss_job_no_salary.html` | a detail page with no visible salary |
| `boss_job_pua_salary.html` | a detail page whose salary is obfuscated by a PUA glyph font |
| `boss_job_detail_live_shape.html` | a dedicated detail page using the live-observed `.job-banner .info-primary` / `.job-boss-info` markup for company, city, experience and education |
| `boss_search_split_pane_live_shape.html` | `/web/geek/jobs`: a rendered card list plus the selected-job detail pane BOSS renders beside it, proving the page reads as `search` (never as one job from the pane) and that duplicate-title/company cards each keep their own fields |
| `boss_search_fallback_card_discovery.html` | `/web/geek/jobs` with none of the fixed `BossSelectors.CARD` shapes present at all, proving cards are still found from their own canonical `/job_detail/` anchors, a duplicate in-card anchor dedupes to one card, the selected pane's own link is never a card, and fields never mix |
| `unsupported.html` | a BOSS page that is neither a search nor a detail page |

Because the fixtures mirror *conventions* rather than a captured DOM, a passing
test proves the extractor handles the shapes it was written for - it does not
prove live BOSS compatibility. Only the manual check in `extension/README.md`
can do that.
