"""Deterministic compensation arithmetic (v0.9).

Pure functions over numbers. No database, no network, no OpenAI - which is what
lets every figure on the offer pages be recomputed and checked.

The distinctions this module exists to keep straight:

**Guaranteed vs target.** A target bonus is not money you have. Base plus a
*guaranteed* bonus plus one-time cash is what you are certain of; the target
number is a separate, clearly-labelled figure. Collapsing them into one
"package" is the single most common way offer comparisons mislead.

**First year vs steady state.** A signing bonus is paid once. Counting it in
every future year inflates the offer permanently, so it appears in the
first-year figures and is excluded from steady state.

**Equity is not cash.** A grant becomes a per-year number only when both a
value and a vesting period are known, and even then it is reported separately
and flagged as an estimate. An option grant is never treated as guaranteed
money; when it cannot be valued the answer is ``None``, never zero.

**Missing is not zero.** Every function returns ``None`` when its inputs are
absent, so "we don't know" never renders as "0".

Currency never enters the arithmetic: two amounts are only ever combined when
the caller has already established they share a currency. See
``services/offer_management.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable

#: Months of base pay per year when the offer does not say. Chinese offers
#: frequently pay 13-16; assuming 12 silently understates them, so the parser
#: reports what it found and the caller decides.
DEFAULT_MONTHS_PER_YEAR = 12


def _clean(value: float | int | Decimal | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # drop NaN


def _sum_present(values: Iterable[float | None]) -> float | None:
    """Sum the values that exist. ``None`` when *none* of them do.

    A present 0 is real ("guaranteed bonus: none") and counts; an absent field
    is unknown and must not be silently read as zero.
    """
    present = [v for v in (_clean(v) for v in values) if v is not None]
    return float(sum(present)) if present else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


# --------------------------------------------------------------------------
# annualization
# --------------------------------------------------------------------------


def annualize(
    monthly: float | None, months_per_year: int | None = None
) -> float | None:
    """Monthly base -> annual base. ``None`` in, ``None`` out."""
    amount = _clean(monthly)
    if amount is None:
        return None
    # `or` would treat an explicit 0 as "not stated" and silently use 12.
    months = DEFAULT_MONTHS_PER_YEAR if months_per_year is None else months_per_year
    if months <= 0:
        return None
    return _round(amount * months)


def resolve_base_annual(
    *,
    base_salary_annual: float | None,
    base_salary_monthly: float | None,
    months_per_year: int | None = None,
) -> float | None:
    """The annual base, however the user expressed it.

    An explicit annual figure always wins: if the user typed both, they meant
    the annual one, and deriving it from the monthly value could contradict
    what they entered.
    """
    explicit = _clean(base_salary_annual)
    if explicit is not None:
        return _round(explicit)
    return annualize(base_salary_monthly, months_per_year)


# --------------------------------------------------------------------------
# cash
# --------------------------------------------------------------------------


@dataclass(slots=True)
class CompensationInputs:
    """One revision's raw figures, already known to share a currency."""

    base_salary_annual: float | None = None
    base_salary_monthly: float | None = None
    months_per_year: int | None = None

    bonus_guaranteed: float | None = None
    bonus_target: float | None = None
    signing_bonus: float | None = None

    allowances_annual: float | None = None
    housing_value: float | None = None
    transport_value: float | None = None
    other_cash_annual: float | None = None

    stock_value: float | None = None
    stock_vesting_years: float | None = None

    @property
    def base_annual(self) -> float | None:
        return resolve_base_annual(
            base_salary_annual=self.base_salary_annual,
            base_salary_monthly=self.base_salary_monthly,
            months_per_year=self.months_per_year,
        )

    @property
    def recurring_extras(self) -> float | None:
        """Cash that repeats every year, beyond base and bonus."""
        return _sum_present(
            [
                self.allowances_annual,
                self.housing_value,
                self.transport_value,
                self.other_cash_annual,
            ]
        )


@dataclass(slots=True)
class CompensationBreakdown:
    """Everything derived, with the uncertain parts kept separate."""

    base_annual: float | None = None

    #: Base + guaranteed bonus + recurring extras + signing. Money you can
    #: count on in year one.
    first_year_guaranteed_cash: float | None = None
    #: Guaranteed plus the *target* bonus. Not money you have.
    first_year_target_cash: float | None = None
    #: Year two onward - the signing bonus is gone.
    steady_state_guaranteed_cash: float | None = None
    steady_state_target_cash: float | None = None

    #: Grant value spread over its vesting period. None when unvaluable.
    equity_annualized: float | None = None
    #: Target cash + annualized equity, when equity can be valued at all.
    estimated_first_year_total_comp: float | None = None
    #: True when a grant exists but could not be turned into a number.
    equity_excluded: bool = False
    #: Human-facing notes about what is missing or uncertain.
    notes: list[str] = field(default_factory=list)


