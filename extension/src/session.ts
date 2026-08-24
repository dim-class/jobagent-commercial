/**
 * M4a bounded-session scaffolding: explicit human approval, start/stop, an
 * always-visible stop control, and zero-valued progress counters. No
 * navigation, no scrolling, no clicking a job - see CLAUDE.md's "Chrome
 * extension - M4 supervised navigation policy" and
 * docs/orchestration/ROADMAP.md M4a.
 *
 * State lives in `chrome.storage.session` (this extension's own in-memory
 * session storage, cleared on browser restart) - never a recruitment
 * site's cookies/localStorage/sessionStorage. A browser restart or a tab
 * that no longer matches what was approved fails closed: the session is
 * stopped, never silently resumed.
 */

;(function () {
  interface TaskOut {
    id: number
    name: string
    keywords: string | null
    city: string | null
    experience_text: string | null
    education_text: string | null
  }

  interface SessionEvent {
    event_type: 'started' | 'stopped'
    reason: string | null
    created_at: string
  }

  interface SessionOut {
    id: number
    task_id: number
    status: 'running' | 'stopped'
    approved_criteria: { task_name: string }
    page_cap: number
    candidate_cap: number
    scroll_cap: number
    tab_origin: string
    pages_visited: number
    candidates_extracted: number
    scrolls_used: number
    events: SessionEvent[]
  }

  //: Immutable POC ceilings - CLAUDE.md "1a." A human's approved caps may be
  //: lower, never higher. Mirrors backend/app/services/supervised_sessions.py;
  //: the backend re-checks these independently and is the real enforcement.
  const CEILING = { pageCap: 3, candidateCap: 20, scrollCap: 5 }
  const REQUIRED_ORIGIN = 'https://www.zhipin.com'
  const STORAGE_KEY = 'jobagent_session_pointer'

  interface StoredPointer {
    sessionId: number
    tabId: number
  }

  const $ = (id: string) => document.getElementById(id) as HTMLElement
  const taskSelect = $('session-task') as HTMLSelectElement
  const criteriaEl = $('session-criteria')
  const pageCapInput = $('session-page-cap') as HTMLInputElement
  const candidateCapInput = $('session-candidate-cap') as HTMLInputElement
  const scrollCapInput = $('session-scroll-cap') as HTMLInputElement
  const tabConfirmEl = $('session-tab-confirm')
  const startBtn = $('session-start') as HTMLButtonElement
  const stopBtn = $('session-stop') as HTMLButtonElement
  const sessionStatusEl = $('session-status')
  const progressEl = $('session-progress')
  const historyEl = $('session-history')

  let currentSessionId: number | null = null

  function setSessionStatus(text: string, tone: 'info' | 'ok' | 'warn' | 'error' = 'info') {
    sessionStatusEl.textContent = text
    sessionStatusEl.className = 'status status-' + tone
  }

  /** One typed trust boundary: the network response is `unknown` until this
   * single cast, never an implicit or explicit `any` anywhere else. */
  async function request<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
    const response = await fetch(JobAgentConfig.BACKEND_BASE + path, {
      method: init?.method || 'GET',
      headers: init?.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    })
    const text = await response.text()
    const payload: unknown = text ? JSON.parse(text) : null
    if (!response.ok) {
      const message = (payload as { message?: string } | null)?.message
      throw new Error(message || `请求失败（HTTP ${response.status}）`)
    }
    return payload as T
  }

  async function activeTab(): Promise<chrome.tabs.Tab | null> {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true })
    return tabs.length ? tabs[0] : null
  }

  function originOf(url: string): string | null {
    try {
      const parsed = new URL(url)
      return parsed.protocol + '//' + parsed.host
    } catch {
      return null
    }
  }

  function clampCaps() {
    pageCapInput.max = String(CEILING.pageCap)
    candidateCapInput.max = String(CEILING.candidateCap)
    scrollCapInput.max = String(CEILING.scrollCap)
    if (Number(pageCapInput.value) > CEILING.pageCap) pageCapInput.value = String(CEILING.pageCap)
    if (Number(candidateCapInput.value) > CEILING.candidateCap) {
      candidateCapInput.value = String(CEILING.candidateCap)
    }
    if (Number(scrollCapInput.value) > CEILING.scrollCap) scrollCapInput.value = String(CEILING.scrollCap)
  }

  async function loadTasks() {
    taskSelect.innerHTML = ''
    try {
      const response = await request<{ items: TaskOut[] }>(JobAgentConfig.TASKS_PATH)
      const tasks: TaskOut[] = response.items || []
      if (!tasks.length) {
        taskSelect.appendChild(new Option('（还没有任务，请先在控制台创建）', ''))
        startBtn.disabled = true
        return
      }
      startBtn.disabled = false
      for (const task of tasks) taskSelect.appendChild(new Option(task.name, String(task.id)))
      renderCriteria(tasks[0])
      taskSelect.addEventListener('change', () => {
        const selected = tasks.find((t) => String(t.id) === taskSelect.value)
        if (selected) renderCriteria(selected)
      })
    } catch (err) {
      setSessionStatus('无法加载任务列表：' + String((err as Error)?.message || err), 'error')
    }
  }

  function renderCriteria(task: TaskOut) {
    const parts = [task.keywords, task.city, task.experience_text, task.education_text]
      .filter(Boolean)
      .join(' · ')
    criteriaEl.textContent = parts || '未设置搜索条件'
  }

  async function confirmTab(): Promise<{ ok: boolean; origin: string | null; tabId: number | null }> {
    const tab = await activeTab()
    if (!tab || tab.id === undefined || !tab.url) {
      tabConfirmEl.textContent = '找不到当前标签页。'
      return { ok: false, origin: null, tabId: null }
    }
    const origin = originOf(tab.url)
    if (origin !== REQUIRED_ORIGIN) {
      tabConfirmEl.textContent =
        `当前标签页不是 ${REQUIRED_ORIGIN}（实际：${origin || '未知'}）。请切换到该页面后重试。`
      return { ok: false, origin, tabId: tab.id }
    }
    tabConfirmEl.textContent = `已确认当前标签页：${REQUIRED_ORIGIN}`
    return { ok: true, origin, tabId: tab.id }
  }

  async function storePointer(pointer: StoredPointer | null) {
    if (pointer) await chrome.storage.session.set({ [STORAGE_KEY]: pointer })
    else await chrome.storage.session.remove(STORAGE_KEY)
  }

  async function loadPointer(): Promise<StoredPointer | null> {
    const stored = await chrome.storage.session.get(STORAGE_KEY)
    return (stored[STORAGE_KEY] as StoredPointer | undefined) || null
  }

  const EVENT_LABEL: Record<string, string> = { started: '已开始', stopped: '已停止' }

  function renderHistory(events: SessionEvent[]) {
    historyEl.innerHTML = ''
    for (const event of events) {
      const line = document.createElement('div')
      line.className = 'sub'
      line.textContent =
        (EVENT_LABEL[event.event_type] || event.event_type) +
        (event.reason ? `（${event.reason}）` : '') +
        ` · ${event.created_at}`
      historyEl.appendChild(line)
    }
  }

  function renderRunning(session: SessionOut) {
    startBtn.classList.add('hidden')
    stopBtn.classList.remove('hidden')
    taskSelect.disabled = true
    pageCapInput.disabled = true
    candidateCapInput.disabled = true
    scrollCapInput.disabled = true
    progressEl.textContent =
      `会话进行中 · 页面 ${session.pages_visited}/${session.page_cap} · ` +
      `候选人 ${session.candidates_extracted}/${session.candidate_cap} · ` +
      `本页滚动 ${session.scrolls_used}/${session.scroll_cap}`
    renderHistory(session.events)
  }

  function renderIdle() {
    startBtn.classList.remove('hidden')
    stopBtn.classList.add('hidden')
    taskSelect.disabled = false
    pageCapInput.disabled = false
    candidateCapInput.disabled = false
    scrollCapInput.disabled = false
    progressEl.textContent = ''
  }

  async function startSession() {
    clampCaps()
    const { ok, origin, tabId } = await confirmTab()
    if (!ok || tabId === null || !origin) {
      setSessionStatus('无法启动：请先切换到 BOSS 直聘页面（https://www.zhipin.com）。', 'error')
      return
    }
    const taskId = Number(taskSelect.value)
    if (!taskId) {
      setSessionStatus('请先选择一个任务。', 'error')
      return
    }

    startBtn.disabled = true
    try {
      const session = await request<SessionOut>(JobAgentConfig.SESSIONS_PATH, {
        method: 'POST',
        body: {
          task_id: taskId,
          page_cap: Number(pageCapInput.value),
          candidate_cap: Number(candidateCapInput.value),
          scroll_cap: Number(scrollCapInput.value),
          tab_origin: origin,
        },
      })
      currentSessionId = session.id
      await storePointer({ sessionId: session.id, tabId })
      // Push it to the approved tab immediately - no reload needed, no
      // storage event content scripts cannot receive, just one message.
      try {
        await chrome.tabs.sendMessage(tabId, { type: 'jobagent:session-started', session })
      } catch {
        // The overlay will still pick this up on the tab's next load.
      }
      renderRunning(session)
      setSessionStatus(
        '会话已开始。本阶段（M4a）不会翻页、滚动、点击岗位或读取任何岗位信息 —— 仅记录会话本身。',
        'ok',
      )
    } catch (err) {
      setSessionStatus('启动会话失败：' + String((err as Error)?.message || err), 'error')
    } finally {
      startBtn.disabled = false
    }
  }

  async function stopSession(reason: 'user_stop' | 'stale_tab') {
    if (currentSessionId === null) return
    const pointer = await loadPointer()
    stopBtn.disabled = true
    try {
      const session = await request<SessionOut>(
        `${JobAgentConfig.SESSIONS_PATH}/${currentSessionId}/stop`,
        { method: 'POST', body: { reason } },
      )
      await storePointer(null)
      if (pointer) {
        try {
          await chrome.tabs.sendMessage(pointer.tabId, { type: 'jobagent:session-stopped' })
        } catch {
          // Tab may be gone or navigated away - nothing more to remove there.
        }
      }
      currentSessionId = null
      renderIdle()
      renderHistory(session.events)
      setSessionStatus(
        reason === 'user_stop'
          ? '会话已停止。'
          : '检测到标签页已失效或浏览器已重启，会话已自动停止（不会继续任何操作）。',
        reason === 'user_stop' ? 'ok' : 'warn',
      )
    } catch (err) {
      setSessionStatus('停止会话失败：' + String((err as Error)?.message || err), 'error')
    } finally {
      stopBtn.disabled = false
    }
  }

  /**
   * On popup open: recover a running session only if the local pointer, the
   * backend, and the current active tab all agree. Any mismatch - browser
   * restart, a closed/changed tab, or a backend session with no matching
   * local pointer - fails closed: stop it, never resume it silently.
   */
  async function recoverOrReset() {
    const pointer = await loadPointer()
    let active: SessionOut | null = null
    try {
      active = await request<SessionOut | null>(JobAgentConfig.SESSIONS_ACTIVE_PATH)
    } catch {
      active = null
    }

    if (!active) {
      if (pointer) await storePointer(null)
      renderIdle()
      return
    }

    const tab = await activeTab()
    const origin = tab && tab.url ? originOf(tab.url) : null
    const tabMatches = !!pointer && !!tab && pointer.tabId === tab.id && origin === active.tab_origin

    currentSessionId = active.id
    if (!tabMatches) {
      await stopSession('stale_tab')
      return
    }

    renderRunning(active)
    setSessionStatus('已恢复正在进行的会话。', 'info')
  }

  startBtn.addEventListener('click', () => void startSession())
  stopBtn.addEventListener('click', () => void stopSession('user_stop'))
  ;[pageCapInput, candidateCapInput, scrollCapInput].forEach((input) =>
    input.addEventListener('change', clampCaps),
  )

  void (async function init() {
    clampCaps()
    await loadTasks()
    await recoverOrReset()
  })()
})()
