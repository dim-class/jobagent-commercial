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
| `unsupported.html` | a BOSS page that is neither a search nor a detail page |

Because the fixtures mirror *conventions* rather than a captured DOM, a passing
test proves the extractor handles the shapes it was written for - it does not
prove live BOSS compatibility. Only the manual check in `extension/README.md`
can do that.
