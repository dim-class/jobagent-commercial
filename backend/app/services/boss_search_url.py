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

from app.services.boss_search_filters import ALLOWED_FILTERS

#: The exact origin CLAUDE.md's M4 policy and `extension/manifest.json`'s
#: `content_scripts.matches` already require - never a subdomain, never a
#: different scheme.
BOSS_ORIGIN = "https://www.zhipin.com"
BOSS_SEARCH_PATH = "/web/geek/jobs"


def build_search_url(
    city_id: str, keyword: str, filters: dict[str, str] | None = None
) -> str:
    """One validated, same-origin BOSS results-page URL.

    ``city_id`` and ``keyword`` are taken as already-validated (city_id from
    ``boss_cities.city_id_for``, keyword from the approved SearchPlan row) -
    this function only assembles them; it never validates a city name itself,
    so a caller skipping that step is a programming error, not a URL this
    function could quietly "fix".

    ``filters`` narrows the search so a repeat run sees different postings at
    the top - see ``boss_search_filters``. It must already have come through
    ``parse_filters``, which is what bounds the keys and the value shapes; this
    function re-checks the keys anyway, because it is the last place before a
    URL the extension will navigate to. ``city`` and ``query`` can never be
    overridden: they are written after the filters, so even a filter dict that
    somehow carried them would not change the search the console displays.
    """
    params: list[tuple[str, str]] = []
    for key, value in sorted((filters or {}).items()):
        if key in ALLOWED_FILTERS:
            params.append((key, value))
    params.append(("city", city_id))
    params.append(("query", keyword))
    # A comma stays a comma: BOSS writes multi-value filters as
    # `experience=104,101` and the generated URL should have the shape the
    # site produces, not a percent-encoded variant of it. Nothing else is
    # exempted - the keyword still gets fully encoded.
    return f"{BOSS_ORIGIN}{BOSS_SEARCH_PATH}?{urlencode(params, safe=',')}"
