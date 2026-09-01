"""The AI direction analysis: the gate around it, and how it is blended in.

The point of this feature is that a model reads the résumé and judges which
BOSS keywords it actually supports. The point of these tests is that doing so
never happens by accident, never happens twice for the same question, and never
outranks real outcome evidence.
"""

from __future__ import annotations

import pytest

from app.core.career_strategy import load_strategy
from app.schemas.direction import DirectionFit, ResumeDirectionAnalysis
from app.services import direction_analysis, search_direction_ranking
from app.services.direction_analysis import NotConfirmedError


def _analysis(**fits: int) -> ResumeDirectionAnalysis:
    return ResumeDirectionAnalysis(
        directions=[
            DirectionFit(keyword=k, fit=v, reason="简历里写过相关经历") for k, v in fits.items()
        ],
        summary="偏基础设施方向",
    )


# --------------------------------------------------------------------------
# the money gate
# --------------------------------------------------------------------------


def test_reading_the_plan_never_calls_a_model(client, active_resume, monkeypatch):
    """The console loads this on open, so it must be free."""

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("planning must not call the model")

    monkeypatch.setattr("app.services.direction_analysis.run_direction_analysis", explode)

    body = client.get("/api/tasks/search-plan/direction-plan").json()
    assert body["pending_calls"] == 1, "uncached, so it would cost exactly one call"
    assert body["cached"] is False
    assert body["directions"] == []


def test_running_without_confirmation_spends_nothing(db, active_resume, monkeypatch):
    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("an unconfirmed run must not call the model")

    monkeypatch.setattr("app.services.direction_analysis.run_direction_analysis", explode)

    with pytest.raises(NotConfirmedError):
        direction_analysis.run(db, confirmed=False)


def test_one_call_covers_the_whole_resume_and_repeats_are_free(db, active_resume, monkeypatch):
    """The cost is one call, not one per direction - and zero the second time."""
    calls = {"n": 0}

    async def fake(*, candidates, **kwargs):
        calls["n"] += 1
        return _analysis(**{candidates[0]: 90})

    monkeypatch.setattr("app.services.direction_analysis.run_direction_analysis", fake)

    first = direction_analysis.run(db, confirmed=True)
    assert calls["n"] == 1
    assert len(first.candidates) > 1, "several directions, still one call"
    assert first.cached is True and first.pending_calls == 0

    direction_analysis.run(db, confirmed=True)
    assert calls["n"] == 1, "a cached answer must not be re-billed"


def test_a_changed_resume_invalidates_the_cached_verdict(db, active_resume, monkeypatch):
    """The verdict is about this résumé; a different one is a different question."""
    calls = {"n": 0}

    async def fake(*, candidates, **kwargs):
        calls["n"] += 1
        return _analysis(**{candidates[0]: 90})

    monkeypatch.setattr("app.services.direction_analysis.run_direction_analysis", fake)

    direction_analysis.run(db, confirmed=True)
    assert direction_analysis.plan(db).cached is True

    active_resume.content_hash = "0" * 64
    db.commit()
    assert direction_analysis.plan(db).cached is False, "a new résumé re-analyses"


# --------------------------------------------------------------------------
# how the judgement is used
# --------------------------------------------------------------------------


def test_ai_fit_replaces_the_character_overlap_and_is_labelled(db, active_resume):
    strategy = load_strategy()
    roles = [r for r in strategy["preferred_roles"]]

    ranking = search_direction_ranking.rank(
        db, resume=active_resume, strategy=strategy, ai=_analysis(**{roles[-1]: 95, roles[0]: 5})
    )
    by_keyword = {d.keyword: d for d in ranking.directions}

    judged = by_keyword[roles[-1]]
    assert judged.fit == 0.95 and judged.fit_source == "ai"
    assert any("AI 判断" in r for r in judged.reasons)
    # A direction the model skipped has no comparable measurement, so none is
    # invented: it is marked unjudged rather than being scored by character
    # overlap, which could otherwise beat an actual 95/100 judgement.
    unjudged = next(d for d in ranking.directions if d.keyword not in {roles[0], roles[-1]})
    assert unjudged.fit_source == "unjudged" and unjudged.fit == 0.0
    assert any("没有可比较的匹配度" in r for r in unjudged.reasons)

    assert ranking.directions[0].keyword == roles[-1], "the 95 outranks the 5"


def test_a_suggested_keyword_is_usable_but_never_written_to_the_strategy(db, active_resume):
    strategy = load_strategy()
    before = list(strategy["preferred_roles"])
    ai = _analysis(**{before[0]: 40})
    ai.suggested = [DirectionFit(keyword="基础设施工程师", fit=92, reason="简历写着基础设施工程师")]

    ranking = search_direction_ranking.rank(db, resume=active_resume, strategy=strategy, ai=ai)
    suggested = next(d for d in ranking.directions if d.keyword == "基础设施工程师")
    assert suggested.suggested is True
    assert ranking.directions[0].keyword == "基础设施工程师", "usable for this search"
    assert any("不会自动写进职业策略" in n for n in ranking.notes)

    assert load_strategy(force=True)["preferred_roles"] == before, "strategy untouched"


def test_ranking_itself_never_fetches_an_analysis(db, active_resume, monkeypatch):
    """`rank` takes the AI view as an argument; paying for one is the caller's
    confirmed decision, so ranking stays free."""

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("ranking must not reach the model")

    monkeypatch.setattr("app.services.direction_analysis.run_direction_analysis", explode)
    ranking = search_direction_ranking.rank(db, resume=active_resume, strategy=load_strategy())
    assert ranking.directions
    assert all(d.fit_source == "text" for d in ranking.directions)


def test_generic_role_suffixes_no_longer_dominate_the_fallback_fit(db, active_resume):
    """`工程师` is shared by every Chinese engineering title.

    Left in, it gave 云计算工程师 / 云运维工程师 / 云平台工程师 an identical 33%
    on a real résumé - a number that looked like a measurement and was really
    just the suffix.
    """
    vocabulary = search_direction_ranking._resume_vocabulary(active_resume, load_strategy())
    fits = {
        role: search_direction_ranking._text_fit(role, vocabulary)
        for role in ("云计算工程师", "云运维工程师", "云平台工程师")
    }
    assert len(set(fits.values())) > 1, f"still indistinguishable: {fits}"
