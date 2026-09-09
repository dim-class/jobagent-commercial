"""A claimed application attempt always ends in a recorded outcome.

Approval #116 (2026-09-09) sat in ``executing`` forever. BOSS had opened the
conversation, so the click had happened; something threw between that and the
report, and a bare ``catch {}`` discarded two things at once - the reason, and
the claimed approval's outcome. The screen said only 「单岗位投递执行中断或后端
结果未确认」, which names no cause, and the row had to be closed by hand through
the queue's manual "结果未知" button.

Both halves are pinned here because both were wrong:

- once ``/begin`` has claimed the approval, *every* exit owes it an outcome,
  including an exit by exception. That needs ``claimed`` hoisted out of the
  ``try``: ``observed`` is block-scoped inside it, so the catch could not
  settle even if it had wanted to;
- the thrown reason must reach the user, with query strings stripped - BOSS
  puts ``lid`` and ``securityId`` there, and an error can carry a URL.

``unknown`` is the only honest outcome for an interrupted attempt, and
CLAUDE.md's rule holds: it is never upgraded to ``applied`` by assumption.

This is deliberately not in ``test_extension_m4a_contract.py`` - that file's
``background_source`` fixture slices the worker down to the M4a scope, so the
M6 code these assertions are about is not in it. That is exactly how the first
version of this test passed against the wrong ``catch`` block.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKGROUND_TS = ROOT / "extension" / "src" / "background.ts"
BACKGROUND_JS = ROOT / "extension" / "dist" / "background.js"


def _strip_comments(code: str) -> str:
    code = re.sub(r"/\*[\s\S]*?\*/", "", code)
    return re.sub(r"^\s*//.*$", "", code, flags=re.MULTILINE)


def _after_the_claim(code: str) -> str:
    """The M6 attempt's tail, from the moment the approval is claimed.

    `tsc` reformats the brace, so the catch is matched by the keyword rather
    than by the source's exact spelling - the built bundle is checked too, and
    a rule that only holds in TypeScript is not the one that runs.
    """
    body = _strip_comments(code)
    claim = body.index("/begin`")
    catch = body.index("catch (err) {", claim)
    return body[claim:catch + 1800]


@pytest.fixture(scope="session")
def source() -> str:
    return BACKGROUND_TS.read_text(encoding="utf-8")


def test_the_claim_records_what_a_later_catch_needs(source: str) -> None:
    assert "claimed = observed" in _after_the_claim(source)[:400]


def test_an_interrupted_attempt_is_settled_as_unknown(source: str) -> None:
    tail = _after_the_claim(source)
    assert "settleM6Outcome(claimedApproval, claimed, 'unknown'" in tail, (
        "a claimed approval is never left in `executing`"
    )
    assert "attempt_interrupted:" in tail


def test_an_interrupted_attempt_is_never_guessed_into_success(source: str) -> None:
    tail = _after_the_claim(source)
    catch = tail[tail.index("catch (err) {"):]
    assert "'applied'" not in catch
    assert "clicked_and_greeted" not in catch


def test_the_reason_reaches_the_user_without_a_query_string(source: str) -> None:
    catch = _after_the_claim(source)
    catch = catch[catch.index("catch (err) {"):]
    assert "String((err as Error)?.message" in catch, "the thrown reason is kept"
    assert r"replace(/\?[^\s]*/g, '')" in catch, "queries are stripped from it"
    assert "${why}" in catch, "and it reaches the message the user reads"


def test_the_built_bundle_carries_the_same_settlement() -> None:
    if not BACKGROUND_JS.exists():
        pytest.skip("extension/dist not built")
    tail = _after_the_claim(BACKGROUND_JS.read_text(encoding="utf-8"))
    assert "settleM6Outcome(claimedApproval, claimed, 'unknown'" in tail


def test_a_diagnostic_can_never_destroy_the_record_it_explains(source: str) -> None:
    """`detail` is truncated to the backend schema's own ceiling.

    On 2026-09-09 a richer greeting diagnostic pushed one detail to 414
    characters against a `max_length=256`. The POST came back 422
    「请求参数不合法」, the exception took the whole attempt down with it, and the
    approval was left in `executing` - a diagnostic destroying the record it
    exists to explain. A note that arrives shortened beats one that never
    arrives.
    """
    from app.schemas.application import ApplicationApprovalOutcomeRequest

    limit = ApplicationApprovalOutcomeRequest.model_fields["detail"].metadata
    schema_max = next(
        getattr(m, "max_length") for m in limit if hasattr(m, "max_length")
    )
    body = _strip_comments(source)
    assert f"const M6_DETAIL_MAX = {schema_max}" in body, (
        "the worker's cap and the schema's must be the same number"
    )
    assert "detail: detail.slice(0, M6_DETAIL_MAX)" in body, (
        "every settle truncates, so no caller can overrun it"
    )
