"""Deterministic compensation arithmetic (v0.9).

Pure logic - no database, no network, no OpenAI.

The tests that matter most are the ones about what the calculator *refuses* to
do: treat a target bonus as money, repeat a signing bonus forever, value an
option grant with no vesting period, or read a missing field as zero.
"""

from __future__ import annotations

import pytest

from app.services.offer_calculator import (
    DEFAULT_MONTHS_PER_YEAR,
    CompensationInputs,
    annualize,
    annualize_equity,
    compute_breakdown,
    detect_currency,
    parse_offer_salary,
    resolve_base_annual,
    uplift,
)


def inputs(**kwargs) -> CompensationInputs:
    return CompensationInputs(**kwargs)


# --------------------------------------------------------------------------
# annualization
# --------------------------------------------------------------------------


def test_monthly_becomes_annual_at_twelve_by_default():
    assert annualize(20_000) == 240_000
    assert DEFAULT_MONTHS_PER_YEAR == 12


def test_a_stated_month_count_is_used():
    """14薪 / 16薪 are ordinary in Chinese offers."""
    assert annualize(20_000, 14) == 280_000
    assert annualize(20_000, 16) == 320_000


def test_missing_monthly_yields_none_not_zero():
    assert annualize(None) is None
    assert annualize(None, 14) is None


def test_a_nonsense_month_count_yields_none():
    assert annualize(20_000, 0) is None


def test_an_explicit_annual_figure_wins_over_the_monthly_one():
    """If the user typed both, the annual number is what they meant."""
    result = resolve_base_annual(
        base_salary_annual=350_000, base_salary_monthly=20_000, months_per_year=14
    )
    assert result == 350_000


def test_annual_is_derived_when_only_monthly_is_given():
    assert (
        resolve_base_annual(
            base_salary_annual=None, base_salary_monthly=20_000, months_per_year=14
        )
        == 280_000
    )


# --------------------------------------------------------------------------
# guaranteed vs target - the spec's worked example
# --------------------------------------------------------------------------


def test_the_spec_example():
    """Base 300K, guaranteed 0, target 60K, signing 20K."""
    breakdown = compute_breakdown(
        inputs(
            base_salary_annual=300_000,
            bonus_guaranteed=0,
            bonus_target=60_000,
            signing_bonus=20_000,
        )
    )
    assert breakdown.first_year_guaranteed_cash == 320_000
    assert breakdown.first_year_target_cash == 380_000


def test_a_target_bonus_is_never_counted_as_guaranteed():
    breakdown = compute_breakdown(
        inputs(base_salary_annual=300_000, bonus_target=100_000)
    )
    assert breakdown.first_year_guaranteed_cash == 300_000
    assert breakdown.first_year_target_cash == 400_000
    assert breakdown.first_year_guaranteed_cash != breakdown.first_year_target_cash


def test_a_guaranteed_bonus_does_count(db=None):
    breakdown = compute_breakdown(
        inputs(base_salary_annual=300_000, bonus_guaranteed=30_000)
    )
    assert breakdown.first_year_guaranteed_cash == 330_000


def test_recurring_extras_are_included_in_guaranteed_cash():
    breakdown = compute_breakdown(
        inputs(
            base_salary_annual=300_000,
            allowances_annual=12_000,
            housing_value=24_000,
            transport_value=6_000,
        )
    )
    assert breakdown.first_year_guaranteed_cash == 342_000
    assert breakdown.steady_state_guaranteed_cash == 342_000


def test_an_unvalued_benefit_is_not_cash():
    """"Company housing available" is not money until the user says what it is worth."""
    breakdown = compute_breakdown(inputs(base_salary_annual=300_000, housing_value=None))
    assert breakdown.first_year_guaranteed_cash == 300_000


# --------------------------------------------------------------------------
# first year vs steady state
# --------------------------------------------------------------------------


def test_a_signing_bonus_counts_only_in_the_first_year():
    breakdown = compute_breakdown(
        inputs(base_salary_annual=300_000, signing_bonus=50_000)
    )
    assert breakdown.first_year_guaranteed_cash == 350_000
    assert breakdown.steady_state_guaranteed_cash == 300_000


def test_steady_state_still_includes_the_target_bonus():
    breakdown = compute_breakdown(
        inputs(base_salary_annual=300_000, bonus_target=60_000, signing_bonus=20_000)
    )
    assert breakdown.steady_state_target_cash == 360_000
    assert breakdown.first_year_target_cash == 380_000


def test_a_signing_bonus_produces_a_note():
    breakdown = compute_breakdown(
        inputs(base_salary_annual=300_000, signing_bonus=20_000)
    )
    assert any("一次性" in note for note in breakdown.notes)


# --------------------------------------------------------------------------
# equity
# --------------------------------------------------------------------------


def test_a_grant_with_a_vesting_period_is_spread_over_it():
    value, excluded, note = annualize_equity(stock_value=400_000, vesting_years=4)
    assert value == 100_000
    assert excluded is False
    assert note is None


def test_a_grant_with_no_vesting_period_is_excluded_not_guessed():
    """Assuming one-year vesting would roughly quadruple a typical grant."""
    value, excluded, note = annualize_equity(stock_value=400_000, vesting_years=None)
    assert value is None
    assert excluded is True
    assert "未计入可比较总包" in note


