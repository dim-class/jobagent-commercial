/**
 * The slice of the Chrome extension API this POC actually uses.
 *
 * Hand-written rather than pulling in `@types/chrome`, so the extension needs
 * exactly one dev dependency (TypeScript) and the surface it is allowed to
 * touch is visible in one short file. Nothing here reaches recruitment-site
 * cookies/storage, `webRequest`, `webNavigation`, `debugger`,
 * or `windows.create` (no new windows) - if a future,
 * separately-authorized milestone needs one of those, it has to be added
 * here first, deliberately. `storage.session` is the one exception: it is
 * the extension's own in-memory session storage (cleared on browser
 * restart), never a site's storage.
 *
 * `tabs.create` and `tabs.update(active)` are deliberately limited to the
 * authorized localhost-console start/resume handoff. `tabs.update` (URL)
 * was added deliberately for M4e/M4f (CLAUDE.md
 * "Chrome extension - M4 supervised navigation policy", explicitly
 * authorized): the one foreground-tab navigation that milestone permits,
 * to a same-origin BOSS search URL only - `background.ts`'s
 * `isRunnerNavOrigin` checks every URL before this is ever called.
 */

declare namespace chrome {
  namespace runtime {
    const lastError: { message?: string } | undefined

    function getURL(path: string): string
    /** Own packaged version only; no browser/profile information. */
    function getManifest(): { version: string }

    /** Own extension popup lifecycle only; no recruitment-page storage. */
    function getContexts(filter: { contextTypes: ['POPUP'] }): Promise<{ documentUrl?: string }[]>

    function sendMessage(message: unknown, callback?: (response: unknown) => void): void

    /** Only what a content script's sender carries in this extension: its
     * own tab id, used by the M4a background worker to answer "what tab am
     * I in" - never used to read or drive any other tab. */
    interface MessageSender {
      tab?: { id?: number }
      url?: string
      frameId?: number
    }

    const onMessage: {
      addListener(
        callback: (
          message: unknown,
          sender: MessageSender,
          sendResponse: (response?: unknown) => void,
        ) => boolean | void,
      ): void
    }
  }

  namespace tabs {
    const onActivated: {
      addListener(callback: () => void): void
      removeListener(callback: () => void): void
    }
    const onUpdated: {
      addListener(callback: (tabId: number, change: { url?: string; status?: string }) => void): void
      removeListener(callback: (tabId: number, change: { url?: string; status?: string }) => void): void
    }
    interface Tab {
      id?: number
      url?: string
      title?: string
      /** Is this the selected tab in its window - the closest available
       * foreground signal `chrome.tabs` exposes. M4e/M4f re-checks this
       * before every step; M4a-M4c never read it. */
      active?: boolean
      windowId?: number
    }

    function query(queryInfo: { active?: boolean; currentWindow?: boolean; windowId?: number }): Promise<Tab[]>

    function sendMessage(tabId: number, message: unknown): Promise<unknown>

    /** M4f navigation; active=true only for explicit console resume of its owned tab. */
    function update(tabId: number, updateProperties: { url?: string; active?: boolean }): Promise<Tab>

    /** Explicit localhost-console start only: one visible tab in the same normal window. */
    function create(properties: { url: string; active: true; windowId: number }): Promise<Tab>

    /** M4e/M4f only - re-reads the current state of one tab (url/active) so
     * the runner can verify, immediately before every step, that the tab it
     * is about to drive still exists, is still exact BOSS origin, and is
     * still the foreground tab of its window - never a background/hidden
     * one. Read-only; never itself changes anything about the tab. */
    function get(tabId: number): Promise<Tab>

    /** In-memory only; cropped before anything leaves the extension worker. */
    function captureVisibleTab(windowId: number, options: { format: 'png' }): Promise<string>
  }

  namespace windows {
    /** Read-only explicit foreground resolution; never activate a browser window. */
    function getLastFocused(queryOptions: { windowTypes: ['normal'] }): Promise<{ id?: number; type?: string; focused: boolean }>
    function get(windowId: number): Promise<{ focused: boolean }>
    const onFocusChanged: {
      addListener(callback: () => void): void
      removeListener(callback: () => void): void
    }
  }

  namespace scripting {
    interface InjectionTarget {
      tabId: number
      allFrames?: boolean
    }

    function executeScript(injection: {
      target: InjectionTarget
      files: string[]
    }): Promise<unknown[]>
  }

  namespace storage {
    interface StorageChange {
      oldValue?: unknown
      newValue?: unknown
    }

    /** Fires on any storage write, in any area - callers filter by
     * `areaName`. An event listener, not a timer or a poll. */
    const onChanged: {
      addListener(
        callback: (changes: Record<string, StorageChange>, areaName: string) => void,
      ): void
    }

    /** Extension-only, cleared on browser restart - never site storage. */
    namespace session {
      function get(keys: string | string[] | null): Promise<Record<string, unknown>>
      function set(items: Record<string, unknown>): Promise<void>
      function remove(keys: string | string[]): Promise<void>
    }
  }
}
