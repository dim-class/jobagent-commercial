"""Launch the capture browser once, to prove it works on this machine.

    python scripts\\browser_check.py

Opens the same visible browser the 浏览器采集 page uses, loads the BOSS home
page, waits a few seconds so you can see it, then closes it.

It does NOT log in, does not read any job, and does not save anything. Use the
web UI for real capture.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.logging import ensure_utf8_stdout, setup_logging  # noqa: E402
from app.services.browser_session import BrowserSession  # noqa: E402

HOLD_SECONDS = 6


async def _run() -> int:
    session = BrowserSession()
    profile = session._settings.browser_profile_path  # noqa: SLF001 - dev script
    print(f"  profile dir   {profile}")

    try:
        status, already = await session.start()
    except Exception as exc:  # noqa: BLE001
        message = getattr(exc, "message", None) or str(exc)
        print(f"\nFAILED - {message}")
        return 1

    print(f"  browser       {status.channel}")
    print(f"  running       {status.running} (already_running={already})")
    print(f"  open tabs     {status.page_count}")
    print(f"  current url   {status.current_url or '-'}")
    print(f"\n  A visible browser window should be open. Closing it in {HOLD_SECONDS}s...")

    await asyncio.sleep(HOLD_SECONDS)
    await session.stop()
    print("\n  PASSED - the capture browser launches and closes cleanly.")
    print("  Login state persists in the profile directory above.")
    return 0


def main() -> int:
    ensure_utf8_stdout()
    setup_logging("INFO")
    print("Job Agent capture-browser check")
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    sys.exit(main())
