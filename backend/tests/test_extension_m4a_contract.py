"""M4a extension contract - static checks against source and the built bundle.

No live browser, no Playwright, no zhipin.com - these are string/regex
assertions against `extension/manifest.json`, the M4a TypeScript sources,
and (when built) their compiled `dist/*.js` output. They exist to pin down
the M4a acceptance contract from CLAUDE.md's "Chrome extension - M4
supervised navigation policy" and docs/orchestration/ROADMAP.md M4a:

- the manifest wires the service worker and the overlay content script;
- the overlay never touches `chrome.storage.session` directly and never
  widens its access level;
- the background worker is the only owner of the session pointer;
- the popup pushes immediate start/stop messages to the approved tab;
- the bar shows the approved task name and caps/progress;
- a missing pointer with a backend-active session is stopped as
  `stale_tab` (browser-restart fail-closed path);
- no navigation/scroll/timer/observer API appears in *executable* code
  (comments are stripped first, so documentation mentioning a forbidden
  API by name - as this module's own docstrings do - is not a false
  positive).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

EXTENSION = Path(__file__).resolve().parents[2] / "extension"
SRC = EXTENSION / "src"
DIST = EXTENSION / "dist"

MANIFEST_PATH = EXTENSION / "manifest.json"
SESSION_TS = SRC / "session.ts"
OVERLAY_TS = SRC / "overlay.ts"
BACKGROUND_TS = SRC / "background.ts"

SESSION_JS = DIST / "session.js"
OVERLAY_JS = DIST / "overlay.js"
BACKGROUND_JS = DIST / "background.js"


def _strip_comments(code: str) -> str:
    """Block then line comments - same approach as test_extension_extraction.py,
    so a docstring mentioning a forbidden API by name is never a false positive."""
    without_blocks = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_blocks, flags=re.M)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


#: `background.ts`'s M4e/M4f bounded-automatic-runner section - real
#: (non-comment) tokens, so they survive being located before
#: `_strip_comments` runs. That section (constants through its own
#: functions, ending right before the shared `chrome.runtime.onMessage`
#: listener every milestone's handlers are registered in) is a separately,
#: explicitly authorized carve-out (CLAUDE.md "Chrome extension - M4
#: supervised navigation policy" M4e/M4f amendment) from exactly the
#: promises this M4a contract file checks: it is the one place `setTimeout`
#: (bounded DOM-stabilization polling) and `chrome.tabs.update` (one
#: foreground-tab navigation to a same-origin BOSS search URL) are allowed.
#: Excising just that span - not truncating the file there - keeps the
#: listener registration and every M4a/M4b/M4c `if` block after it (which
#: this file also asserts against) intact; the M4f `if` blocks that remain
#: only call the excised functions by name, so no forbidden token survives
#: in what is left either.
_M4F_SECTION_START_MARKER = "const RUNNER_STORAGE_KEY"
_M4F_SECTION_END_MARKER = "chrome.runtime.onMessage.addListener"


def _m4a_scope(raw_code: str) -> str:
    start = raw_code.find(_M4F_SECTION_START_MARKER)
    end = raw_code.find(_M4F_SECTION_END_MARKER)
    if start == -1 or end == -1 or end <= start:
        return raw_code
    return raw_code[:start] + raw_code[end:]


@pytest.fixture(scope="session")
def manifest() -> dict:
    return json.loads(_read(MANIFEST_PATH))


@pytest.fixture(scope="session")
def session_source() -> str:
    return _strip_comments(_read(SESSION_TS))


@pytest.fixture(scope="session")
def overlay_source() -> str:
    return _strip_comments(_read(OVERLAY_TS))


@pytest.fixture(scope="session")
def background_source() -> str:
    return _strip_comments(_m4a_scope(_read(BACKGROUND_TS)))


@pytest.fixture(scope="session")
def dist_code() -> str:
    """The built M4a bundle, comments stripped. Skips (never fails) when the
    extension has not been built - a fresh clone can run the rest of the
    suite before `npm run build` has been executed in `extension/`."""
    paths = (SESSION_JS, OVERLAY_JS, BACKGROUND_JS)
    missing = [p.name for p in paths if not p.exists()]
    if missing:
        pytest.skip(
            "extension not built - run 'npm install && npm run build' in "
            f"extension/ (missing: {', '.join(missing)})"
        )
    parts = [_read(SESSION_JS), _read(OVERLAY_JS), _m4a_scope(_read(BACKGROUND_JS))]
    return _strip_comments("\n;\n".join(parts))


@pytest.fixture(scope="session")
def overlay_dist_code() -> str:
    """`dist/overlay.js` alone, comments stripped - so the "no fetch / no
    backend URL" property is checked against the shipped overlay bundle by
    itself, not just diluted inside the combined `dist_code` fixture (which
    also contains background.js and session.js, both of which legitimately
    do call fetch)."""
    if not OVERLAY_JS.exists():
        pytest.skip(
            "extension not built - run 'npm install && npm run build' in "
            f"extension/ (missing: {OVERLAY_JS.name})"
        )
    return _strip_comments(_read(OVERLAY_JS))


# --------------------------------------------------------------------------
# manifest wiring
# --------------------------------------------------------------------------


def test_manifest_registers_the_background_service_worker(manifest):
    assert manifest.get("background", {}).get("service_worker") == "dist/background.js"


def test_manifest_content_scripts_include_the_overlay(manifest):
    scripts = manifest["content_scripts"][0]["js"]
    assert "dist/overlay.js" in scripts
    assert "dist/content.js" in scripts  # existing detection behavior untouched
    # The overlay must run after the extraction files it shares the isolated
    # world with, not before them.
    assert scripts.index("dist/content.js") < scripts.index("dist/overlay.js")


def test_manifest_still_scopes_content_scripts_to_the_exact_boss_host(manifest):
    assert manifest["content_scripts"][0]["matches"] == ["https://www.zhipin.com/*"]


def test_manifest_never_widens_permissions_for_navigation(manifest):
    permissions = set(manifest.get("permissions", []))
    forbidden = {"tabs", "webNavigation", "debugger", "webRequest", "webRequestBlocking"}
    assert not (permissions & forbidden), permissions & forbidden


# --------------------------------------------------------------------------
# the overlay never touches storage.session directly
# --------------------------------------------------------------------------


def test_overlay_never_reads_or_writes_storage_session(overlay_source):
    assert "storage.session" not in overlay_source


def test_overlay_never_widens_the_storage_access_level(overlay_source, session_source, background_source):
    for label, code in (("overlay", overlay_source), ("session", session_source), ("background", background_source)):
        assert "setAccessLevel" not in code, f"{label}.ts must never widen storage.session access"


def test_overlay_makes_no_network_call_and_knows_no_backend_url(overlay_source):
    """The actual fix: MV3 content scripts cannot reliably cross-origin
    fetch a loopback address from an `https://` page. The overlay must
    contain neither a `fetch(` call nor the backend origin string - all
    loopback HTTP moved to background.ts."""
    assert "fetch(" not in overlay_source
    assert "127.0.0.1" not in overlay_source


def test_the_built_overlay_bundle_also_makes_no_network_call(overlay_dist_code):
    """Asserted against the shipped `dist/overlay.js` too, so a source-only
    check cannot pass while a stale or hand-edited build still fetches."""
    assert "fetch(" not in overlay_dist_code
    assert "127.0.0.1" not in overlay_dist_code


def test_overlay_asks_the_background_worker_via_narrow_messages(overlay_source):
    assert "jobagent:session-snapshot" in overlay_source
    assert "jobagent:stop-session" in overlay_source
    # It sends these as runtime messages, not by reading storage or the
    # network itself.
    assert "chrome.runtime.sendMessage" in overlay_source


# --------------------------------------------------------------------------
# the background worker is the trusted pointer AND network owner
# --------------------------------------------------------------------------


def test_background_owns_storage_session_reads_and_writes(background_source):
    assert "chrome.storage.session.get" in background_source
    assert "chrome.storage.session.remove" in background_source


def test_background_owns_the_loopback_backend_calls(background_source):
    assert "127.0.0.1:8000" in background_source
    assert "fetch(" in background_source
    assert "/api/extension/sessions/active" in background_source
    assert "/stop" in background_source


def test_background_handles_both_high_level_messages(background_source):
    assert "'jobagent:session-snapshot'" in background_source
    assert "'jobagent:stop-session'" in background_source


def test_background_scopes_everything_to_the_requesting_tab(background_source):
    """The whole privacy property: a tab only ever gets its own pointer,
    and only ever stops its own session."""
    assert "sender.tab" in background_source
    assert "pointer.tabId !== tabId" in background_source
    assert "pointer.tabId" in background_source


def test_background_has_no_timer_alarm_or_navigation_api(background_source):
    forbidden = ("setInterval", "setTimeout", "chrome.alarms", "chrome.tabs.update", "chrome.tabs.create")
    for needle in forbidden:
        assert needle not in background_source, f"background.ts must not use {needle}"


# --------------------------------------------------------------------------
# popup pushes immediate start/stop notifications to the approved tab
# --------------------------------------------------------------------------


def test_popup_pushes_session_started_to_the_approved_tab(session_source):
    assert "chrome.tabs.sendMessage" in session_source
    assert "jobagent:session-started" in session_source


def test_popup_pushes_session_stopped_to_the_approved_tab(session_source):
    assert "jobagent:session-stopped" in session_source


def test_overlay_listens_for_both_push_notifications(overlay_source):
    assert "jobagent:session-started" in overlay_source
    assert "jobagent:session-stopped" in overlay_source


# --------------------------------------------------------------------------
# the bar shows the approved task name, caps, and progress
# --------------------------------------------------------------------------


def test_overlay_bar_renders_the_approved_task_name(overlay_source):
    assert "approved_criteria.task_name" in overlay_source


def test_overlay_bar_renders_caps_and_progress(overlay_source):
    for field in ("pages_visited", "page_cap", "candidates_extracted", "candidate_cap", "scrolls_used", "scroll_cap"):
        assert field in overlay_source, f"overlay bar must render {field}"


# --------------------------------------------------------------------------
# background's buildSnapshot: missing pointer + backend-active -> fail
# closed (browser restart); unreachable backend -> preserve, never clear
# --------------------------------------------------------------------------


def test_missing_pointer_with_a_running_backend_session_is_stopped_as_stale(background_source):
    """The browser-restart / missing-pointer path: `buildSnapshot` must
    check the backend even with no local pointer, and stop a still-running
    session with reason `stale_tab` rather than leaving it as a zombie."""
    start = background_source.index("async function buildSnapshot")
    end = background_source.index("async function stopForTab")
    body = background_source[start:end]

    no_pointer = body.index("if (!pointer)")
    stale_call = body.index("tryStopIfRunning", no_pointer)
    branch_return = body.index("return", stale_call)

    assert no_pointer < stale_call < branch_return
    assert "'stale_tab'" in background_source or '"stale_tab"' in background_source


def test_a_pointer_that_disagrees_with_the_backend_also_stops_stale(background_source):
    """The second fail-closed path: a pointer exists for this tab, but the
    backend's active session does not match it (wrong id/origin/status)."""
    assert background_source.count("tryStopIfRunning") >= 2


