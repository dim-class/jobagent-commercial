"""Deterministic JD normalization and hashing."""

from __future__ import annotations

from app.services.hashing import job_content_hash
from app.services.job_normalizer import (
    normalize_city,
    normalize_job,
    normalize_whitespace_text,
)


def test_collapses_blank_lines_and_trims():
    raw = "  第一行  \n\n\n\n第二行\t\t结尾   \n\n\n"
    assert normalize_whitespace_text(raw) == "第一行\n\n第二行 结尾"


def test_unifies_symbol_bullets_but_keeps_numbering():
    raw = "• 第一条\n● 第二条\n- 第三条\n1. 第四条\n2、第五条"
    assert normalize_whitespace_text(raw) == "- 第一条\n- 第二条\n- 第三条\n1. 第四条\n2、第五条"


def test_keeps_cjk_punctuation_untouched():
    text = "负责（云平台）：运维，稳定。"
    assert normalize_whitespace_text(text) == text


def test_folds_look_alike_glyphs():
    # curly quotes, em dash, non-breaking space
    assert normalize_whitespace_text("“云”—原生 平台") == '"云"-原生 平台'


def test_preserves_jd_content():
    raw = "负责 AWS 云平台运维\n熟悉 Kubernetes 与 Terraform"
    out = normalize_whitespace_text(raw)
    for token in ("AWS", "Kubernetes", "Terraform"):
        assert token in out


def test_normalize_city_strips_suffix_and_district():
    assert normalize_city("北京市") == "北京"
    assert normalize_city("上海·浦东新区") == "上海"
    assert normalize_city("  杭州 ") == "杭州"
    assert normalize_city("beijing") == "北京"
    assert normalize_city("") is None
    assert normalize_city(None) is None


def test_raw_description_is_never_destroyed():
    raw = "行一\r\n\r\n\r\n行二   "
    job = normalize_job(company="A公司", title="工程师", raw_description=raw)
    assert job.raw_description == raw
    assert job.normalized_description == "行一\n\n行二"


def test_content_hash_is_whitespace_insensitive():
    a = normalize_job(company="A公司", title="云工程师", raw_description="负责  云平台\n\n\n运维")
    b = normalize_job(company="A公司", title="云工程师", raw_description="负责 云平台\n运维")
    assert a.content_hash == b.content_hash


def test_content_hash_differs_by_company():
    a = normalize_job(company="A公司", title="云工程师", raw_description="同样的 JD")
    b = normalize_job(company="B公司", title="云工程师", raw_description="同样的 JD")
    assert a.content_hash != b.content_hash


def test_content_hash_helper_matches_normalizer():
    job = normalize_job(company="A公司", title="云工程师", raw_description="负责云平台运维")
    assert job.content_hash == job_content_hash(
        company="A公司", title="云工程师", normalized_description=job.normalized_description
    )