def test_no_grant_at_all_is_not_an_exclusion():
    value, excluded, note = annualize_equity(stock_value=None, vesting_years=None)
    assert (value, excluded, note) == (None, False, None)


def test_equity_is_never_folded_into_guaranteed_cash():
    breakdown = compute_breakdown(
        inputs(base_salary_annual=300_000, stock_value=400_000, stock_vesting_years=4)
    )
    assert breakdown.first_year_guaranteed_cash == 300_000, "equity is not cash"
    assert breakdown.equity_annualized == 100_000
    assert breakdown.estimated_first_year_total_comp == 400_000


def test_unvaluable_equity_leaves_total_comp_as_cash_only(db=None):
    breakdown = compute_breakdown(
        inputs(base_salary_annual=300_000, stock_value=400_000, stock_vesting_years=None)
    )
    assert breakdown.equity_excluded is True
    assert breakdown.equity_annualized is None
    assert breakdown.estimated_first_year_total_comp == 300_000


# --------------------------------------------------------------------------
# missing is not zero
# --------------------------------------------------------------------------


def test_an_entirely_empty_revision_yields_nulls():
    breakdown = compute_breakdown(inputs())
    assert breakdown.base_annual is None
    assert breakdown.first_year_guaranteed_cash is None
    assert breakdown.first_year_target_cash is None
    assert breakdown.estimated_first_year_total_comp is None


def test_a_present_zero_is_real():
    """"Guaranteed bonus: none" is information; a missing field is not."""
    breakdown = compute_breakdown(
        inputs(base_salary_annual=300_000, bonus_guaranteed=0)
    )
    assert breakdown.first_year_guaranteed_cash == 300_000


def test_a_missing_base_is_reported_rather_than_assumed():
    breakdown = compute_breakdown(inputs(bonus_target=50_000))
    assert breakdown.base_annual is None
    assert any("未填写基本工资" in note for note in breakdown.notes)


# --------------------------------------------------------------------------
# uplift
# --------------------------------------------------------------------------


def test_uplift_absolute_and_percentage():
    result = uplift(300_000, 330_000)
    assert result.absolute == 30_000
    assert result.percentage == pytest.approx(0.1)
    assert result.known is True


def test_a_missing_side_yields_no_uplift_rather_than_a_fake_one():
    assert uplift(None, 330_000).absolute is None
    assert uplift(300_000, None).absolute is None
    assert uplift(None, None).known is False


def test_a_zero_start_has_no_defined_percentage():
    result = uplift(0, 30_000)
    assert result.absolute == 30_000
    assert result.percentage is None, "dividing by zero is undefined, not infinite"


def test_a_downward_revision_is_reported_honestly():
    result = uplift(330_000, 300_000)
    assert result.absolute == -30_000
    assert result.percentage < 0


# --------------------------------------------------------------------------
# offer text parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_annual"),
    [
        ("年薪30万", 300_000),
        ("年收入 45 万", 450_000),
        ("8M JPY", 8_000_000),
        ("600万日元", 6_000_000),
    ],
)
def test_annual_forms_parse(text, expected_annual):
    parsed = parse_offer_salary(text)
    assert parsed.base_salary_annual == expected_annual
    assert parsed.confidence == "high"


def test_monthly_k_form_parses():
    parsed = parse_offer_salary("20K/月")
    assert parsed.base_salary_monthly == 20_000
    assert parsed.base_salary_annual is None
    assert parsed.confidence == "high"


def test_a_month_count_implies_a_monthly_figure():
    """"20K·14薪" - nobody writes an annual salary as "14 of these"."""
    parsed = parse_offer_salary("20K·14薪")
    assert parsed.base_salary_monthly == 20_000
    assert parsed.months_per_year == 14
    assert parsed.confidence == "high"
    assert annualize(parsed.base_salary_monthly, parsed.months_per_year) == 280_000


def test_a_range_is_flagged_rather_than_picked():
    """An offer is one number. A band means the parser is not sure."""
    parsed = parse_offer_salary("25-35K·16薪")
    assert parsed.confidence == "low"
    assert parsed.months_per_year == 16
    assert any("区间" in note for note in parsed.notes)


def test_unparseable_text_says_so(db=None):
    parsed = parse_offer_salary("公司提供宿舍和班车")
    assert parsed.is_usable is False
    assert parsed.confidence == "none"
    assert any("请手动填写" in note for note in parsed.notes)


def test_empty_text_is_handled():
    assert parse_offer_salary(None).confidence == "none"
    assert parse_offer_salary("").confidence == "none"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("600万日元", "JPY"),
        ("年薪 30 万元", "CNY"),
        ("120000 USD", "USD"),
        ("something", None),
    ],
)
def test_currency_detection(text, expected):
    assert detect_currency(text) == expected


def test_a_million_form_without_a_currency_is_low_confidence():
    parsed = parse_offer_salary("8M")
    assert parsed.base_salary_annual == 8_000_000
    assert parsed.confidence == "low"
    assert any("币种" in note for note in parsed.notes)


def test_the_parser_never_invents_a_bonus_or_equity():
    """It reads a base figure and nothing else - the rest is the user's to enter."""
    parsed = parse_offer_salary("年薪30万，另有年终奖和股票")
    assert parsed.base_salary_annual == 300_000
    assert not hasattr(parsed, "bonus_target")