def test_an_unreachable_backend_never_clears_or_stops_an_owned_pointer(background_source):
    """The actual root-cause fix: when the backend cannot be reached for a
    tab that does own a pointer, `buildSnapshot` must return `unreachable`
    without touching the pointer or calling stop - never treat "could not
    check" as "confirmed gone"."""
    matches_index = background_source.index("const matches =")
    # The catch block immediately preceding the matching computation is the
    # "pointer belongs to this tab, but the backend could not be reached"
    # reachability check (comments are stripped, so this is the reliable
    # code marker rather than the prose).
    last_catch = background_source.rindex("catch", 0, matches_index)
    body = background_source[last_catch:matches_index]

    assert "'unreachable'" in body
    assert "clearGlobalPointer" not in body
    assert "tryStopIfRunning" not in body


# --------------------------------------------------------------------------
# a second, unapproved BOSS tab must stay silent - never stale-stop
# --------------------------------------------------------------------------


def test_background_distinguishes_a_second_tab_from_a_browser_restart(background_source):
    """A pointer belonging to a *different* tab must gate a distinct,
    earlier branch from "no pointer anywhere" - the literal condition a
    second, unapproved tab hits before anything else runs."""
    assert "pointer.tabId !== tabId" in background_source


def test_a_second_unapproved_tab_returns_before_any_stale_stop_or_backend_call(background_source):
    """Opening a second BOSS tab while a session runs on another must never
    call the stop endpoint and must never even query `/active` for that
    tab - the second-tab branch has to come first and exit immediately,
    with no leak of that other tab's pointer or session id."""
    start = background_source.index("async function buildSnapshot")
    end = background_source.index("async function stopForTab")
    body = background_source[start:end]

    second_tab_check = body.index("pointer.tabId !== tabId")
    second_tab_return = body.index("'other_tab'", second_tab_check)
    first_active_fetch = body.index("fetchActive")
    first_stale_stop = body.index("tryStopIfRunning")

    assert second_tab_check < second_tab_return
    assert second_tab_return < first_active_fetch
    assert second_tab_return < first_stale_stop


