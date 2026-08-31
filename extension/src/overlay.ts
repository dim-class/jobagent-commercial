/**
 * M4b/M4c fixed session bar for the approved BOSS tab - task/caps/progress,
 * a Stop control, and two explicitly-authorized human-click controls
 * (CLAUDE.md "Chrome extension - M4 supervised navigation policy"):
 * "下一位候选人" opens one already-rendered search-result card; "向下滚动"
 * performs one bounded scroll step on the results container. Both are
 * exactly one human click - neither chains, retries, or waits for another.
 *
 * Real logged-in Chrome verification established that BOSS's
 * `/web/geek/jobs` results are one continuous/infinite scroll list with no
 * pagination control at all - a "下一页" action was implemented and tested,
 * but it always failed with `no_control` on the real page and ended an
 * otherwise-valid session. It has been removed rather than left as a
 * permanently-broken button: this milestone now only ever asks the human to
 * scroll (bounded) or open a candidate, never to paginate. The backend's
 * `target: "results"`/`page_cap`/`pages_visited` accounting is untouched -
 * the starting page is still counted once at session creation - it is just
 * never incremented again, because this flow never leaves that one page.
 *
 * This content script makes NO network calls and contains no backend URL:
 * MV3 content scripts cannot reliably cross-origin fetch a loopback address
 * from an `https://` page - that was the actual cause of a prior M4a bug.
 * All loopback HTTP lives in `background.ts`; this script only exchanges
 * narrow runtime messages with it and renders/removes UI based on the
 * answer. It never touches `chrome.storage.session` directly either.
 *
 * The navigation state machine is prepare-then-confirm, never click-then-
 * ask: `jobagent:navigate-prepare` must return `ok: true` *before* this
 * script clicks or scrolls anything (a denial - cap reached, session
 * stopped, wrong origin - performs nothing at all), and
 * `jobagent:navigate-confirm` reports the step's *real* outcome afterward -
 * a failed step is confirmed as `outcome: "failed"` and never advances any
 * counter.
 *
 * Every applicable policy hard stop below - verification/CAPTCHA/login/
 * security/rate-limit, wrong origin/page shape, no candidates left/a
 * detected candidate loop, a denied prepare, a failed step, or a confirm
 * call that itself failed - actually ends the session: it POSTs stop
 * through `background.ts` (which clears the pointer only after that stop is
 * confirmed), then removes the navigation controls so nothing can be
 * clicked again. None of them retry automatically; the human must start a
 * new session for another attempt. `user_stop` (the Stop button) and
 * `stale_tab` (M4a's reload/restart fail-closed path) are unchanged.
 *
 * No timer, no polling, no MutationObserver, no auto-scroll/chain. It never
 * searches, applies (立即沟通), messages, follows/collects, or reads
 * cookies/storage/forms - the only DOM mutations it ever performs are one
 * bounded scroll (`boss/extract.ts`'s `scrollResultsContainer`) and one
 * `.click()` on an already-rendered card link (`openCandidateLink`, the one
 * click primitive left in the whole extension). See CLAUDE.md's "Chrome
 * extension - M4 supervised navigation policy" and
 * docs/orchestration/ROADMAP.md M4b/M4c.
 */

