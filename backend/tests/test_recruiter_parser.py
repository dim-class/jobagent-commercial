"""Deterministic recruiter-text handling: speaker splitting and keyword signals.

Every sample here is synthetic, written for this repository. No test contacts a
recruitment site, an inbox, or OpenAI.
"""

from __future__ import annotations

import pytest

from app.models import MessageDirection
from app.schemas.recruiter import InputMode, RequestType
from app.services.recruiter_parser import (
    analyze_signals,
    detect_language,
    detect_signals,
    has_question,
    split_conversation,
)

BOSS_SINGLE = "您好，想确认您的期望薪资和最快到岗时间。另外下周三下午方便面试吗？"

TRANSCRIPT = """HR：您好，看到您的简历，想了解一下您的期望薪资。
我：您好，感谢联系。我的期望薪资是 30-40K。
HR：好的。那下周二或周三方便安排线上面试吗？"""

JAPANESE = "お世話になっております。希望年収と入社可能日を教えていただけますか。来週水曜の面接は可能でしょうか。"
ENGLISH = "Hi, thanks for applying. Could you share your expected salary and availability for an interview next week?"


# --------------------------------------------------------------------------
# language detection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (BOSS_SINGLE, "zh"),
        (JAPANESE, "ja"),
        (ENGLISH, "en"),
        ("面接の日程を調整させてください", "ja"),
        ("请问您的期望薪资是多少", "zh"),
        ("", "en"),
    ],
)
def test_detect_language(text, expected):
    assert detect_language(text) == expected


def test_japanese_wins_over_kanji():
    """Kana are decisive; shared kanji alone must not be read as Chinese."""
    assert detect_language("面接の候補日をご連絡ください") == "ja"


# --------------------------------------------------------------------------
# keyword signals
# --------------------------------------------------------------------------


def test_detect_salary_and_interview_signals():
    hits = detect_signals(BOSS_SINGLE)
    assert RequestType.expected_salary.value in hits
    assert RequestType.interview_availability.value in hits
    assert RequestType.start_date.value in hits


@pytest.mark.parametrize(
    ("text", "expected_type"),
    [
        ("请把简历发我一份", RequestType.resume),
        ("履歴書を送ってください", RequestType.resume),
        ("Could you send your CV?", RequestType.resume),
        ("您目前的签证状态是什么", RequestType.visa_status),
        ("Do you need visa sponsorship?", RequestType.visa_status),
        ("最快什么时候可以到岗", RequestType.start_date),
        ("希望年収はいくらですか", RequestType.expected_salary),
        ("expected salary range?", RequestType.expected_salary),
        ("是否接受远程办公", RequestType.remote_preference),
        ("您的日语是什么水平", RequestType.language_skill),
    ],
)
def test_signal_types_across_languages(text, expected_type):
    assert expected_type.value in detect_signals(text)


def test_no_signals_for_small_talk():
    assert detect_signals("好的，谢谢您，祝您周末愉快。") == {}


@pytest.mark.parametrize(
    ("text", "expected"),
    [("方便面试吗？", True), ("ご都合はいかがですか", True), ("好的，收到。", False)],
)
def test_has_question(text, expected):
    assert has_question(text) is expected


def test_analyze_signals_is_serialisable():
    data = analyze_signals(BOSS_SINGLE).to_dict()
    assert data["language"] == "zh"
    assert data["has_question"] is True
    assert data["char_count"] > 0
    assert isinstance(data["request_types"], list)


# --------------------------------------------------------------------------
# speaker splitting
# --------------------------------------------------------------------------


def test_single_message_stays_whole():
    result = split_conversation(BOSS_SINGLE)
    assert len(result.messages) == 1
    assert result.mode_used is InputMode.single_message
    assert result.messages[0].direction is MessageDirection.recruiter
    assert result.messages[0].text == BOSS_SINGLE


def test_transcript_is_split_by_speaker():
    result = split_conversation(TRANSCRIPT)
    assert result.mode_used is InputMode.conversation
    assert len(result.messages) == 3

    directions = [m.direction for m in result.messages]
    assert directions == [
        MessageDirection.recruiter,
        MessageDirection.user,
        MessageDirection.recruiter,
    ]
    assert "期望薪资" in result.messages[0].text
    assert "30-40K" in result.messages[1].text


def test_forced_single_mode_never_splits():
    result = split_conversation(TRANSCRIPT, mode=InputMode.single_message)
    assert len(result.messages) == 1
    assert "HR：" in result.messages[0].text


def test_multiline_turns_stay_together():
    text = "HR：您好。\n我们这边有个云平台岗位。\n我：好的，麻烦发一下 JD。"
    result = split_conversation(text)
    assert len(result.messages) == 2
    assert "云平台岗位" in result.messages[0].text


def test_unlabelled_lead_is_kept_and_flagged():
    """Text before the first speaker label must not be silently dropped."""
    text = "（来自BOSS直聘）\nHR：您好，方便沟通吗？\n我：可以的。"
    result = split_conversation(text)

    assert any("没有标注发言人" in w for w in result.warnings)
    assert result.messages[0].text == "（来自BOSS直聘）"
    assert result.messages[0].direction is MessageDirection.recruiter


def test_bare_timestamp_lines_are_dropped():
    text = "HR：您好\n10:31\n我：您好\n2026-08-20 10:35\nHR：请问期望薪资？"
    result = split_conversation(text)
    assert len(result.messages) == 3
    assert all("10:31" not in m.text for m in result.messages)


def test_english_and_japanese_labels_are_recognised():
    text = "Recruiter: Are you available next week?\nMe: Yes, Tuesday works."
    result = split_conversation(text)
    assert [m.direction for m in result.messages] == [
        MessageDirection.recruiter,
        MessageDirection.user,
    ]

    ja = "採用担当：面接の日程を調整したいです。\n私：来週の水曜日でお願いします。"
    result_ja = split_conversation(ja)
    assert [m.direction for m in result_ja.messages] == [
        MessageDirection.recruiter,
        MessageDirection.user,
    ]


def test_unknown_labels_do_not_force_a_split():
    """A colon in ordinary prose must not be read as a speaker label."""
    text = "您好：我们公司的福利如下：五险一金、弹性工作。"
    result = split_conversation(text)
    assert len(result.messages) == 1


def test_empty_input_is_reported_not_crashed():
    result = split_conversation("   ")
    assert result.messages == []
    assert result.warnings
