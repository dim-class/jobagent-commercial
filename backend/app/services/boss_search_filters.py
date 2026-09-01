"""Reuse the filters a human already chose on a BOSS search page.

BOSS's results list is ordered by relevance and has no "newest first" control,
so a repeated search of the same keyword keeps showing the same top results and
a run spends its candidate budget on postings already stored. Narrowing the
query is what changes *which* postings sit at the top: 朝阳区 and 海淀区 return
largely disjoint lists, and so do two salary bands.

The filter codes are BOSS's own (`salary=406`, `multiBusinessDistrict=110105`)
and this module deliberately holds **no table of them**. Guessing that 407 means
30-50K, or that a Hangzhou district keeps its pre-2021 code, would put a wrong
search in front of the user with no way to notice. Instead the human builds the
search they want in their own browser and pastes the URL; we keep the parameters
verbatim.

Nothing here fetches anything. A pasted URL is metadata - exactly as
`services/urls.py` already treats one.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

from app.core.errors import ValidationError

#: The BOSS results page this can read, and nothing else.
_SEARCH_ORIGIN = "https://www.zhipin.com"
_SEARCH_PATH = "/web/geek/jobs"

#: Parameters that may be carried over. `city` and `query` are excluded on
#: purpose: those come from the user's own city choice and from the résumé
#: ranking, and letting a pasted URL override them would silently search for
#: something other than what the console displays.
ALLOWED_FILTERS: frozenset[str] = frozenset(
    {
        "salary",
        "multiBusinessDistrict",
        "experience",
        "degree",
        "industry",
        "scale",
        "stage",
        "jobType",
        "position",
    }
)

#: Every known BOSS filter value is a code or a comma-separated list of codes.
#: Anything else is refused rather than passed through - a filter value is not
#: free text, and this string ends up in a URL the extension will navigate to.
_VALUE = re.compile(r"^[0-9]{1,12}(?:,[0-9]{1,12}){0,19}$")

#: A guard on the whole set, so a pasted URL cannot make an unbounded query.
MAX_FILTERS = 8


def parse_filters(url: str | None) -> dict[str, str]:
    """Extract the whitelisted filters from a pasted BOSS search URL.

    Raises `ValidationError` with an actionable Chinese message when the URL is
    not a BOSS results page. An unknown parameter is dropped silently - BOSS
    adds tracking keys to its own links, and refusing the paste over one would
    be unhelpful - but a *recognised* parameter carrying an unrecognised value
    is an error, because that is the case where we would otherwise navigate to
    something we did not understand.
    """
    parts = urlsplit((url or "").strip())
    if f"{parts.scheme}://{parts.netloc}" != _SEARCH_ORIGIN or parts.path != _SEARCH_PATH:
        raise ValidationError(
            "请粘贴 BOSS 直聘的职位搜索页地址"
            "（形如 https://www.zhipin.com/web/geek/jobs?...）。",
            detail={"expected_path": _SEARCH_PATH},
        )

    filters: dict[str, str] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        if key not in ALLOWED_FILTERS:
            continue
        if not _VALUE.match(value):
            raise ValidationError(
                f"筛选参数「{key}」的取值无法识别，已停止处理，未使用该链接。",
                detail={"parameter": key},
            )
        filters[key] = value

    if len(filters) > MAX_FILTERS:
        raise ValidationError(
            f"该链接包含 {len(filters)} 个筛选条件，超过上限 {MAX_FILTERS} 个。",
            detail={"count": len(filters), "max": MAX_FILTERS},
        )
    return filters


def describe(filters: dict[str, str]) -> str:
    """A short, honest label for the console.

    It names the parameters, never their meaning: this module does not know
    that `salary=406` is 20-30K, and inventing a translation is exactly the
    guess it refuses to make. The human chose these on BOSS and can read them
    back there.
    """
    if not filters:
        return "无附加筛选"
    return "、".join(f"{key}={value}" for key, value in sorted(filters.items()))