;(function () {
  const REQUIRED_ORIGIN = 'https://www.zhipin.com'
  const BAR_ID = 'jobagent-session-bar'
  const STATUS_ID = 'jobagent-session-status-line'
  const M7_BAR_ID = 'jobagent-m7-status-bar'

  interface M7StatusState {
    phase: 'scanning' | 'complete' | 'failed'
    conversations: number
    matched: number
    skipped: number
    observed: number
    imported: number
    duplicates: number
    scroll_rounds: number
    ai_used: false
    detail?: string | null
  }

  function renderM7Status(state: M7StatusState) {
    document.getElementById(M7_BAR_ID)?.remove()
    const bar = document.createElement('div')
    bar.id = M7_BAR_ID
    const color = state.phase === 'complete' ? '#087f5b' : state.phase === 'failed' ? '#b42318' : '#0f3d91'
    bar.setAttribute('style', `position:fixed;top:0;left:0;right:0;z-index:2147483647;` +
      `background:${color};color:#fff;font:13px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",` +
      `"Microsoft YaHei",sans-serif;padding:8px 14px;box-shadow:0 1px 4px rgba(0,0,0,.3);`)
    const phase = state.phase === 'complete' ? '扫描完成' : state.phase === 'failed' ? '扫描停止' : '扫描中'
    bar.textContent = `JobAgent HR沟通 · ${phase} · 已处理 ${state.conversations} · ` +
      `已关联 ${state.matched} · 跳过 ${state.skipped} · 可见消息 ${state.observed} · ` +
      `新增 ${state.imported} · 重复 ${state.duplicates} · 滚动 ${state.scroll_rounds}/5` +
      (state.detail ? ` · ${state.detail}` : '')
    document.documentElement.appendChild(bar)
  }

  interface SessionOut {
    id: number
    status: 'running' | 'stopped'
    page_cap: number
    candidate_cap: number
    scroll_cap: number
    tab_origin: string
    pages_visited: number
    candidates_extracted: number
    scrolls_used: number
    approved_criteria: { task_name: string }
  }

  type Snapshot =
    | { kind: 'session'; session: SessionOut }
    | { kind: 'other_tab' }
    | { kind: 'none' }
    | { kind: 'unreachable' }

  interface DetectCandidate {
    title: string | null
    company: string | null
    salary_text: string | null
    city: string | null
    experience_text: string | null
    education_text: string | null
    source_url: string | null
    external_id: string | null
    matched_selectors: Record<string, string>
  }

  interface DetectResult {
    page_type: 'search' | 'detail' | 'unsupported'
    verification: boolean
    login_required?: boolean
    candidates: DetectCandidate[]
  }

  interface OpenCandidateResult {
    ok: boolean
    error?: string
  }

  interface NavigateResult {
    ok: boolean
    session?: SessionOut
    error?: string
  }

  interface MergedCandidate extends DetectCandidate {
    description: string | null
    missing_fields: string[]
    warnings: string[]
  }

  interface CaptureResult {
    status: 'ok' | 'not_loaded' | 'identity_mismatch' | 'verification' | 'login_required'
    candidate: MergedCandidate | null
  }

  interface PreviewSendResult {
    ok: boolean
    error?: string
    preview?: { new_count: number; duplicate_count: number; incomplete_count: number; excluded_count?: number }
  }

  interface ScrollResult {
    ok: boolean
    error?: string
  }

  //: Cards already opened (success) or already tried and failed this page
  //: load - the loop guard.
  const handledUrls = new Set<string>()

  //: M4d - every unique (query-stripped, per `extract.ts`'s `cleanUrl`)
  //: candidate URL observed so far this session, in first-seen order (a
  //: `Set` preserves insertion order in JS) - the session-local inventory
  //: each "向下滚动" step's before/after snapshot is compared against.
  const observedUrls = new Set<string>()

  const NEXT_BTN_ID = 'jobagent-next-candidate-btn'
  const CAPTURE_BTN_ID = 'jobagent-capture-detail-btn'
  const SCROLL_BTN_ID = 'jobagent-scroll-step-btn'

  //: Synchronous in-flight guards against a rapid double-click starting a
  //: second concurrent step - each set/checked before any `await`, so two
  //: clicks in the same tick can never both pass. `anyStepInFlight()` below
  //: additionally makes the three steps mutually exclusive: only one of
  //: opening a candidate, capturing or scrolling may run at a time, never
  //: two different steps interleaved.
  let navigationInFlight = false
  let captureInFlight = false
  let scrollInFlight = false

  function anyStepInFlight(): boolean {
    return navigationInFlight || captureInFlight || scrollInFlight
  }

  //: The exact card phase one (`openNextCandidate`) just opened - the only
  //: thing phase two (`captureSelectedDetail`) is allowed to verify against
  //: and merge with. Cleared the moment it is consumed (sent, or the
  //: session/page it belongs to is no longer valid) so a stale capture can
  //: never fire against a candidate that is no longer the one on screen.
  //:
  //: While this is set, "下一位候选人" must stay disabled - a real logged-in
  //: Chrome run found that cap math alone let repeated Next clicks overwrite
  //: this before it was ever captured, silently dropping every candidate but
  //: the last. `nextAllowed()` below is the one place that decision is made;
  //: every render and every state change after a capture attempt goes
  //: through it, and `openNextCandidateStep` also refuses outright - as
  //: defense in depth, not just a disabled attribute - to open a second
  //: candidate while one is still pending.
  let pendingCapture: DetectCandidate | null = null

  //: The most recently known session, kept in step with `pendingCapture` so
  //: `nextAllowed()` can be recomputed after a capture attempt without a
  //: full bar re-render (which would otherwise be the only place cap state
  //: is read).
  let lastSession: SessionOut | null = null

  /** Whether "下一位候选人" may open another candidate right now: never while
   * one is still pending capture, and never at or above the approved cap. */
  function nextAllowed(session: SessionOut | null): boolean {
    if (!session) return false
    if (pendingCapture) return false
    return session.candidates_extracted < session.candidate_cap
  }

  /** Whether "向下滚动" may scroll right now: same pending-capture rule as
   * every other action button, and never at or above the per-page cap. */
  function scrollAllowed(session: SessionOut | null): boolean {
    if (!session) return false
    if (pendingCapture) return false
    return session.scrolls_used < session.scroll_cap
  }

  function setNextButtonDisabled(disabled: boolean) {
    const btn = document.getElementById(NEXT_BTN_ID) as HTMLButtonElement | null
    if (btn) btn.disabled = disabled
  }

  function setCaptureButtonDisabled(disabled: boolean) {
    const btn = document.getElementById(CAPTURE_BTN_ID) as HTMLButtonElement | null
    if (btn) btn.disabled = disabled
  }

  function setScrollButtonDisabled(disabled: boolean) {
    const btn = document.getElementById(SCROLL_BTN_ID) as HTMLButtonElement | null
    if (btn) btn.disabled = disabled
  }

  /** Re-derive every action button's disabled state from current
   * `pendingCapture` + `lastSession`, for the moments (a capture attempt
   * finishing, a scroll confirm) that change one of those without
   * re-rendering the whole bar. The single source of truth for both is
   * `nextAllowed`/`scrollAllowed` above - nothing here re-derives the rule a
   * second, possibly-drifting way. */
  function refreshActionButtons() {
    setNextButtonDisabled(!nextAllowed(lastSession))
    setScrollButtonDisabled(!scrollAllowed(lastSession))
  }

  function currentOrigin(): string {
    return document.location.protocol + '//' + document.location.host
  }

  function askBackground<T>(message: Record<string, unknown>): Promise<T> {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(message, (response) => resolve((response || {}) as T))
      } catch {
        resolve({} as T)
      }
    })
  }

  function removeBar() {
    const bar = document.getElementById(BAR_ID)
    if (bar) bar.remove()
    const status = document.getElementById(STATUS_ID)
    if (status) status.remove()
  }

  // ------------------------------------------------------------------------
  // M4e/M4f: bounded automatic runner state panel (CLAUDE.md "Chrome
  // extension - M4 supervised navigation policy" M4e/M4f amendment, review
  // item 7 - "Add visible runner task state/counters and pause/resume/
  // cancel feedback to the existing page overlay as well as popup. No timer
  // polling."). This panel is driven entirely by messages `background.ts`
  // pushes right after every durable state write (`broadcastRunnerState`/
  // `broadcastRunnerCleared`) plus one read on load - it never polls, and it
  // never itself calls a browser API beyond rendering DOM it owns and
  // relaying a click to the background worker.

  const RUNNER_BAR_ID = 'jobagent-runner-bar'

  interface RunnerStateMessage {
    taskId: number
    phase: string
    scrollsUsed: number
    candidatesProcessed: number
    candidateCap: number
    candidatesAttempted: number
    observedCount: number
    newCount: number
    duplicateCount: number
    noNewRounds: number
    currentUrl: string | null
    visibleJobs: number
    importedJobs: number
    currentCandidate: string | null
    lastAction: string | null
    pausedReason: string | null
    city: string | null
    keyword: string | null
    updatedAt: string
    pauseRequested: boolean
    pauseConfirmed: boolean
    cancelRequested: boolean
    lastError: string | null
    pendingTerminal: { outcome: string; error?: string } | null
  }

  const PHASE_LABEL: Record<string, string> = {
    navigating: '正在打开搜索页',
    stabilizing: '正在等待页面稳定',
    processing: '正在处理候选人',
    scrolling: '正在滚动',
    stopped: '已暂停',
  }

  function removeRunnerBar() {
    const bar = document.getElementById(RUNNER_BAR_ID)
    if (bar) bar.remove()
  }

  function renderRunnerBar(state: RunnerStateMessage) {
    removeRunnerBar()
    const bar = document.createElement('div')
    bar.id = RUNNER_BAR_ID
    bar.setAttribute(
      'style',
      'position:fixed;top:64px;left:0;right:0;z-index:2147483645;' +
        'background:#0f3d91;color:#fff;font:12px/1.6 -apple-system,BlinkMacSystemFont,' +
        '"Segoe UI","Microsoft YaHei",sans-serif;padding:6px 12px;display:flex;' +
        'align-items:center;gap:10px;box-shadow:0 1px 4px rgba(0,0,0,.3);',
    )

    const label = document.createElement('span')
    label.style.flex = '1'
    const phaseText = PHASE_LABEL[state.phase] || state.phase
    const pendingText = state.pendingTerminal ? ` · 有未确认的结束操作（${state.pendingTerminal.outcome}）` : ''
    const errorText = state.lastError ? ` · ${state.lastError}` : ''
    const cityKeyword = [state.city, state.keyword].filter(Boolean).join(' · ')
    const candidateText = state.currentCandidate ? ` · 当前候选人 ${state.currentCandidate}` : ''
    const actionText = state.lastAction ? ` · 最近动作 ${state.lastAction}` : ''
    const pausedReasonText = state.pausedReason ? ` · 暂停原因 ${state.pausedReason}` : ''
    label.textContent =
      `JobAgent 自动运行（M4e/M4f）· 任务 #${state.taskId}${cityKeyword ? ' · ' + cityKeyword : ''} · ${phaseText} · ` +
      `当前网址 ${state.currentUrl || '（尚未导航）'} · ` +
      `已用名额 ${state.candidatesAttempted ?? '未知'}/${state.candidateCap ?? '未知'} · ` +
      `已处理候选人(jobs) ${state.candidatesProcessed} · 已导入(imported) ${state.importedJobs} · ` +
      `可见(visible) ${state.visibleJobs} · 已滚动(scroll) ${state.scrollsUsed}/5 · ` +
      `观察(observed) ${state.observedCount} · 新增(new) ${state.newCount} · ` +
      `重复(duplicate) ${state.duplicateCount} · 连续无新增(no-new) ${state.noNewRounds}` +
      candidateText +
      actionText +
      pausedReasonText +
      ` · 更新于 ${state.updatedAt}` +
      pendingText +
      errorText
    bar.appendChild(label)

    const pauseBtn = document.createElement('button')
    pauseBtn.type = 'button'
    pauseBtn.textContent = '暂停'
    pauseBtn.disabled = state.pauseRequested || state.cancelRequested
    pauseBtn.setAttribute(
      'style',
      'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
        'color:#fff;cursor:pointer;font:inherit;',
    )
    pauseBtn.addEventListener('click', () => void askBackground({ type: 'jobagent:runner-pause' }))
    bar.appendChild(pauseBtn)

    const resumeBtn = document.createElement('button')
    resumeBtn.type = 'button'
    resumeBtn.textContent = '恢复'
    resumeBtn.disabled = !state.pauseRequested || state.cancelRequested
    resumeBtn.setAttribute(
      'style',
      'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
        'color:#fff;cursor:pointer;font:inherit;',
    )
    resumeBtn.addEventListener('click', () => void askBackground({ type: 'jobagent:runner-resume' }))
    bar.appendChild(resumeBtn)

    const cancelBtn = document.createElement('button')
    cancelBtn.type = 'button'
    cancelBtn.textContent = '取消'
    cancelBtn.disabled = state.cancelRequested
    cancelBtn.setAttribute(
      'style',
      'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
        'color:#fff;cursor:pointer;font:inherit;',
    )
    cancelBtn.addEventListener('click', () => void askBackground({ type: 'jobagent:runner-cancel' }))
    bar.appendChild(cancelBtn)

    if (state.pendingTerminal) {
      const retryBtn = document.createElement('button')
      retryBtn.type = 'button'
      retryBtn.textContent = '重试结束'
      retryBtn.setAttribute(
        'style',
        'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
          'color:#fff;cursor:pointer;font:inherit;',
      )
      retryBtn.addEventListener('click', () => void askBackground({ type: 'jobagent:runner-retry-terminal' }))
      bar.appendChild(retryBtn)
    }

    document.documentElement.appendChild(bar)
  }

  function setStatusLine(text: string) {
    let el = document.getElementById(STATUS_ID)
    if (!el) {
      el = document.createElement('div')
      el.id = STATUS_ID
      el.setAttribute(
        'style',
        'position:fixed;top:34px;left:0;right:0;z-index:2147483646;' +
          'background:#2f6df6;color:#fff;font:11px/1.6 -apple-system,BlinkMacSystemFont,' +
          '"Segoe UI","Microsoft YaHei",sans-serif;padding:3px 12px;',
      )
      document.documentElement.appendChild(el)
    }
    el.textContent = text
  }

  /**
   * The one path every policy hard stop goes through: POST stop for real
   * (through background.ts, which clears the pointer only once the backend
   * confirms), remove the navigation controls so nothing is clickable
   * again, then show why - in that order, so the message is not wiped by
   * removing the bar.
   *
   * If the stop POST itself is not confirmed, the pointer (and likely the
   * backend session) are unchanged - claiming the session ended and pulling
   * the bar would strand the human with no Stop control at all. So on an
   * unconfirmed stop the bar stays, "下一位候选人" stays disabled (the
   * violation that triggered this is still real), and the status line says
   * so truthfully; only the still-present Stop button may retry - nothing
   * here retries automatically.
   */
  async function hardStop(reason: string, message: string) {
    // Whatever was pending is no longer safe to send under any outcome
    // below - a confirmed stop ends the session outright, and even an
    // unconfirmed one means the violation that triggered this is still real.
    pendingCapture = null
    setCaptureButtonDisabled(true)

    const result = await askBackground<{ ok: boolean }>({ type: 'jobagent:stop-session', reason })
    if (!result.ok) {
      setNextButtonDisabled(true)
      setScrollButtonDisabled(true)
      setStatusLine(message + '（后台未确认停止，会话可能仍在运行；请点击"停止会话"重试，不会自动重试。）')
      return
    }
    const bar = document.getElementById(BAR_ID)
    if (bar) bar.remove()
    setStatusLine(message)
  }

  function progressText(session: SessionOut): string {
    return (
      `JobAgent 受监督会话（M4b/M4c）· 任务「${session.approved_criteria.task_name}」· ` +
      `结果为连续滚动列表（无翻页控件）· ` +
      `本页滚动 ${session.scrolls_used}/${session.scroll_cap} · ` +
      `候选人 ${session.candidates_extracted}/${session.candidate_cap} · ` +
      `不会自动滚动/翻页/投递/发消息`
    )
  }

  function renderBar(
    session: SessionOut,
    onStop: () => void,
    onNext: () => void,
    onCapture: () => void,
    onScroll: () => void,
  ) {
    lastSession = session
    removeBar()
    const bar = document.createElement('div')
    bar.id = BAR_ID
    bar.setAttribute(
      'style',
      'position:fixed;top:0;left:0;right:0;z-index:2147483647;' +
        'background:#1a1d23;color:#fff;font:12px/1.6 -apple-system,BlinkMacSystemFont,' +
        '"Segoe UI","Microsoft YaHei",sans-serif;padding:6px 12px;display:flex;' +
        'align-items:center;gap:10px;box-shadow:0 1px 4px rgba(0,0,0,.3);',
    )

    const label = document.createElement('span')
    label.style.flex = '1'
    label.textContent = progressText(session)
    bar.appendChild(label)

    const nextBtn = document.createElement('button')
    nextBtn.type = 'button'
    nextBtn.id = NEXT_BTN_ID
    nextBtn.textContent = '下一位候选人'
    // Cap alone is not the whole story: a candidate still waiting on
    // "捕获详情" must keep this disabled too - see `nextAllowed`.
    nextBtn.disabled = !nextAllowed(session)
    nextBtn.setAttribute(
      'style',
      'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
        'color:#fff;cursor:pointer;font:inherit;',
    )
    nextBtn.addEventListener('click', () => void onNext())
    bar.appendChild(nextBtn)

    const captureBtn = document.createElement('button')
    captureBtn.type = 'button'
    captureBtn.id = CAPTURE_BTN_ID
    captureBtn.textContent = '捕获详情'
    // Only enabled once phase one has actually opened a candidate on this
    // page - a fresh bar render (session start, or a reconnect after this
    // script re-injects) never has anything pending to capture yet.
    captureBtn.disabled = !pendingCapture
    captureBtn.setAttribute(
      'style',
      'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
        'color:#fff;cursor:pointer;font:inherit;',
    )
    captureBtn.addEventListener('click', () => void onCapture())
    bar.appendChild(captureBtn)

    const scrollBtn = document.createElement('button')
    scrollBtn.type = 'button'
    scrollBtn.id = SCROLL_BTN_ID
    scrollBtn.textContent = '向下滚动'
    scrollBtn.disabled = !scrollAllowed(session)
    scrollBtn.setAttribute(
      'style',
      'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
        'color:#fff;cursor:pointer;font:inherit;',
    )
    scrollBtn.addEventListener('click', () => void onScroll())
    bar.appendChild(scrollBtn)

    const stopBtn = document.createElement('button')
    stopBtn.type = 'button'
    stopBtn.textContent = '停止会话'
    stopBtn.setAttribute(
      'style',
      'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
        'color:#fff;cursor:pointer;font:inherit;',
    )
    stopBtn.addEventListener('click', () => void onStop())
    bar.appendChild(stopBtn)

    document.documentElement.appendChild(bar)
  }

  async function stopFromBar() {
    const result = await askBackground<{ ok: boolean }>({
      type: 'jobagent:stop-session',
      reason: 'user_stop',
    })
    if (result.ok) {
      pendingCapture = null
      lastSession = null
      removeBar()
    }
    // If not confirmed, the session (and the pointer) are unchanged in the
    // worker - leave the bar as-is rather than guessing at a new state.
  }

  /**
   * The one explicit, human-triggered "Continue" step: read the page fresh,
   * hard-stop the whole session on every applicable policy violation, ask
   * the worker to *authorize* one click, only then click, then report the
   * click's real outcome. No part of this repeats automatically - each call
   * is exactly one human click, and every hard stop below ends the session
   * rather than merely refusing this one step.
   */
  async function openNextCandidate() {
    // Synchronous guard, checked and set before any `await`: a second click
    // arriving while a step is already running (rapid double-click, or a
    // click on a different action button) does no fetch and no click at
    // all - it is simply dropped. See `anyStepInFlight`.
    if (anyStepInFlight()) return
    navigationInFlight = true
    setNextButtonDisabled(true)
    try {
      await openNextCandidateStep()
    } finally {
      navigationInFlight = false
    }
  }

  async function openNextCandidateStep() {
    // Defense in depth, not just the disabled attribute: a real logged-in
    // Chrome run showed repeated Next clicks reaching here while a candidate
    // still awaited "捕获详情", silently overwriting it. Refuse outright -
    // no page read, no prepare, no click - rather than trust only the button
    // state a stray or racing click might bypass.
    if (pendingCapture) {
      setStatusLine('还有候选人等待"捕获详情"，请先完成捕获再打开下一位。')
      refreshActionButtons()
      return
    }

    if (currentOrigin() !== REQUIRED_ORIGIN) {
      await hardStop('wrong_origin', '当前标签页已不是 https://www.zhipin.com，会话已结束。')
      return
    }

    const detection = BossContentScript.handle({ type: BossContentScript.DETECT }) as {
      ok: boolean
      result?: DetectResult
    }
    if (!detection.ok || !detection.result) {
      // Inconclusive, not a confirmed violation - e.g. the page may still
      // be loading. Never treat "could not check" as a policy stop; the
      // human can just click again once the page has settled.
      setStatusLine('无法读取当前页面，请稍后重试。')
      setNextButtonDisabled(false)
      return
    }
    const page = detection.result

    if (page.login_required) {
      await hardStop('login_required', 'BOSS 尚未登录或登录已过期，请自行登录后重新开始会话。')
      return
    }

    // Hard stop: CAPTCHA / identity / security-risk interstitial, and the
    // same signal covers BOSS's "访问过于频繁" rate-limit wording - see
    // boss/selectors.ts VERIFICATION_HINTS.
    if (page.verification) {
      await hardStop(
        'verification',
        'BOSS 正在要求安全验证或提示访问过于频繁，请自行处理，会话已结束（不会重试）。',
      )
      return
    }
    // Hard stop: wrong page shape for this step.
    if (page.page_type !== 'search') {
      await hardStop('wrong_page', '当前不是搜索结果页，无法选择候选人，会话已结束。')
      return
    }

    const next = page.candidates.findIndex(
      (c) => c.title && c.source_url && !handledUrls.has(c.source_url),
    )
    // Hard stop: nothing left to open. BOSS's results here are one
    // continuous scroll list with no pagination control - "向下滚动" is the
    // only way more candidates can appear, and that is the human's own,
    // separate, bounded action.
    if (next === -1) {
      await hardStop(
        'no_candidates',
        '当前页面上没有更多可打开的候选人（可尝试"向下滚动"，不会自动查找），会话已结束。',
      )
      return
    }
    const targetUrl = page.candidates[next].source_url as string

    // Hard stop / loop guard: never re-open a URL already handled.
    if (handledUrls.has(targetUrl)) {
      await hardStop('loop_detected', '检测到重复的候选人链接，会话已结束（不会重复打开）。')
      return
    }

    setStatusLine('正在申请打开下一位候选人…')
    const prepared = await askBackground<NavigateResult>({
      type: 'jobagent:navigate-prepare',
      target: 'detail',
      pageUrl: targetUrl,
    })
    if (!prepared.ok) {
      // Hard stop: cap reached, session stopped, wrong origin, or this tab
      // does not own the session - click nothing at all.
      await hardStop(
        'prepare_denied',
        '未获得授权，不会点击，会话已结束：' + (prepared.error || '未知原因'),
      )
      return
    }

    const openResult = BossContentScript.handle({
      type: BossContentScript.OPEN_CANDIDATE,
      index: next,
    }) as { ok: boolean; result?: OpenCandidateResult }
    const clicked = !!openResult.ok && !!openResult.result?.ok

    handledUrls.add(targetUrl)

    const confirmed = await askBackground<NavigateResult>({
      type: 'jobagent:navigate-confirm',
      target: 'detail',
      pageUrl: targetUrl,
      outcome: clicked ? 'success' : 'failed',
      error: clicked ? null : openResult.result?.error || 'unknown',
    })

    if (!clicked) {
      // Hard stop: selector ambiguity / out-of-range / not clickable - the
      // click was confirmed failed above (never counted); now end the
      // session rather than let the human keep guessing.
      await hardStop(
        'click_failed',
        '无法唯一定位该候选人的链接，会话已结束（不会猜测点击）：' +
          (openResult.result?.error || 'unknown'),
      )
      return
    }

    if (!confirmed.ok) {
      // Hard stop: the click really happened, but the backend could not be
      // told, so the recorded candidate count can no longer be trusted -
      // stop rather than risk exceeding the approved cap unaccounted for.
      await hardStop(
        'confirm_failed',
        '候选人已打开，但未能确认写入后台，会话已结束（避免上限计数失真）。',
      )
      return
    }

    // Phase one is done: cache exactly the card just opened (never anything
    // re-derived later) so phase two has one unambiguous identity to verify
    // the pane against.
    pendingCapture = page.candidates[next]

    if (confirmed.session) {
      renderBar(
        confirmed.session,
        () => void stopFromBar(),
        () => void openNextCandidate(),
        () => void captureSelectedDetail(),
        () => void scrollOnePage(),
      )
    }
    setCaptureButtonDisabled(false)
    setStatusLine('已打开候选人详情页，请等右侧详情加载后点击"捕获详情"。')
  }

  /**
   * M4b phase two, the human's second click: read the pane the previous
   * click opened, verify it is really `pendingCapture` (never guessed - see
   * `boss/extract.ts`'s `captureAndMerge`), merge, and send only to the
   * existing loopback preview path. Nothing here imports; the human still
   * reviews and imports separately, exactly as `popup.ts` already works.
   */
  async function captureSelectedDetail() {
    if (anyStepInFlight()) return
    captureInFlight = true
    setCaptureButtonDisabled(true)
    try {
      await captureSelectedDetailStep()
    } finally {
      captureInFlight = false
    }
  }

  async function captureSelectedDetailStep() {
    const cached = pendingCapture
    if (!cached || !cached.source_url) {
      setStatusLine('没有待捕获的候选人，请先点击"下一位候选人"。')
      return
    }
    if (currentOrigin() !== REQUIRED_ORIGIN) {
      await hardStop('wrong_origin', '当前标签页已不是 https://www.zhipin.com，会话已结束。')
      return
    }

    // Confirm this tab still owns a running session before sending anything
    // - a stopped/reassigned session must never receive a capture.
    const snapshot = await askBackground<Snapshot>({ type: 'jobagent:session-snapshot' })
    if (snapshot.kind !== 'session') {
      pendingCapture = null
      setCaptureButtonDisabled(true)
      setStatusLine('会话已结束或不属于当前标签页，捕获已取消（不会发送）。')
      return
    }
    // Keep cap numbers fresh for `nextAllowed` even though capture itself
    // never changes them - the session-snapshot answer is the most current
    // one available.
    lastSession = snapshot.session

    const captured = BossContentScript.handle({
      type: BossContentScript.CAPTURE_DETAIL,
      canonicalUrl: cached.source_url,
      cachedCard: cached,
    }) as { ok: boolean; result?: CaptureResult }

    if (!captured.ok || !captured.result) {
      setStatusLine('读取详情面板失败，请重试。')
      setCaptureButtonDisabled(false)
      return
    }
    const result = captured.result

    if (result.status === 'login_required') {
      await hardStop('login_required', 'BOSS 尚未登录或登录已过期，请自行登录后重新开始会话。')
      return
    }
    if (result.status === 'verification') {
      await hardStop(
        'verification',
        'BOSS 正在要求安全验证或提示访问过于频繁，请自行处理，会话已结束（不会重试）。',
      )
      return
    }
    if (result.status === 'not_loaded') {
      // Inconclusive, not a confirmed violation - the pane may still be
      // loading. Let the human simply try again.
      setStatusLine('详情面板还没有加载完成，请稍候再点击"捕获详情"。')
      setCaptureButtonDisabled(false)
      return
    }
    if (result.status === 'identity_mismatch') {
      await hardStop(
        'identity_mismatch',
        '捕获到的详情与刚打开的候选人不一致，会话已结束（不会猜测匹配，也不会发送）。',
      )
      return
    }
    if (!result.candidate) {
      setStatusLine('捕获失败，请重试。')
      setCaptureButtonDisabled(false)
      return
    }

    setStatusLine('正在发送预览…')
    const sent = await askBackground<PreviewSendResult>({
      type: 'jobagent:send-preview',
      pageType: 'detail',
      pageUrl: result.candidate.source_url,
      candidate: result.candidate,
    })
    if (!sent.ok) {
      // Recoverable - the pane is presumably still visible, so the human
      // may just click "捕获详情" again; nothing here retries by itself.
      setStatusLine(
        '发送预览失败：' + (sent.error || '未知错误') + '（不会自动重试，可再次点击"捕获详情"）。',
      )
      setCaptureButtonDisabled(false)
      return
    }

    pendingCapture = null
    setCaptureButtonDisabled(true)
    // Only now - capture actually sent - may Next/scroll unlock again, and
    // then only if their own approved cap has not been reached.
    refreshActionButtons()
    const counts = sent.preview
    setStatusLine(
      counts
        ? `已发送预览：新 ${counts.new_count} / 重复 ${counts.duplicate_count}。什么都还没有保存，如需导入请另行打开弹窗操作。`
        : '已发送预览。什么都还没有保存。',
    )
  }

  /**
   * M4c (CLAUDE.md "Chrome extension - M4 supervised navigation policy",
   * explicitly authorized). One bounded scroll step, gated exactly like
   * `openNextCandidateStep`: refuse outright with a candidate pending,
   * re-check origin/page-shape/verification fresh, ask the backend to
   * *authorize* before performing anything, then report the real outcome.
   * This is the only way more candidates can appear on BOSS's continuous
   * results list - there is no pagination control to activate instead.
   */
  async function scrollOnePage() {
    if (anyStepInFlight()) return
    scrollInFlight = true
    setScrollButtonDisabled(true)
    try {
      await scrollOnePageStep()
    } finally {
      scrollInFlight = false
    }
  }

  async function scrollOnePageStep() {
    if (pendingCapture) {
      setStatusLine('还有候选人等待"捕获详情"，请先完成捕获再滚动。')
      refreshActionButtons()
      return
    }
    if (currentOrigin() !== REQUIRED_ORIGIN) {
      await hardStop('wrong_origin', '当前标签页已不是 https://www.zhipin.com，会话已结束。')
      return
    }

    const detection = BossContentScript.handle({ type: BossContentScript.DETECT }) as {
      ok: boolean
      result?: DetectResult
    }
    if (!detection.ok || !detection.result) {
      setStatusLine('无法读取当前页面，请稍后重试。')
      setScrollButtonDisabled(false)
      return
    }
    const page = detection.result

    if (page.login_required) {
      await hardStop('login_required', 'BOSS 尚未登录或登录已过期，请自行登录后重新开始会话。')
      return
    }
    if (page.verification) {
      await hardStop(
        'verification',
        'BOSS 正在要求安全验证或提示访问过于频繁，请自行处理，会话已结束（不会重试）。',
      )
      return
    }
    if (page.page_type !== 'search') {
      await hardStop('wrong_page', '当前不是搜索结果页，无法滚动，会话已结束。')
      return
    }

    setStatusLine('正在申请滚动…')
    const prepared = await askBackground<NavigateResult>({
      type: 'jobagent:navigate-prepare',
      target: 'scroll',
      pageUrl: null,
    })
    if (!prepared.ok) {
      // Hard stop: this page's scroll cap reached, session stopped, wrong
      // origin, or this tab does not own the session - scroll nothing.
      await hardStop(
        'prepare_denied',
        '未获得授权，不会滚动，会话已结束：' + (prepared.error || '未知原因'),
      )
      return
    }

    const scrolled = BossContentScript.handle({
      type: BossContentScript.SCROLL_STEP,
    }) as { ok: boolean; result?: ScrollResult }
    const succeeded = !!scrolled.ok && !!scrolled.result?.ok

    const confirmed = await askBackground<NavigateResult>({
      type: 'jobagent:navigate-confirm',
      target: 'scroll',
      pageUrl: null,
      outcome: succeeded ? 'success' : 'failed',
      error: succeeded ? null : scrolled.result?.error || 'unknown',
    })

    if (!succeeded) {
      // Hard stop: no unique scrollable container - the scroll was
      // confirmed failed above (never counted); end the session rather than
      // let the human keep guessing at a page that cannot be scrolled.
      await hardStop(
        'scroll_failed',
        '无法唯一定位可滚动的结果容器，会话已结束（不会猜测滚动）：' +
          (scrolled.result?.error || 'unknown'),
      )
      return
    }
    if (!confirmed.ok) {
      await hardStop(
        'confirm_failed',
        '已滚动，但未能确认写入后台，会话已结束（避免上限计数失真）。',
      )
      return
    }

    if (confirmed.session) {
      renderBar(
        confirmed.session,
        () => void stopFromBar(),
        () => void openNextCandidate(),
        () => void captureSelectedDetail(),
        () => void scrollOnePage(),
      )
    }
    // M4d inventory. Seed `observedUrls` with what was already rendered
    // *before* this scroll (the pre-scroll `page` snapshot captured above,
    // at the top of this step) - otherwise, on the very first scroll,
    // `observedUrls` starts empty and every already-visible card would be
    // misreported as "new" just because nothing had been recorded yet. Only
    // a canonical URL genuinely appended by this scroll step counts as new.
    for (const c of page.candidates) {
      if (c.source_url) observedUrls.add(c.source_url)
    }
    const beforeCount = observedUrls.size

    const after = BossContentScript.handle({ type: BossContentScript.DETECT }) as {
      ok: boolean
      result?: DetectResult
    }
    if (!after.ok || !after.result) {
      // Inconclusive, not a confirmed violation, and never a fabricated
      // zero-new-candidates result: the scroll itself already succeeded and
      // was accounted for above (session cap/state are correct) - only the
      // inventory count is unavailable this time. The human may click
      // "向下滚动" again once the page has settled; nothing here retries by
      // itself, and `observedUrls` is left exactly as seeded, never guessed.
      setStatusLine(
        '已滚动一步，但读取滚动后的页面失败，无法统计候选人数量（不会自动重试），可再次点击"向下滚动"。',
      )
      setScrollButtonDisabled(false)
      return
    }
    const afterPage = after.result

    // Same hard-stop policy as the pre-scroll check above, applied to what
    // scrolling revealed: a verification interstitial or a page that is no
    // longer a search results page must still end the session, never be
    // silently folded into an inventory count.
    if (afterPage.login_required) {
      await hardStop('login_required', '滚动后发现 BOSS 登录已失效，请自行登录后重新开始会话。')
      return
    }
    if (afterPage.verification) {
      await hardStop(
        'verification',
        'BOSS 正在要求安全验证或提示访问过于频繁，请自行处理，会话已结束（不会重试）。',
      )
      return
    }
    if (afterPage.page_type !== 'search') {
      await hardStop('wrong_page', '滚动后页面不再是搜索结果页，会话已结束。')
      return
    }

    const afterUrls = new Set<string>()
    for (const c of afterPage.candidates) {
      if (c.source_url) afterUrls.add(c.source_url)
    }
    let newCount = 0
    for (const url of afterUrls) {
      if (!observedUrls.has(url)) {
        observedUrls.add(url)
        newCount += 1
      }
    }
    const observedCount = afterUrls.size
    const duplicateCount = observedCount - newCount
    setStatusLine(
      newCount === 0
        ? `已滚动一步：本次未发现新候选人（可见 ${observedCount} 个，均已记录；会话累计 ${observedUrls.size} 个，此前 ${beforeCount} 个）。`
        : `已滚动一步：可见 ${observedCount} 个 · 新增 ${newCount} 个 · 重复 ${duplicateCount} 个，可用"下一位候选人"打开。`,
    )
  }

  async function init() {
    if (currentOrigin() !== REQUIRED_ORIGIN) return

    const snapshot = await askBackground<Snapshot>({ type: 'jobagent:session-snapshot' })
    if (snapshot.kind === 'session') {
      renderBar(
        snapshot.session,
        () => void stopFromBar(),
        () => void openNextCandidate(),
        () => void captureSelectedDetail(),
        () => void scrollOnePage(),
      )
    }
    // 'other_tab' | 'none' | 'unreachable' -> show nothing this pass.

    // One-time read on load, never polled - see the runner panel's own
    // banner above. If a runner is mid-run for this tab when the script
    // (re-)injects, its panel reappears immediately; later updates arrive
    // only as pushed `jobagent:runner-state` messages.
    const runnerStatus = await askBackground<{ ok: boolean; runner: RunnerStateMessage | null }>({
      type: 'jobagent:runner-status',
    })
    if (runnerStatus.ok && runnerStatus.runner) renderRunnerBar(runnerStatus.runner)
  }

  // Guard against a double registration if this script is ever injected
  // twice into the same isolated world.
  const flag = '__jobagentSessionBarInstalled'
  const globals = window as unknown as Record<string, boolean>
  if (!globals[flag]) {
    globals[flag] = true
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      const msg = (message || {}) as { type?: string; session?: SessionOut }
      if (msg.type === 'jobagent:session-started' && msg.session) {
        const session = msg.session
        renderBar(
          session,
          () => void stopFromBar(),
          () => void openNextCandidate(),
          () => void captureSelectedDetail(),
          () => void scrollOnePage(),
        )
        sendResponse({ ok: true })
        return false
      }
      if (msg.type === 'jobagent:session-stopped') {
        pendingCapture = null
        lastSession = null
        removeBar()
        sendResponse({ ok: true })
        return false
      }
      if (msg.type === 'jobagent:runner-state') {
        const runnerMsg = message as { state?: RunnerStateMessage | null }
        if (runnerMsg.state) renderRunnerBar(runnerMsg.state)
        else removeRunnerBar()
        sendResponse({ ok: true })
        return false
      }
      if (msg.type === 'jobagent:m7-status') {
        const m7 = message as { state?: M7StatusState }
        if (m7.state) renderM7Status(m7.state)
        sendResponse({ ok: true })
        return false
      }
      return false
    })
  }

  void init()
})()
