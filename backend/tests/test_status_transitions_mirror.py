"""The frontend copy of the transition table must not drift from the backend.

`frontend/src/pages/statusTransitions.ts` exists so the job list can grey out a
status button whose only possible outcome is a 422. That is a UI convenience,
not a second authority - but a stale copy is worse than none, because it would
hide a button that does work or offer one that cannot. Hence this test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.models.enums import JobStatus
from app.services.application_workflow import ALLOWED_TRANSITIONS

MIRROR = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "statusTransitions.ts"
)


def _parse_mirror() -> dict[str, set[str]]:
    source = MIRROR.read_text(encoding="utf-8")
    body = re.search(
        r"ALLOWED_TRANSITIONS: Record<JobStatus, readonly JobStatus\[\]> = \{(.*?)\n\}",
        source,
        re.S,
    )
    assert body, "the mirror's table literal could not be located"
    parsed: dict[str, set[str]] = {}
    for name, targets in re.findall(r"(\w+): \[([^\]]*)\]", body.group(1)):
        parsed[name] = {json.loads(t.strip().replace("'", '"')) for t in targets.split(",") if t.strip()}
    return parsed


def test_frontend_mirror_matches_the_backend_transition_table():
    mirror = _parse_mirror()
    backend = {
        status.value: {target.value for target in targets}
        for status, targets in ALLOWED_TRANSITIONS.items()
    }
    assert mirror == backend


def test_every_status_is_covered_so_a_new_one_cannot_be_forgotten():
    assert set(_parse_mirror()) == {status.value for status in JobStatus}
