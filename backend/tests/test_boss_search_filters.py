"""Reusing the filters a human already chose on BOSS.

BOSS's results list has no "newest first" control - the user checked - so the
only way to reach past the same top results is to narrow the query. The codes
are BOSS's own, and this module holds no table of them on purpose: guessing
that 407 means 30-50K would put a wrong search in front of the user with no way
to notice.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.services.boss_search_filters import describe, parse_filters, with_experience
from app.services.boss_search_url import build_search_url

REAL = (
    "https://www.zhipin.com/web/geek/jobs"
    "?city=101010100&multiBusinessDistrict=110105&salary=406"
    "&query=%E4%BA%91%E8%BF%81%E7%A7%BB%E5%B7%A5%E7%A8%8B%E5%B8%88"
)


def test_it_reads_the_filters_from_a_real_pasted_url():
    assert parse_filters(REAL) == {"multiBusinessDistrict": "110105", "salary": "406"}


def test_city_and_query_can_never_come_from_the_pasted_url():
    """Those come from the user's own city choice and the résumé ranking.

    A pasted link that silently changed either would search for something other
    than what the console says it is searching for.
    """
    assert "city" not in parse_filters(REAL)
    assert "query" not in parse_filters(REAL)

    url = build_search_url("101010100", "云计算工程师", {"city": "999", "query": "evil"})
    assert "city=101010100" in url and "city=999" not in url
    assert "query=evil" not in url


def test_a_non_boss_or_non_search_url_is_refused():
    for bad in (
        "https://evil.example/web/geek/jobs?salary=406",
        "http://www.zhipin.com/web/geek/jobs?salary=406",
        "https://www.zhipin.com/job_detail/abc.html",
        "",
        None,
    ):
        with pytest.raises(ValidationError):
            parse_filters(bad)


def test_a_recognised_parameter_with_an_unreadable_value_stops_the_paste():
    """Filter values are codes, never free text - and this string ends up in a
    URL the extension will navigate to."""
    for bad in ("abc", "406; DROP TABLE", "406 OR 1=1", "../../etc"):
        with pytest.raises(ValidationError):
            parse_filters(f"https://www.zhipin.com/web/geek/jobs?salary={bad}")


def test_an_unknown_parameter_is_dropped_rather_than_refused():
    """BOSS adds its own tracking keys; refusing a paste over one is unhelpful."""
    url = "https://www.zhipin.com/web/geek/jobs?salary=406&lid=abc&securityId=def&utm=x"
    assert parse_filters(url) == {"salary": "406"}


def test_the_url_it_builds_keeps_the_filters_and_stays_same_origin():
    url = build_search_url("101010100", "云计算工程师", parse_filters(REAL))
    assert url.startswith("https://www.zhipin.com/web/geek/jobs?")
    assert "salary=406" in url and "multiBusinessDistrict=110105" in url


def test_the_label_names_parameters_and_never_invents_their_meaning():
    """`salary=406` is 20-30K on BOSS today; this code does not know that, and
    saying so would be a guess the user could not check."""
    assert describe({"salary": "406"}) == "salary=406"
    assert describe({}) == "无附加筛选"


MULTI = "https://www.zhipin.com/web/geek/jobs?city=101010100&experience=104,101"


def test_a_multi_value_filter_survives_the_round_trip():
    """BOSS writes several selected bands as one comma-separated value."""
    assert parse_filters(MULTI) == {"experience": "104,101"}


def test_a_comma_stays_a_comma_in_the_generated_url():
    """The URL handed to the extension should have the shape the site itself
    produces, not a percent-encoded variant of it."""
    url = build_search_url("101010100", "云计算工程师", parse_filters(MULTI))
    assert "experience=104,101" in url
    assert "%2C" not in url
    # The keyword is still encoded - only the comma is exempted.
    assert "query=%E4%BA%91" in url


def test_a_comma_cannot_smuggle_anything_past_the_value_check():
    for bad in ("104,abc", "104,,101", "104, 101", ",104", "104,"):
        with pytest.raises(ValidationError):
            parse_filters(f"https://www.zhipin.com/web/geek/jobs?experience={bad}")


# --------------------------------------------------------------------------
# experience: a constraint on every search, never a segment of its own
# --------------------------------------------------------------------------


def test_experience_constrains_every_segment_instead_of_multiplying_them():
    """Two salary bands under one experience band is two searches, not four.

    Salary bands exist to make BOSS return *different* lists, so each is its
    own search. An experience requirement is a property every result should
    have, so it rides along on each.
    """
    segments = [{"salary": "402"}, {"salary": "403"}]
    out = with_experience(segments, "103")
    assert out == [
        {"salary": "402", "experience": "103"},
        {"salary": "403", "experience": "103"},
    ]


def test_experience_alone_still_produces_one_search():
    assert with_experience([], "103") == [{"experience": "103"}]


def test_no_experience_code_changes_nothing():
    segments = [{"salary": "402"}]
    assert with_experience(segments, None) == segments
    assert with_experience(segments, "  ") == segments
    assert with_experience([], None) == []


def test_a_pasted_urls_own_experience_choice_is_never_overwritten():
    """That set came from a URL the human built in their own browser; changing
    it would search for something other than what they pasted."""
    segments = [{"experience": "104,105"}, {"salary": "402"}]
    assert with_experience(segments, "103") == [
        {"experience": "104,105"},
        {"salary": "402", "experience": "103"},
    ]


def test_an_unreadable_experience_code_is_refused_before_anything_is_created():
    """The value ends up in a URL the extension will navigate to."""
    for bad in ("abc", "103;104", "103 104", "'", "10" * 9):
        with pytest.raises(ValidationError):
            with_experience([], bad)


def test_the_constraint_never_mutates_the_caller_s_segments():
    segments = [{"salary": "402"}]
    with_experience(segments, "103")
    assert segments == [{"salary": "402"}]