def test_only_true_global_absence_reaches_the_stale_stop_branch(background_source):
    """The stale-stop call reachable when there is truly no pointer for any
    tab must come after the second-tab branch above - i.e. it is reached
    only once a different tab's pointer has already been ruled out."""
    start = background_source.index("async function buildSnapshot")
    end = background_source.index("async function stopForTab")
    body = background_source[start:end]

    second_tab_check = body.index("pointer.tabId !== tabId")
    global_absence_check = body.index("if (!pointer)")
    stale_stop_call = body.index("tryStopIfRunning", global_absence_check)

    assert second_tab_check < global_absence_check < stale_stop_call


# --------------------------------------------------------------------------
# stopping from the bar: worker verifies ownership, clears only if confirmed
# --------------------------------------------------------------------------


def test_stop_from_bar_verifies_the_sender_owns_the_pointer(background_source):
    start = background_source.index("async function stopForTab")
    end = background_source.index("chrome.runtime.onMessage.addListener")
    body = background_source[start:end]
    assert "pointer.tabId !== tabId" in body


def test_stop_from_bar_clears_the_pointer_only_after_a_confirmed_stop(background_source):
    start = background_source.index("async function stopForTab")
    end = background_source.index("chrome.runtime.onMessage.addListener")
    body = background_source[start:end]

    post_stop = body.index("postStop")
    clear_call = body.index("clearGlobalPointer", post_stop)
    ok_true = body.index("{ ok: true }", clear_call)

    # clearGlobalPointer only runs after postStop, and only the success path
    # returns ok:true - a caught/failed stop must return before reaching it.
    assert post_stop < clear_call < ok_true


