/**
 * The slice of the Chrome extension API this POC actually uses.
 *
 * Hand-written rather than pulling in `@types/chrome`, so the extension needs
 * exactly one dev dependency (TypeScript) and the surface it is allowed to
 * touch is visible in one short file. Nothing here reaches recruitment-site
 * cookies/storage, `webRequest`, `webNavigation`, `debugger`, or
 * `tabs.update`/`tabs.create`/`windows.create` (no navigation, no new
 * windows/tabs) - if a future, separately-authorized milestone needs one of
 * those, it has to be added here first, deliberately. `storage.session` is
 * the one exception: it is the extension's own in-memory session storage
 * (cleared on browser restart), never a site's storage.
 */

declare namespace chrome {
  namespace runtime {
    const lastError: { message?: string } | undefined

    function getURL(path: string): string

    function sendMessage(message: unknown, callback?: (response: unknown) => void): void

    /** Only what a content script's sender carries in this extension: its
     * own tab id, used by the M4a background worker to answer "what tab am
     * I in" - never used to read or drive any other tab. */
    interface MessageSender {
      tab?: { id?: number }
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
    interface Tab {
      id?: number
      url?: string
      title?: string
    }

    function query(queryInfo: { active?: boolean; currentWindow?: boolean }): Promise<Tab[]>

    function sendMessage(tabId: number, message: unknown): Promise<unknown>
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
