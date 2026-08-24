"""Visible-browser lifecycle for human-driven job capture (v0.2).

One headed Chromium, one persistent profile, one instance per backend process.

    start()  -> launch (or report already_running)
    status() -> running / site / current url / current title
    stop()   -> close cleanly

What this module deliberately does **not** do:
  * no headless mode - the human must see and drive the browser;
  * no login automation, no credential storage, no form filling;
  * no CAPTCHA or verification handling of any kind;
  * no navigation on the user's behalf beyond the initial landing page;
  * no clicking of 立即沟通 / apply / send controls, ever.

The agent only *reads* the page the human is already looking at.

Privacy: cookies, localStorage and page HTML never leave this process and are
never logged. URLs are logged with their query string stripped because BOSS
puts session-scoped tokens (``securityId``, ``lid``) there.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    async_playwright,
)

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import get_logger, log_event
# Re-exported so existing imports from this module keep working.
from app.services.urls import canonical_url, host_of, redact_url

logger = get_logger(__name__)

#: Schemes we refuse to read from, whatever the host says.
BLOCKED_SCHEMES = ("chrome:", "chrome-extension:", "about:", "devtools:", "edge:", "view-source:")

DEFAULT_LANDING_URL = "https://www.zhipin.com/"


class BrowserNotRunningError(AppError):
    status_code = 409
    code = "browser_not_running"


class BrowserUnavailableError(AppError):
    """Playwright could not launch - usually a missing Chromium download."""

    status_code = 503
    code = "browser_unavailable"


class NoSupportedPageError(AppError):
    status_code = 422
    code = "no_supported_page"


def is_blocked_url(url: str | None) -> bool:
    lowered = (url or "").strip().lower()
    if not lowered or lowered in ("about:blank",):
        return True
    return any(lowered.startswith(scheme) for scheme in BLOCKED_SCHEMES)


def matches_supported_host(url: str | None, supported: list[str]) -> bool:
    """True when ``url`` belongs to a configured recruitment domain."""
    if is_blocked_url(url):
        return False
    host = host_of(url)
    if not host:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in supported)


def site_for_url(url: str | None, supported: list[str]) -> str | None:
    """Map a URL to a job-source name (``zhipin.com`` -> ``boss``)."""
    if not matches_supported_host(url, supported):
        return None
    host = host_of(url)
    if "zhipin.com" in host:
        return "boss"
    return None


def pick_active_page(candidates: list, activity: dict) -> object:
    """Choose which supported tab the human is working in.

    ``activity`` maps page -> monotonically increasing sequence number, bumped
    on every main-frame navigation. The most recently navigated tab wins; with
    no activity recorded we fall back to the newest tab, since
    ``context.pages`` is in creation order.
    """
    if not candidates:
        raise ValueError("no candidate pages")
    if len(candidates) == 1:
        return candidates[0]
    return max(
        enumerate(candidates),
        key=lambda pair: (activity.get(pair[1], -1), pair[0]),
    )[1]


@dataclass(slots=True)
class BrowserStatus:
    running: bool
    site: str | None = None
    current_url: str | None = None
    current_title: str | None = None
    page_count: int = 0
    supported_hosts: list[str] | None = None
    profile_dir: str | None = None
    channel: str | None = None
    message: str = ""


class BrowserSession:
    """Singleton-per-process wrapper around one persistent Playwright context."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._browser: Browser | None = None
        self._closed_unexpectedly = False
        self._channel: str | None = None
        # Which tab the user last navigated, so capture reads the right one.
        self._activity: dict[Page, int] = {}
        self._activity_seq = itertools.count()
        # True once a supported recruitment page has been loaded in this run.
        self._saw_supported_page = False
        # Serialises start/stop so a double-click cannot launch two browsers.
        self._lock = asyncio.Lock()

    # -- internal helpers -------------------------------------------------

    @property
    def supported_hosts(self) -> list[str]:
        return self._settings.browser_supported_host_list

    def _mark_closed(self) -> None:
        """Called by Playwright when the human closes the browser window."""
        if self._context is not None:
            self._closed_unexpectedly = True
            log_event(logger, "browser.closed_externally")
        self._context = None
        self._browser = None

    def _live_pages(self) -> list[Page]:
        if self._context is None:
            return []
        try:
            return [p for p in self._context.pages if not p.is_closed()]
        except PlaywrightError:
            return []

    def is_running(self) -> bool:
        return self._context is not None and bool(self._live_pages() or self._context)

    # -- launching --------------------------------------------------------

    def _channel_candidates(self) -> list[str | None]:
        """Which browser build to try, in order.

        Playwright's own Chromium is preferred, as required. The system Edge /
        Chrome fallbacks exist because Playwright's bundled ``chrome.exe`` will
        not start on a Windows box that is missing the Microsoft Visual C++
        runtime (it fails with a side-by-side configuration error) - the
        headless shell is unaffected, so the problem only appears for the
        headed browser this feature needs.

        Every candidate is launched against OUR dedicated profile directory,
        so the user's day-to-day Edge/Chrome profile is never opened, reused
        or locked.
        """
        configured = (self._settings.browser_channel or "").strip().lower()
        if configured and configured not in ("auto", "chromium"):
            return [configured]
        if configured == "chromium":
            return [None]
        return [None, "msedge", "chrome"]

    async def _launch_context(self, profile_dir) -> tuple[BrowserContext, str]:
        assert self._playwright is not None
        errors: list[str] = []

        for channel in self._channel_candidates():
            try:
                context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    # Headed on purpose. The human logs in, searches and clears
                    # any verification; we never automate any of that.
                    headless=False,
                    channel=channel,
                    locale=self._settings.browser_locale,
                    timeout=self._settings.browser_launch_timeout_ms,
                    no_viewport=True,
                    args=[
                        "--start-maximized",
                        # Suppress the browser's own permission popups: on Edge
                        # the notification prompt opens an internal page and
                        # steals the tab. This is a browser UI setting, not any
                        # kind of site-protection bypass.
                        "--disable-notifications",
                        "--deny-permission-prompts",
                    ],
                )
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                label = channel or "chromium"
                errors.append(f"{label}:{type(exc).__name__}")
                log_event(logger, "browser.launch_attempt_failed", channel=label)
                continue

            label = channel or "chromium"
            log_event(logger, "browser.launched", channel=label)
            return context, label

        await self._teardown()
        log_event(logger, "browser.launch_failed", attempts=",".join(errors))
        raise BrowserUnavailableError(
            "无法启动可见浏览器。请先运行 .\\scripts\\dev.ps1 playwright-install 安装 "
            "Playwright 浏览器；若仍失败，可安装 Microsoft Edge 或 Chrome，"
            "或在 .env 中设置 BROWSER_CHANNEL=msedge。",
            detail={"attempts": errors},
        )

    # -- lifecycle --------------------------------------------------------

    async def start(self, landing_url: str = DEFAULT_LANDING_URL) -> tuple[BrowserStatus, bool]:
        """Launch the visible browser. Returns ``(status, already_running)``."""
        async with self._lock:
            if self._context is not None:
                # Confirm it is genuinely alive before claiming so.
                if self._live_pages():
                    log_event(logger, "browser.start_noop_already_running")
                    return await self._status_unlocked(), True
                await self._teardown()

            profile_dir = self._settings.browser_profile_path
            profile_dir.mkdir(parents=True, exist_ok=True)

            try:
                self._playwright = await async_playwright().start()
            except Exception as exc:  # noqa: BLE001
                await self._teardown()
                raise BrowserUnavailableError(
                    "无法启动 Playwright。请确认后端依赖已正确安装。",
                    detail={"error_type": type(exc).__name__},
                ) from exc

            self._context, self._channel = await self._launch_context(profile_dir)
            self._closed_unexpectedly = False
            self._browser = self._context.browser
            self._activity.clear()
            self._saw_supported_page = False
            self._context.on("close", lambda _ctx: self._mark_closed())
            self._context.on("page", self._attach_page)
            self._track_existing_pages()

            page = self._live_pages()[0] if self._live_pages() else await self._context.new_page()
            if landing_url:
                try:
                    await page.goto(landing_url, wait_until="domcontentloaded", timeout=30_000)
                except PlaywrightError:
                    # A slow or blocked landing page is not fatal - the human
                    # can navigate wherever they like from here.
                    log_event(logger, "browser.landing_navigation_failed")

            log_event(
                logger,
                "browser.started",
                profile=str(profile_dir),
                hosts=",".join(self.supported_hosts),
            )
            return await self._status_unlocked(), False

    async def stop(self) -> bool:
        """Close the browser. Returns True if something was actually running."""
        async with self._lock:
            was_running = self._context is not None
            await self._teardown()
            if was_running:
                log_event(logger, "browser.stopped")
            return was_running

    async def _teardown(self) -> None:
        context, playwright = self._context, self._playwright
        self._channel = None
        self._activity.clear()
        self._context = None
        self._browser = None
        self._playwright = None
        if context is not None:
            try:
                await context.close()
            except Exception:  # noqa: BLE001 - already gone is fine
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:  # noqa: BLE001
                pass

    async def shutdown(self) -> None:
        """Called from the FastAPI lifespan on process exit."""
        await self.stop()

    # -- page selection ---------------------------------------------------

    def _touch(self, page: Page) -> None:
        """Record that this tab just did something the user caused."""
        self._activity[page] = next(self._activity_seq)
        try:
            if matches_supported_host(page.url, self.supported_hosts):
                self._saw_supported_page = True
        except PlaywrightError:  # pragma: no cover - page going away
            pass

    def _attach_page(self, page: Page) -> None:
        """Watch a tab so we can tell which one the human is working in.

        ``document.visibilityState`` cannot be used for this: Playwright
        launches Chromium with background throttling and occlusion detection
        disabled, so every tab reports ``visible`` regardless of which one is
        in front. Navigation is the reliable signal - the tab the user most
        recently loaded a page in is the tab they are looking at.
        """
        if page in self._activity:
            return
        self._touch(page)
        page.on("framenavigated", lambda frame: self._on_navigated(page, frame))
        page.on("close", lambda _p: self._activity.pop(page, None))

    def _on_navigated(self, page: Page, frame: object) -> None:
        # Main-frame navigations only; sub-frame ads must not count as activity.
        try:
            if frame is not page.main_frame:
                return
        except PlaywrightError:  # pragma: no cover - page going away
            return
        self._touch(page)

    def _track_existing_pages(self) -> None:
        for page in self._live_pages():
            self._attach_page(page)

    async def active_page(self) -> Page:
        """The supported recruitment page the human is working in.

        Selection: most recently navigated supported tab, falling back to the
        newest one. The chosen URL is always echoed back to the UI so the user
        can confirm what is about to be captured.
        """
        self.ensure_running()
        pages = self._live_pages()
        if not pages:
            self._mark_closed()
            raise BrowserNotRunningError(
                "浏览器已被手动关闭，请重新点击「打开浏览器」。",
                detail={"reason": "no_pages"},
            )

        candidates = [p for p in pages if matches_supported_host(p.url, self.supported_hosts)]
        if not candidates:
            if self._saw_supported_page:
                raise NoSupportedPageError(
                    "招聘网站没有向自动化浏览器返回页面内容（页面为空白）。本工具不会绕过此类限制，请改用「岗位库 → 添加岗位」手动粘贴 JD。",
                    detail={"supported_hosts": self.supported_hosts, "reason": "page_empty"},
                )
            raise NoSupportedPageError(
                "当前页面不是受支持的招聘网站。请在浏览器中打开 BOSS 直聘（zhipin.com）后重试。",
                detail={"supported_hosts": self.supported_hosts, "open_pages": len(pages)},
            )

        self._track_existing_pages()
        return pick_active_page(candidates, self._activity)

    def ensure_running(self) -> None:
        if self._context is None or not self._live_pages():
            if self._context is not None:
                self._mark_closed()
            message = (
                "浏览器已被手动关闭，请重新点击「打开浏览器」。"
                if self._closed_unexpectedly
                else "浏览器尚未启动，请先点击「打开浏览器」。"
            )
            raise BrowserNotRunningError(message, detail={"running": False})

    # -- status -----------------------------------------------------------

    async def status(self) -> BrowserStatus:
        async with self._lock:
            return await self._status_unlocked()

    async def _status_unlocked(self) -> BrowserStatus:
        base = BrowserStatus(
            running=False,
            supported_hosts=self.supported_hosts,
            profile_dir=str(self._settings.browser_profile_path),
            channel=self._channel,
        )
        if self._context is None:
            base.message = (
                "浏览器已被手动关闭" if self._closed_unexpectedly else "浏览器未启动"
            )
            return base

        pages = self._live_pages()
        if not pages:
            self._mark_closed()
            base.running = False
            base.message = "浏览器已被手动关闭"
            return base

        base.running = True
        base.page_count = len(pages)

        candidates = [p for p in pages if matches_supported_host(p.url, self.supported_hosts)]
        if not candidates:
            base.message = (
                "招聘网站没有向自动化浏览器返回页面内容（页面为空白）。本工具不会绕过此类限制，请改用「岗位库 → 添加岗位」手动粘贴 JD。"
                if self._saw_supported_page
                else "浏览器已启动，但当前没有打开受支持的招聘网站页面"
            )
            return base

        self._track_existing_pages()
        page = pick_active_page(candidates, self._activity)
        base.current_url = page.url
        base.site = site_for_url(page.url, self.supported_hosts)
        try:
            base.current_title = await page.title()
        except PlaywrightError:
            base.current_title = None
        base.message = "浏览器运行中"
        return base


# Module-level singleton: the backend process owns exactly one browser.
_session: BrowserSession | None = None


def get_browser_session() -> BrowserSession:
    global _session
    if _session is None:
        _session = BrowserSession()
    return _session


def reset_browser_session() -> None:
    """Drop the singleton. Tests only."""
    global _session
    _session = None