#: Shown wherever equity exists but cannot be valued.
EQUITY_EXCLUDED_NOTE = "股权未计入可比较总包"


def compute_breakdown(inputs: CompensationInputs) -> CompensationBreakdown:
    """Derive every comparable figure from one revision's raw numbers."""
    base = inputs.base_annual
    extras = inputs.recurring_extras
    guaranteed_bonus = _clean(inputs.bonus_guaranteed)
    target_bonus = _clean(inputs.bonus_target)
    signing = _clean(inputs.signing_bonus)

    notes: list[str] = []

    recurring_guaranteed = _sum_present([base, guaranteed_bonus, extras])
    first_year_guaranteed = _sum_present([recurring_guaranteed, signing])

    # The target bonus sits on top of guaranteed cash - it is never a
    # replacement for it, and never folded in silently.
    first_year_target = (
        _sum_present([first_year_guaranteed, target_bonus])
        if target_bonus is not None
        else first_year_guaranteed
    )
    steady_target = (
        _sum_present([recurring_guaranteed, target_bonus])
        if target_bonus is not None
        else recurring_guaranteed
    )

    equity_annual, equity_excluded, equity_note = annualize_equity(
        stock_value=inputs.stock_value, vesting_years=inputs.stock_vesting_years
    )
    if equity_note:
        notes.append(equity_note)

    total_comp = (
        _sum_present([first_year_target, equity_annual])
        if equity_annual is not None
        else first_year_target
    )

    if base is None:
        notes.append("未填写基本工资，现金合计仅基于已填写的项目。")
    if target_bonus is not None and guaranteed_bonus is None:
        notes.append("目标奖金不等于保证到手的钱，已单独列出。")
    if signing is not None:
        notes.append("签字费为一次性收入，未计入第二年起的常态现金。")

    return CompensationBreakdown(
        base_annual=_round(base),
        first_year_guaranteed_cash=_round(first_year_guaranteed),
        first_year_target_cash=_round(first_year_target),
        steady_state_guaranteed_cash=_round(recurring_guaranteed),
        steady_state_target_cash=_round(steady_target),
        equity_annualized=_round(equity_annual),
        estimated_first_year_total_comp=_round(total_comp),
        equity_excluded=equity_excluded,
        notes=notes,
    )


def annualize_equity(
    *, stock_value: float | None, vesting_years: float | None
) -> tuple[float | None, bool, str | None]:
    """Spread a grant over its vesting period.

    Returns ``(annualized, excluded, note)``. A grant with no vesting period is
    **not** assumed to vest in one year - that would roughly quadruple a
    typical four-year grant. It is excluded and said so.
    """
    value = _clean(stock_value)
    if value is None:
        return (None, False, None)

    years = _clean(vesting_years)
    if years is None or years <= 0:
        return (None, True, f"{EQUITY_EXCLUDED_NOTE}：填写归属年限后才能折算。")

    return (value / years, False, None)


# --------------------------------------------------------------------------
# negotiation uplift
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Uplift:
    """Change between two company figures. ``None`` when either is missing."""

    absolute: float | None = None
    percentage: float | None = None
    initial: float | None = None
    final: float | None = None

    @property
    def known(self) -> bool:
        return self.absolute is not None


def uplift(initial: float | None, final: float | None) -> Uplift:
    """Absolute and percentage change.

    Returns an empty ``Uplift`` when either side is missing - a missing初始
    figure must not render as a 100% gain, and a zero base makes the percentage
    undefined rather than infinite.
    """
    start = _clean(initial)
    end = _clean(final)
    if start is None or end is None:
        return Uplift(initial=_round(start), final=_round(end))

    absolute = end - start
    percentage = None if start == 0 else absolute / start
    return Uplift(
        absolute=_round(absolute),
        percentage=None if percentage is None else round(percentage, 4),
        initial=_round(start),
        final=_round(end),
    )


# --------------------------------------------------------------------------
# offer text parsing
# --------------------------------------------------------------------------
#
# Deliberately stricter than ``scoring.extract_salary_range``, which is tuned
# for job descriptions where a rough range is good enough. An offer figure ends
# up in a comparison the user makes a real decision on, so this parser reports
# what it is sure of, reports a confidence, and never fills a gap with a guess.


@dataclass(slots=True)
class ParsedSalary:
    """What the parser could establish. Always subject to human confirmation."""

    base_salary_annual: float | None = None
    base_salary_monthly: float | None = None
    months_per_year: int | None = None
    currency: str | None = None
    #: high = one unambiguous reading; low = a guess worth showing but not trusting.
    confidence: str = "none"
    #: What matched, for the UI to explain itself.
    matched_text: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        return self.base_salary_annual is not None or self.base_salary_monthly is not None


