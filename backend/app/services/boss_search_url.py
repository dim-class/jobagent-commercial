"""Deterministic, same-origin BOSS search URL construction (M4e, explicitly
authorized).

Builds exactly one URL of the shape
``https://www.zhipin.com/web/geek/jobs?city=<id>&query=<keyword>`` from an
already-validated city code and keyword - never a loose string concatenation,
never a URL a caller supplies. The origin is a literal constant, never
interpolated, so this can never produce a cross-origin URL regardless of
input. Building the URL does not fetch, navigate, or otherwise contact
anything - see CLAUDE.md's M4e/M4f amendment: only an explicit, separately
human-approved foreground-tab navigation may ever use it.
"""

from __future__ import annotations

from urllib.parse import urlencode

#: The exact origin CLAUDE.md's M4 policy and `extension/manifest.json`'s
#: `content_scripts.matches` already require - never a subdomain, never a
#: different scheme.
BOSS_ORIGIN = "https://www.zhipin.com"
BOSS_SEARCH_PATH = "/web/geek/jobs"


def build_search_url(city_id: str, keyword: str) -> str:
    """One validated, same-origin BOSS results-page URL.

    ``city_id`` and ``keyword`` are taken as already-validated (city_id from
    ``boss_cities.city_id_for``, keyword from the approved SearchPlan row) -
    this function only assembles them; it never validates a city name itself,
    so a caller skipping that step is a programming error, not a URL this
    function could quietly "fix".
    """
    query = urlencode({"city": city_id, "query": keyword})
    return f"{BOSS_ORIGIN}{BOSS_SEARCH_PATH}?{query}"
