"""Deterministic reading of recruiter text, before any AI call (v0.5).

Two jobs:

1. **Split** a pasted conversation into speaker-tagged messages. Chat exports
   vary wildly, so this is best-effort: when the shape is not obvious we keep
   the text whole and say so, rather than mangling it.
2. **Flag** obvious recruiter asks by keyword (salary / interview / resume /
   visa / start date) in zh, ja and en.

The flags are *features*, not a replacement for semantic analysis - they let the
UI show something instantly and give the model a hint, but the agent decides.

Nothing here calls a network or an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.enums import MessageDirection
from app.schemas.recruiter import InputMode, RequestType

MAX_TEXT_CHARS = 20_000

# --------------------------------------------------------------------------
# speaker splitting
# --------------------------------------------------------------------------

#: Labels that mean "the recruiter said this".
_RECRUITER_LABELS = (
    "hr", "HR", "招聘者", "招聘方", "对方", "面试官", "猎头", "顾问",
    "採用担当", "担当者", "リクルーター", "先方",
    "recruiter", "interviewer",
)
#: Labels that mean "I said this".
_USER_LABELS = (
    "我", "本人", "候选人", "自己",
    "私", "自分",
    "me", "myself", "candidate", "you",
)

# Long enough for "Recruiter" / "採用担当者". The length is not what makes this
# safe - _classify_label is: an unrecognised label never starts a new turn, so
# an ordinary sentence containing a colon is left alone.
_LABEL_RE = re.compile(
    r"^\s*(?P<label>[A-Za-z一-鿿぀-ヿ]{1,12})\s*[:：]\s*(?P<body>.*)$"
)

#: A leading timestamp some exports put on its own line, e.g. "2026-08-20 10:31".
_TIMESTAMP_LINE_RE = re.compile(
    r"^\s*(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)?\s*\d{1,2}[:：]\d{2}(?::\d{2})?\s*$"
)


@dataclass(slots=True)
class ParsedMessage:
    direction: MessageDirection
    text: str


@dataclass(slots=True)
class ParseResult:
    messages: list[ParsedMessage]
    mode_used: InputMode
    warnings: list[str] = field(default_factory=list)


def _classify_label(label: str) -> MessageDirection | None:
    lowered = label.strip().lower()
    if any(lowered == item.lower() or item.lower() in lowered for item in _USER_LABELS):
        return MessageDirection.user
    if any(lowered == item.lower() or item.lower() in lowered for item in _RECRUITER_LABELS):
        return MessageDirection.recruiter
    return None


def split_conversation(
    text: str,
    *,
    mode: InputMode = InputMode.auto,
    default_direction: MessageDirection = MessageDirection.recruiter,
) -> ParseResult:
    """Split pasted text into messages.

    ``auto`` only splits when at least two labelled turns are found; otherwise
    the whole paste stays one message. Imperfect speaker detection is expected
    and is surfaced as a warning instead of being hidden.
    """
    raw = (text or "").strip()[:MAX_TEXT_CHARS]
    if not raw:
        return ParseResult(messages=[], mode_used=mode, warnings=["没有检测到消息内容"])

    if mode is InputMode.single_message:
        return ParseResult(
            messages=[ParsedMessage(direction=default_direction, text=raw)],
            mode_used=InputMode.single_message,
        )

    turns: list[ParsedMessage] = []
    current: ParsedMessage | None = None
    unlabelled_lead: list[str] = []
    labelled_turns = 0

    for line in raw.split("\n"):
        if _TIMESTAMP_LINE_RE.match(line):
            continue  # a bare timestamp row carries no content

        match = _LABEL_RE.match(line)
        direction = _classify_label(match.group("label")) if match else None

        if direction is not None:
            labelled_turns += 1
            body = match.group("body").strip()
            current = ParsedMessage(direction=direction, text=body)
            turns.append(current)
            continue

        if current is None:
            unlabelled_lead.append(line)
        else:
            current.text = f"{current.text}\n{line}".strip()

    warnings: list[str] = []
    lead_text = "\n".join(unlabelled_lead).strip()

    if labelled_turns < 2 and mode is InputMode.auto:
        # Not obviously a transcript - keep it whole rather than guessing.
        return ParseResult(
            messages=[ParsedMessage(direction=default_direction, text=raw)],
            mode_used=InputMode.single_message,
        )

    if lead_text:
        # Text before the first label: attribute it to the default speaker and
        # say so, instead of silently dropping or misfiling it.
        turns.insert(0, ParsedMessage(direction=default_direction, text=lead_text))
        warnings.append("开头有一段没有标注发言人的内容，已按招聘方消息处理，请确认")

    cleaned = [m for m in turns if m.text.strip()]
    if not cleaned:
        return ParseResult(
            messages=[ParsedMessage(direction=default_direction, text=raw)],
            mode_used=InputMode.single_message,
            warnings=["未能识别发言人，已保留原文"],
        )

    if labelled_turns and len(cleaned) != labelled_turns + (1 if lead_text else 0):
        warnings.append("发言人识别可能不完整，请核对每条消息的归属")

    return ParseResult(messages=cleaned, mode_used=InputMode.conversation, warnings=warnings)


# --------------------------------------------------------------------------
# deterministic request signals
# --------------------------------------------------------------------------

#: keyword -> request type. Multilingual because recruiters write in zh/ja/en.
SIGNAL_KEYWORDS: tuple[tuple[RequestType, tuple[str, ...]], ...] = (
    (
        RequestType.expected_salary,
        ("期望薪资", "期望年薪", "期望薪水", "薪资要求", "希望年収", "希望給与",
         "expected salary", "salary expectation", "compensation expectation"),
    ),
    (
        RequestType.current_salary,
        ("目前薪资", "当前薪资", "现在的薪资", "現年収", "current salary"),
    ),
    (
        RequestType.interview_availability,
        ("面试", "面談", "面接", "方便的时间", "时间方便", "安排时间",
         "interview", "availability", "available time", "schedule a call"),
    ),
    (
        RequestType.resume,
        ("简历", "履历", "履歴書", "職務経歴書", "resume", "cv"),
    ),
    (
        RequestType.visa_status,
        ("签证", "在留", "工作签", "visa", "sponsorship", "work permit"),
    ),
    (
        RequestType.start_date,
        ("到岗", "入职", "入社", "最快什么时候", "start date", "notice period",
         "when can you start"),
    ),
    (
        RequestType.notice_period,
        ("离职周期", "notice period", "退職まで"),
    ),
    (
        RequestType.work_location,
        ("工作地点", "办公地点", "勤務地", "work location", "onsite"),
    ),
    (
        RequestType.remote_preference,
        ("远程", "在宅", "リモート", "remote", "hybrid"),
    ),
    (
        RequestType.language_skill,
        ("日语", "英语", "日本語", "英語", "japanese level", "english level", "n1", "n2"),
    ),
    (
        RequestType.certification,
        ("证书", "认证", "資格", "certification", "certified"),
    ),
    (
        RequestType.years_of_experience,
        ("几年经验", "工作年限", "経験年数", "years of experience"),
    ),
)

#: Rough language detection for the reply. Deterministic and cheap.
_JA_ONLY = re.compile(r"[぀-ゟ゠-ヿ]")
_CJK = re.compile(r"[一-鿿]")


def detect_language(text: str) -> str:
    """``ja`` | ``zh`` | ``en`` - what the recruiter appears to be writing.

    Japanese is checked first: kana are decisive, whereas kanji alone are not.
    """
    sample = (text or "")[:2000]
    if _JA_ONLY.search(sample):
        return "ja"
    if _CJK.search(sample):
        return "zh"
    return "en"


def detect_signals(text: str) -> dict[str, list[str]]:
    """Keyword hits per request type. Evidence only - the agent decides."""
    lowered = (text or "").lower()
    hits: dict[str, list[str]] = {}
    for request_type, keywords in SIGNAL_KEYWORDS:
        found = [kw for kw in keywords if kw.lower() in lowered]
        if found:
            hits[request_type.value] = found
    return hits


def has_question(text: str) -> bool:
    return any(mark in (text or "") for mark in ("?", "？", "吗", "呢", "ますか", "ですか"))


@dataclass(slots=True)
class DeterministicSignals:
    language: str
    request_types: list[str]
    keyword_hits: dict[str, list[str]]
    has_question: bool
    char_count: int

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "request_types": self.request_types,
            "keyword_hits": self.keyword_hits,
            "has_question": self.has_question,
            "char_count": self.char_count,
        }


def analyze_signals(text: str) -> DeterministicSignals:
    hits = detect_signals(text)
    return DeterministicSignals(
        language=detect_language(text),
        request_types=sorted(hits),
        keyword_hits=hits,
        has_question=has_question(text),
        char_count=len(text or ""),
    )