_CURRENCY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("JPY", ("日元", "円", "jpy", "¥万円", "万円")),
    ("CNY", ("人民币", "元", "cny", "rmb", "￥")),
    ("USD", ("美元", "usd", "$")),
    ("EUR", ("欧元", "eur", "€")),
    ("HKD", ("港币", "hkd")),
    ("SGD", ("新币", "sgd")),
    ("GBP", ("英镑", "gbp")),
)

#: "14薪" / "16薪" / "13个月"
_MONTHS_RE = re.compile(r"(\d{2})\s*(?:薪|个月)")
#: "年薪30万" / "年收入 600 万"
_ANNUAL_WAN_RE = re.compile(r"年(?:薪|收入|包)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*万")
#: "600万日元" / "500万円"
_WAN_CURRENCY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*万\s*(?:日元|円|人民币|元)?")
#: "8M JPY" / "8.5m"
_MILLION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[mM]\b")
#: "20K/月" / "20k 月薪" / "月薪 25K"
_MONTHLY_K_RE = re.compile(
    r"(?:月薪\s*[:：]?\s*)?(\d{1,3}(?:\.\d+)?)\s*[kK]\s*(?:/\s*月|每月|月)?"
)
#: "25-35K" - a range, which for an offer is ambiguous, not a value.
_RANGE_RE = re.compile(r"(\d{1,3})\s*[kK]?\s*[-~～至]\s*(\d{1,3})\s*[kK]")


def detect_currency(text: str) -> str | None:
    lowered = (text or "").lower()
    for code, hints in _CURRENCY_HINTS:
        if any(hint in lowered for hint in hints):
            return code
    return None


def parse_offer_salary(text: str | None) -> ParsedSalary:
    """Read an offer's salary wording, conservatively.

    Never returns a figure it had to guess between two readings: a range like
    "25-35K" in an offer letter is reported as ambiguous with low confidence,
    because an offer is one number, not a band.
    """
    raw = (text or "").strip()
    if not raw:
        return ParsedSalary()

    currency = detect_currency(raw)
    months_match = _MONTHS_RE.search(raw)
    months = int(months_match.group(1)) if months_match else None
    if months is not None and not (12 <= months <= 20):
        months = None  # "20薪" is plausible; "99薪" is a false positive.

    notes: list[str] = []

    # A range is not an offer figure.
    range_match = _RANGE_RE.search(raw)
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        notes.append("识别到的是区间而不是确定数字，请填写实际 Offer 金额。")
        return ParsedSalary(
            base_salary_monthly=float(low) * 1000,
            months_per_year=months,
            currency=currency,
            confidence="low",
            matched_text=range_match.group(0),
            notes=notes + [f"区间 {low}K–{high}K，已按下限填入，请确认。"],
        )

    # "年薪30万"
    annual_wan = _ANNUAL_WAN_RE.search(raw)
    if annual_wan:
        return ParsedSalary(
            base_salary_annual=float(annual_wan.group(1)) * 10_000,
            months_per_year=months,
            currency=currency,
            confidence="high",
            matched_text=annual_wan.group(0),
            notes=notes,
        )

    # "8M JPY"
    million = _MILLION_RE.search(raw)
    if million:
        return ParsedSalary(
            base_salary_annual=float(million.group(1)) * 1_000_000,
            months_per_year=months,
            currency=currency,
            confidence="high" if currency else "low",
            matched_text=million.group(0),
            notes=notes if currency else notes + ["未能确定币种，请确认。"],
        )

    # "600万日元" - only when a currency word makes the unit unambiguous.
    wan = _WAN_CURRENCY_RE.search(raw)
    if wan and currency:
        return ParsedSalary(
            base_salary_annual=float(wan.group(1)) * 10_000,
            months_per_year=months,
            currency=currency,
            confidence="high",
            matched_text=wan.group(0),
            notes=notes,
        )

    # "20K/月"
    monthly = _MONTHLY_K_RE.search(raw)
    if monthly:
        # A stated 薪 count ("20K·14薪") is itself a statement that the figure
        # is monthly - nobody writes an annual salary as "14 of these".
        has_month_word = months is not None or any(
            token in raw for token in ("月薪", "/月", "每月", "月")
        )
        return ParsedSalary(
            base_salary_monthly=float(monthly.group(1)) * 1000,
            months_per_year=months,
            currency=currency,
            confidence="high" if has_month_word else "low",
            matched_text=monthly.group(0),
            notes=notes
            if has_month_word
            else notes + ["未能确定是月薪还是年薪，请确认。"],
        )

    return ParsedSalary(
        currency=currency,
        months_per_year=months,
        confidence="none",
        notes=["未能从文本中识别出薪资数字，请手动填写。"],
    )
