"""Every definition of a BOSS job id must accept the same characters.

The id lives in three character classes - the backend's extractor, the
extension's detection patterns, and the M6 worker's own re-check before it
acts - and on 2026-09-09 the third one was narrower than the other two. It
omitted ``~``, so a posting whose id ends in one was found, stored, analyzed,
queued and confirmed, and then refused at the last step with
「投递确认无效、已使用或招呼语模式不合法」 - a message naming three causes,
none of which was the real one.

Measured that day: 7 of 857 stored BOSS jobs carry a ``~``, and not one of them
could ever be applied to. The failure is invisible until someone tries the
exact job, which is why it survived: the other 850 worked.

``~`` is RFC 3986 unreserved and legal unescaped in a path. ``.`` is
deliberately excluded everywhere: the id is interpolated into a path, and
leaving it out makes ``..`` unrepresentable rather than merely unlikely.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.urls import boss_external_id

ROOT = Path(__file__).resolve().parents[2]

WORKER = ROOT / "extension" / "src" / "background.ts"
SELECTORS = ROOT / "extension" / "src" / "boss" / "selectors.ts"

#: Real ids read from the user's own library on 2026-09-09.
IDS_WITH_TILDE = [
    "36a4ae3730ed97581Xd90t21E1s~",
    "60d84fdd8c0502f433x92tW0F1E~",
    "6b98a1bc21d397fc3nx_2Nm6EVc~",
]


def _classes(path: Path) -> list[str]:
    """Every ``[...]`` character class that looks like an id alphabet."""
    text = path.read_text(encoding="utf-8")
    return re.findall(r"\[A-Za-z0-9[^\]]*\]", text)


def test_the_backend_extracts_an_id_that_ends_in_a_tilde() -> None:
    for external_id in IDS_WITH_TILDE:
        url = f"https://www.zhipin.com/job_detail/{external_id}.html"
        assert boss_external_id(url) == external_id


def test_the_worker_accepts_every_id_the_backend_stores() -> None:
    """The check that runs last must not be the strictest one."""
    match = re.search(r"const M6_EXTERNAL_ID = /(\^\[[^\]]+\]\+\$)/", WORKER.read_text(encoding="utf-8"))
    assert match, "M6_EXTERNAL_ID not found in the worker"
    pattern = re.compile(match.group(1))
    for external_id in IDS_WITH_TILDE:
        assert pattern.match(external_id), f"the M6 gate would refuse {external_id}"


def test_the_extension_detects_the_same_alphabet_it_will_act_on() -> None:
    """Detection and execution disagreeing is how this bug reached a user:
    the extension found these jobs perfectly well and then refused them."""
    for found in _classes(SELECTORS) + _classes(WORKER):
        assert "~" in found, f"{found} is narrower than the ids BOSS actually issues"
        assert "." not in found.replace("A-Za-z0-9", ""), (
            f"{found} allows a dot, which makes `..` representable in a path"
        )


def test_a_dot_is_still_refused_everywhere() -> None:
    assert boss_external_id("https://www.zhipin.com/job_detail/a..b.html") is None