# --------------------------------------------------------------------------
# what must never appear in executable code (comments stripped)
# --------------------------------------------------------------------------


FORBIDDEN_NAVIGATION_AND_ACTION_APIS = (
    "location.assign",
    "location.replace",
    "location.href =",
    "window.open",
    ".scrollTo",
    ".scrollBy",
    ".scrollIntoView",
    ".click(",
    ".submit(",
    "chrome.tabs.update",
    "chrome.tabs.create",
    "chrome.windows.create",
    "chrome.webNavigation",
    "chrome.debugger",
    "storage.session.setAccessLevel",
)

FORBIDDEN_PASSIVE_TRACKING_APIS = (
    "setInterval",
    "setTimeout",
    "MutationObserver",
    "IntersectionObserver",
    "requestAnimationFrame",
    "chrome.alarms",
)

FORBIDDEN_SITE_DATA_APIS = (
    "document.cookie",
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "navigator.credentials",
)


@pytest.mark.parametrize("needle", FORBIDDEN_NAVIGATION_AND_ACTION_APIS)
def test_m4a_source_never_navigates_clicks_or_scrolls(
    needle, session_source, overlay_source, background_source
):
    for label, code in (
        ("session", session_source),
        ("overlay", overlay_source),
        ("background", background_source),
    ):
        assert needle not in code, f"{label}.ts must not use {needle}"


@pytest.mark.parametrize("needle", FORBIDDEN_PASSIVE_TRACKING_APIS)
def test_m4a_source_has_no_timer_poll_or_observer(needle, session_source, overlay_source, background_source):
    for label, code in (
        ("session", session_source),
        ("overlay", overlay_source),
        ("background", background_source),
    ):
        assert needle not in code, f"{label}.ts must not use {needle}"


@pytest.mark.parametrize("needle", FORBIDDEN_SITE_DATA_APIS)
def test_m4a_source_never_reads_site_data(needle, session_source, overlay_source, background_source):
    for label, code in (
        ("session", session_source),
        ("overlay", overlay_source),
        ("background", background_source),
    ):
        assert needle not in code, f"{label}.ts must not read {needle}"


@pytest.mark.parametrize(
    "needle",
    FORBIDDEN_NAVIGATION_AND_ACTION_APIS + FORBIDDEN_PASSIVE_TRACKING_APIS + FORBIDDEN_SITE_DATA_APIS,
)
def test_the_built_m4a_bundle_also_has_none_of_it(needle, dist_code):
    """Asserted against the shipped output too, so a source-only check
    cannot pass while a build step (or a stray `any`-cast workaround)
    reintroduces something forbidden."""
    assert needle not in dist_code, f"built M4a bundle must not contain {needle}"


# ---------------------------------------------------------------------------
# The popup never stops a session it did not start
# ---------------------------------------------------------------------------


NEXT_FUNCTION = chr(10) + "  function "


def _recover_body(code: str) -> str:
    """`recoverOrReset`'s body, up to the next function declaration."""
    start = code.index("recoverOrReset")
    rest = code[start:]
    end = rest.find(NEXT_FUNCTION, 1)
    return rest if end == -1 else rest[:end]


def test_the_popup_leaves_another_entry_points_session_alone(session_source):
    """A console-started run has no pointer here, and is not this popup's to end.

    Live on 2026-09-05 it was: the console tells the user to click the toolbar
    icon once (that is how salary OCR gets its `activeTab` grant), the popup
    opened, saw a running session with no pointer of its own, and stopped it as
    `stale_tab` 0.6s after it was created. The run then failed on its next
    navigation with 会话未在进行中.
    """
    body = _recover_body(session_source)
    guard = body.index("if (!pointer)")
    stop = body.index("stopSession('stale_tab')")
    assert guard < stop, "the no-pointer early return must come first"


def test_the_built_popup_bundle_carries_the_same_guard():
    if not SESSION_JS.exists():
        import pytest

        pytest.skip("extension/dist not built")
    body = _recover_body(_strip_comments(_read(SESSION_JS)))
    assert body.index("if (!pointer)") < body.index("stopSession('stale_tab')")
