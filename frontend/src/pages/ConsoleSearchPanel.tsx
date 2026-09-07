import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, api } from '@/api/client'
import { Alert, Card, Modal } from '@/components/ui'
import { startFullSalaryBackfill } from '@/pages/salaryBackfill'
import { assessConsoleConnection, ConsoleConnectionError, consoleExtension,
  DEFAULT_BATCH_CANDIDATE_CAP, MAX_CONSOLE_BATCH_TASKS, selectBoundedPendingTasks } from '@/pages/consoleExtension'
import type { ConsoleAction, ConsoleReply } from '@/pages/consoleExtension'
import type { DirectionAnalysisPlan, DirectionChoice, ReadinessOut, SearchKeywordAnalytics, SearchPlanOptions, SearchPlanTask } from '@/types'

//: A run that stops says why in one code. Until now that code was only
//: visible behind 「显示细节」, so a stopped run looked like nothing happening
//: at all - which is exactly how it was reported.
const STOP_HINTS: Record<string, string> = {
  not_foreground: '上一次运行是因为 BOSS 标签页离开前台才停的。勾选「后台搜索」再开始，切走就不会中断。',
  content_unavailable: '页面没有响应扩展（多见于刚重新加载扩展之后）。刷新一下那个 BOSS 标签页再开始。',
  tab_lost: 'BOSS 标签页被关掉了。重新打开一个已登录的 BOSS 页面再开始。',
  wrong_origin: 'BOSS 标签页被导航到了别的网站，运行已停止。',
  login_required: 'BOSS 要求登录。请在标签页里登录后再开始；扩展不会替你登录。',
  verification: 'BOSS 出现了验证或风控页面。请自己处理完再开始；扩展不会绕过。',
  capture_timeout: '详情面板没有在限定次数内加载出来。',
  capture_timeout_skipped: '有岗位的详情面板一直没渲染出来，已跳过。后台标签页被别的窗口完全盖住时，Chrome 会停止渲染它 —— 把 BOSS 窗口留一条边露在外面（不必是当前窗口），就能一边搜一边干别的。',
  salary_not_foreground: '读取薪资需要 BOSS 标签页在前台，而运行时你切走了。勾选「后台搜索」后会直接跳过薪资 OCR（薪资留空，之后用薪资补全），不会再因此中断。',
  salary_tab_changed: '读取薪资时标签页状态变了，运行已停止。勾选「后台搜索」会跳过这一步。',
}

function stopHint(error: string | null | undefined): string | null {
  if (!error) return null
  //: A batch that cannot start its next task reports
  //: `next_task_start_failed:start-v3/<reason>` - the same foreground family,
  //: worth the same advice.
  if (/start-v3\/|next_task_start_failed/.test(error)) {
    return '批次停在了任务之间：下一个任务启动时 BOSS 标签页不在前台。勾选「后台搜索」再开始，整批都不会因为你切走而中断。'
  }
  const key = Object.keys(STOP_HINTS).find(code => error === code || error.startsWith(code + ':')
    || error.endsWith(':' + code))
  return key ? STOP_HINTS[key] : `上一次运行以「${error}」停止。`
}

function taskStatusLabel(task: SearchPlanTask): string {
  if (task.state === 'paused_login_required' || task.paused_reason === 'login_required') return '需要登录 BOSS'
  if (task.state === 'paused_verification' || task.paused_reason === 'verification') return '等待人工验证'
  if (task.paused_reason === 'background_not_rendering') return '窗口被盖住，已暂停'
  return task.state || '未开始'
}

/** A pasted block is split on the newline character itself; `trim()` on
 *  each line removes the CR that a Windows clipboard leaves behind. */
const SEGMENTS_KEY = 'jobagent.search.segments'
const BANDS_KEY = 'jobagent.search.salaryBands'
const AUTO_FILL_KEY = 'jobagent.search.autoFillSalary'
const TARGET_KEY = 'jobagent.search.targetCount'
const BACKGROUND_KEY = 'jobagent.search.background'
const CHOSEN_BANDS_KEY = 'jobagent.search.salaryBandsChosen'
const EXP_BANDS_KEY = 'jobagent.search.experienceBands'
const EXP_CHOSEN_KEY = 'jobagent.search.experienceChosen'
const EXP_SOURCE_KEY = 'jobagent.search.experienceBandsSource'

/** BOSS's own 经验 bands, read off a live results page on 2026-09-07:
 *  `<li ka="sel-job-rec-exp-104"> 1-3年<i class="ui-icon-check"></i></li>`.
 *
 *  A seed, not an authority. The reader overwrites it the moment it can read
 *  the live menu, and every band is displayed beside its code so a person can
 *  check one against BOSS's own URL. It exists because requiring a successful
 *  read before the control appears meant the control usually did not appear -
 *  and a filter nobody can select filters nothing.
 *
 *  If BOSS renumbers these, the live read corrects them; until it does, the
 *  displayed code is the thing to verify. 不限 is excluded on purpose: it is
 *  the unfiltered search. */
const SEEDED_EXPERIENCE_BANDS: SalaryBand[] = [
  { label: '经验不限', code: '101' },
  { label: '在校生', code: '108' },
  { label: '应届生', code: '102' },
  { label: '1年以内', code: '103' },
  { label: '1-3年', code: '104' },
  { label: '3-5年', code: '105' },
  { label: '5-10年', code: '106' },
  { label: '10年以上', code: '107' },
]

export interface SalaryBand { label: string; code: string }

/** Every access guarded: a private window or blocked site data throws rather
 *  than returning empty, and an unusable store must not stop the page. */
function loadBands(): SalaryBand[] {
  try {
    const raw = JSON.parse(window.localStorage.getItem(BANDS_KEY) || '[]')
    if (!Array.isArray(raw)) return []
    return raw.filter((row: unknown): row is SalaryBand =>
      Boolean(row) && typeof (row as SalaryBand).label === 'string'
      && typeof (row as SalaryBand).code === 'string')
  } catch { return [] }
}

function saveBands(bands: SalaryBand[]): void {
  try { window.localStorage.setItem(BANDS_KEY, JSON.stringify(bands)) } catch { /* ignore */ }
}

function loadChosenBands(): string[] {
  try {
    const raw = JSON.parse(window.localStorage.getItem(CHOSEN_BANDS_KEY) || '[]')
    return Array.isArray(raw) ? raw.filter((v: unknown) => typeof v === 'string') : []
  } catch { return [] }
}

function saveChosenBands(codes: string[]): void {
  try { window.localStorage.setItem(CHOSEN_BANDS_KEY, JSON.stringify(codes)) } catch { /* ignore */ }
}

/** Remember the search segments between visits.
 *
 * These are a standing preference - "always exclude 5-10年", "always split by
 * salary band" - not a per-run choice, and re-pasting them from BOSS every
 * time is exactly the friction that keeps them from being used at all.
 *
 * localStorage rather than the backend on purpose: this is one browser's
 * convenience, and `career_strategy.yaml` is never written automatically. Every
 * access is guarded - a private window or blocked site data throws rather than
 * returning empty, and an unusable store must not stop the page rendering.
 */
function loadSegments(): string {
  try {
    return window.localStorage.getItem(SEGMENTS_KEY) ?? ''
  } catch {
    return ''
  }
}

function saveSegments(value: string): void {
  try {
    if (value.trim()) window.localStorage.setItem(SEGMENTS_KEY, value)
    else window.localStorage.removeItem(SEGMENTS_KEY)
  } catch {
    /* Not being able to remember them is not a reason to stop using them. */
  }
}

const NEWLINE = String.fromCharCode(10)

export default function ConsoleSearchPanel({ onSelect }: { onSelect: (id: number) => void }) {
  const [city, setCity] = useState('')
  const [cities, setCities] = useState<string[]>([])
  const [keyword, setKeyword] = useState('')
  const [searchOptions, setSearchOptions] = useState<SearchPlanOptions | null>(null)
  const [hasResume, setHasResume] = useState(false)
  const [hasRoles, setHasRoles] = useState(false)
  const [setupLoaded, setSetupLoaded] = useState(false)
  const [cap, setCap] = useState(3)
  //: Remembered per browser, so a number typed once stays typed. Defaults to
  //: the opened-detail ceiling: asking for fewer only makes a direction stop
  //: earlier, and the ceiling is what limits the work either way.
  const [targetCount, setTargetCount] = useState(() => {
    try {
      const stored = Number(window.localStorage.getItem(TARGET_KEY))
      if (Number.isInteger(stored) && stored >= 1 && stored <= 60) return stored
    } catch { /* private window or blocked site data */ }
    return 60
  })
  const [batchSize, setBatchSize] = useState(2)
  const [batchCap, setBatchCap] = useState(DEFAULT_BATCH_CANDIDATE_CAP)
  const [tasks, setTasks] = useState<SearchPlanTask[]>([])
  const [portfolioTaskIds, setPortfolioTaskIds] = useState<number[]>([])
  const [keywordStats, setKeywordStats] = useState<SearchKeywordAnalytics | null>(null)
  //: Why these directions were chosen. The search runs without a second
  //: confirmation, so this is the after-the-fact explanation.
  const [chosen, setChosen] = useState<DirectionChoice[]>([])
  const [chosenNotes, setChosenNotes] = useState<string[]>([])
  //: The AI's read of the résumé. Loaded on open because reading is free; the
  //: analysis itself only runs on an explicit click.
  //: Let the search keep going while the BOSS tab sits behind other
  //: windows. Authorized for search only - applying stays foreground and
  //: per-job confirmed. Off unless chosen, every run.
  //: BOSS's own salary bands, read once off a page the user had open. Kept
  //: in this browser like the segment box - it is one machine's convenience,
  //: and `career_strategy.yaml` is never written automatically.
  const [salaryBands, setSalaryBands] = useState<SalaryBand[]>(() => loadBands())
  // BOSS's own 经验 bands, read off the results page the same way the salary
  // ones are. Unlike salary, exactly one may be chosen: it constrains every
  // search rather than splitting the run into segments.
  const [expBands, setExpBands] = useState<SalaryBand[]>(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(EXP_BANDS_KEY) || '[]')
      if (Array.isArray(stored) && stored.length) return stored
    } catch { /* fall through to the seed */ }
    return SEEDED_EXPERIENCE_BANDS
  })
  // Whether what is on screen came from BOSS itself or from the seed above.
  const [expFromSite, setExpFromSite] = useState(() => {
    try { return window.localStorage.getItem(EXP_SOURCE_KEY) === 'site' } catch { return false }
  })
  // Several bands at once, because BOSS itself accepts them as one
  // comma-separated value (`experience=101,104`) - 经验不限 alongside 1-3年 is
  // one search, not two, and it is what a person actually wants: postings that
  // ask for nothing are as reachable as postings that ask for a year or two.
  const [expChosen, setExpChosen] = useState<string[]>(() => {
    try {
      const raw = window.localStorage.getItem(EXP_CHOSEN_KEY) || ''
      return raw ? raw.split(',').filter(Boolean) : []
    } catch { return [] }
  })
  const [expBusy, setExpBusy] = useState(false)
  const expProbed = useRef(false)
  const [chosenBands, setChosenBands] = useState<string[]>(() => loadChosenBands())
  const [bandBusy, setBandBusy] = useState(false)
  //: Reading the bands is a pure DOM read on a tab the human already has open,
  //: so there is no reason to make them press a button for it. Tried once per
  //: page load, only while nothing is running, and only when none are stored.
  //: Silent on failure - BOSS may keep the options out of the DOM until the
  //: menu is opened, and that is what the button is still there for.
  const bandsProbed = useRef(false)
  //: How many stored jobs still have no salary. A search cannot read most of
  //: them - BOSS renders the salary in a private-use font in the results list
  //: and in the pane beside it, while the standalone detail page shows it
  //: plainly - so the backfill is a second pass by design, not a bug. What was
  //: wrong is that its prompt lived further down the console, where a run that
  //: had just finished did not point at it.
  const [missingSalaries, setMissingSalaries] = useState<number | null>(null)
  //: Collection outran analysis: 178 jobs sat unanalysed after one day of
  //: searching, which makes them invisible - nobody knows which are worth
  //: applying to. Counting is free and calls no model; spending still happens
  //: only in the jobs page, behind its own plan-then-confirm gate showing the
  //: exact number of calls.
  const [unanalysed, setUnanalysed] = useState<number | null>(null)
  //: Every "the button does nothing" this project produced for a new pair of
  //: hands was one unmet prerequisite that the page never named.
  const [readiness, setReadiness] = useState<ReadinessOut | null>(null)
  //: Clearing the backlog used to mean four trips through the jobs page: the
  //: per-run cap is 50, and 178 jobs sat unanalysed for two days because of
  //: it. One confirmation, naming the whole cost, then the same capped
  //: endpoint called until the backlog is gone.
  //: Collecting and analysing produce a pile nobody looks at unless the page
  //: that finished the work says where the next decision is.
  const [queueWaiting, setQueueWaiting] = useState<number | null>(null)
  const [analyseConfirm, setAnalyseConfirm] = useState<number | null>(null)
  const [analyseBusy, setAnalyseBusy] = useState(false)
  const [analyseNote, setAnalyseNote] = useState('')
  const [salaryBusy, setSalaryBusy] = useState(false)
  const [salaryNote, setSalaryNote] = useState('')
  //: BOSS renders the salary in a private-use font everywhere a search can
  //: see it, so a newly collected job almost always arrives without one and
  //: the backfill is a second pass by design. Having to remember that pass
  //: after every single search is the part that was not by design. Authorized
  //: 2026-09-05 to chain it automatically; the checkbox turns it back off, and
  //: every stop condition of the backfill itself is unchanged.
  const [autoFillSalary, setAutoFillSalary] = useState(() => {
    try { return window.localStorage.getItem(AUTO_FILL_KEY) !== 'off' } catch { return true }
  })
  //: One chain per finished batch. Without this the effect below would restart
  //: the backfill on every status refresh while the count stays above zero.
  const autoFilled = useRef(false)
  //: Let the search keep going while the BOSS tab sits behind other windows.
  //: Authorized for search only - applying stays foreground and per-job
  //: confirmed. Off until chosen once, then remembered: a run is hours long
  //: now, and a choice that silently reset itself failed a run ten seconds in
  //: with `not_foreground` while the option sat collapsed out of sight.
  const [runInBackground, setRunInBackground] = useState(() => {
    try { return window.localStorage.getItem(BACKGROUND_KEY) === 'on' } catch { return false }
  })
  const [filterBusy, setFilterBusy] = useState(false)
  const [filterNote, setFilterNote] = useState('')
  const [aiPlan, setAiPlan] = useState<DirectionAnalysisPlan | null>(null)
  const [aiBusy, setAiBusy] = useState(false)
  const [aiError, setAiError] = useState<string | null>(null)
  //: BOSS search URLs the user built in their own browser. Each one narrows a
  //: search so its top results differ - the only way to reach past the first
  //: page without moving a ceiling. One per line.
  const [filterUrls, setFilterUrls] = useState(loadSegments)
  const filterLines = filterUrls.split(NEWLINE).map(line => line.trim()).filter(Boolean)
  //: Echoed back so a paste that contributed nothing is visible rather than
  //: silently ignored. Display only - the backend parses these again, with the
  //: whitelist and validation that actually decide what gets used.
  const segmentSummary = filterLines
    .map(line => {
      try {
        const params = new URL(line).searchParams
        const kept = [...params.entries()]
          .filter(([key]) => key !== 'city' && key !== 'query')
          .map(([key, value]) => `${key}=${value}`)
        return kept.length ? kept.join('、') : '（没有筛选参数）'
      } catch {
        return '（不是有效链接）'
      }
    })
    .join(' ｜ ')
  const [selected, setSelected] = useState<number | null>(null)
  const [connection, setConnection] = useState<ConsoleReply | null>(null)
  const [backendReady, setBackendReady] = useState(false)
  const [backendDetail, setBackendDetail] = useState('检查中')
  const [planDetail, setPlanDetail] = useState('检查中')
  const [bridgeDetail, setBridgeDetail] = useState('检查中')
  const [diagnosticCode, setDiagnosticCode] = useState('checking')
  const [checkedAt, setCheckedAt] = useState('尚未完成')
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [confirmation, setConfirmation] = useState<{
    action: 'start' | 'resume'; taskId: number; city: string | null; keywords: string | null; cap: number
  } | null>(null)
  const [batchConfirmation, setBatchConfirmation] = useState<{
    tasks: Array<{ id: number; city: string | null; keywords: string | null }>; cap: number
  } | null>(null)
  const admission = useRef(false)
  const alive = useRef(true)
  const refreshSequence = useRef(0)
  const task = tasks.find(row => row.id === selected)
  const activePortfolioIds = connection?.batch?.taskIds?.length ? connection.batch.taskIds : portfolioTaskIds
  const portfolioTasks = activePortfolioIds
    .map(id => tasks.find(row => row.id === id))
    .filter((row): row is SearchPlanTask => Boolean(row))
  const portfolioCities = [...new Set(portfolioTasks.map(row => row.city).filter(Boolean))]
  const portfolioDirections = [...new Set(portfolioTasks.map(row => row.keywords).filter(Boolean))]
  const portfolioCompleted = portfolioTasks.filter(row => row.state === 'completed').length
  const portfolioObserved = portfolioTasks.reduce((total, row) => total + row.observed_jobs, 0)
  const portfolioImported = portfolioTasks.reduce((total, row) => total + row.imported_jobs, 0)

  const loadSetup = useCallback(async () => {
    try {
      const [options, strategyResponse] = await Promise.all([
        api.getSearchPlanOptions(AbortSignal.timeout(5000)),
        api.getStrategy(),
      ])
      let resumeAvailable = true
      try { await api.getActiveResume() }
      catch (err) {
        if (err instanceof ApiError && err.status === 404) resumeAvailable = false
        else throw err
      }
      const configuredCities = strategyResponse.strategy.target_cities
        .filter(value => options.supported_cities.includes(value))
        .slice(0, options.max_selected_cities)
      const firstCity = configuredCities[0] || options.supported_cities[0] || ''
      const firstRole = strategyResponse.strategy.preferred_roles.find(value => value.trim()) || ''
      setSearchOptions(options)
      setCities(configuredCities)
      setCity(firstCity)
      setKeyword(firstRole)
      setHasResume(resumeAvailable)
      setHasRoles(Boolean(firstRole))
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载个人搜索设置失败')
    } finally { setSetupLoaded(true) }
  }, [])

  const refresh = useCallback(async () => {
    const sequence = ++refreshSequence.current
    setChecking(true)
    setBackendReady(false) // stale success must not enable start during a new probe
    const [health, plan, bridge] = await Promise.allSettled([
      api.health(AbortSignal.timeout(5000)), api.listSearchPlan(AbortSignal.timeout(5000)), consoleExtension('status'),
    ])
    if (!alive.current || sequence !== refreshSequence.current) return
    const healthy = health.status === 'fulfilled' && health.value?.status === 'ok' && health.value.database === 'ok'
    const planOk = plan.status === 'fulfilled' && Array.isArray(plan.value?.items)
    setBackendReady(healthy && planOk)
    setBackendDetail(healthy ? '后端和数据库正常' : health.status === 'fulfilled'
      ? '健康检查未通过；不是扩展连接错误' : '健康检查失败或超时；请检查本机 8000 服务')
    setPlanDetail(planOk ? '任务 API 正常' : '任务 API 不可用；请检查后端版本或服务')
    if (planOk && plan.status === 'fulfilled') setTasks(plan.value.items)
    if (bridge.status === 'fulfilled') {
      const assessment = assessConsoleConnection(bridge.value)
      setConnection(assessment.ready ? bridge.value : null)
      setBridgeDetail(assessment.detail)
      setDiagnosticCode(assessment.ready ? 'ready' : bridge.value.code || 'handshake_incompatible')
    } else {
      setConnection(null)
      setBridgeDetail(bridge.reason instanceof Error ? bridge.reason.message : '扩展状态未知')
      setDiagnosticCode(bridge.reason instanceof ConsoleConnectionError ? bridge.reason.code : 'unknown')
    }
    setChecking(false)
    setCheckedAt(new Date().toLocaleTimeString())
    setError('')
  }, [])

  // Free, local, read-only: it aggregates analyses that already exist, so it
  // costs nothing to show next to the keyword picker. No model call.
  useEffect(() => {
    let alive = true
    void api.searchKeywordAnalytics()
      .then(value => { if (alive) setKeywordStats(value) })
      .catch(() => { if (alive) setKeywordStats(null) })
    // Reading the direction plan calls no model, so it is safe on open. It
    // reports whether an analysis is cached and what a new one would cost.
    void api.getDirectionPlan()
      .then(value => { if (alive) setAiPlan(value) })
      .catch(() => { if (alive) setAiPlan(null) })
    return () => { alive = false }
  }, [])

  /** Take the filters from a BOSS tab the human already has open.
   *
   * They choose the filters on BOSS the way they always would; this removes
   * only the part where they copy the address bar back into JobAgent, which is
   * the step that kept getting skipped. Read-only: the extension reports one
   * tab's URL parameters and touches nothing.
   */
  const loadReadiness = useCallback(async () => {
    try { setReadiness(await api.getReadiness()) } catch { setReadiness(null) }
  }, [])

  const loadQueue = useCallback(async () => {
    try { setQueueWaiting((await api.applicationQueue({ limit: 1 })).total) }
    catch { setQueueWaiting(null) }
  }, [])

  const loadUnanalysed = useCallback(async () => {
    try {
      setUnanalysed((await api.listJobs({ analyzed: false, limit: 1 })).total)
    } catch {
      setUnanalysed(null)
    }
  }, [])

  const loadMissingSalaries = useCallback(async () => {
    try {
      setMissingSalaries((await api.getSalaryBackfillPlan()).salary_missing)
    } catch {
      setMissingSalaries(null)
    }
  }, [])

  async function analyseBacklog(total: number) {
    if (analyseBusy) return
    setAnalyseConfirm(null)
    setAnalyseBusy(true)
    let done = 0
    let failed = 0
    try {
      // The endpoint caps each call at MAX_ANALYSES_PER_RUN and, with no ids,
      // takes the unanalysed jobs itself - so this is the same gate, called
      // until there is nothing left, not a way around it.
      for (let round = 0; round < 40; round += 1) {
        setAnalyseNote(`正在分析…已完成 ${done}/${total}`)
        const result = await api.analyzeBatch(false)
        done += result.analyzed + result.cached
        failed += result.failed
        // Nothing moved: either the backlog is gone or every job in this round
        // failed. Either way, stop rather than loop.
        if (result.analyzed + result.cached === 0) break
      }
      setAnalyseNote(
        failed > 0
          ? `完成 ${done} 个，失败 ${failed} 个（不会自动重试，可再点一次）。`
          : `完成：${done} 个岗位已分析。`,
      )
      await loadUnanalysed()
    } catch (err) {
      setAnalyseNote(err instanceof ApiError ? err.message : '批量分析失败')
    } finally { setAnalyseBusy(false) }
  }

  async function fillSalaries(automatic = false) {
    if (salaryBusy) return
    setSalaryBusy(true); setSalaryNote('')
    try {
      const result = await startFullSalaryBackfill(runInBackground)
      setSalaryNote(automatic ? '搜索完成，已自动开始补全薪资。' + result.message : result.message)
      await loadMissingSalaries()
    } catch (err) {
      setSalaryNote(err instanceof ApiError ? err.message : '启动薪资补全失败。')
    } finally { setSalaryBusy(false) }
  }

  useEffect(() => {
    if (bandsProbed.current || salaryBands.length || bandBusy) return
    if (!connection?.capabilities?.includes('read-salary-filter-v1')) return
    if (connection.runner || connection.batch?.state === 'running') return
    bandsProbed.current = true
    void (async () => {
      try {
        const reply = await consoleExtension('read-salary-filter')
        const bands = (reply.options ?? []).filter(row => row.label !== '不限')
        if (reply.ok && bands.length) { setSalaryBands(bands); saveBands(bands) }
      } catch { /* silent: the button is the explicit path */ }
    })()
  }, [connection, salaryBands.length, bandBusy])

  // Its own effect on purpose. Hanging this off the salary probe meant every
  // salary-side condition silently blocked it - a stored set of salary bands
  // was enough to make the experience menu never be read at all, which is
  // exactly what happened on 2026-09-07: the picker simply never appeared.
  useEffect(() => {
    // Not gated on `expBands.length` any more: they start seeded, so waiting
    // for them to be empty would mean never refreshing them from the site.
    if (expProbed.current || expFromSite || expBusy) return
    if (!connection?.capabilities?.includes('read-experience-filter-v1')) return
    if (connection.runner || connection.batch?.state === 'running') return
    expProbed.current = true
    void (async () => {
      try { await readExperienceBands(true) } catch { /* the button is the explicit path */ }
    })()
  }, [connection, expFromSite, expBusy])

  useEffect(() => {
    if (!autoFillSalary || salaryBusy || autoFilled.current) return
    const running = connection?.batch?.state === 'running' || connection?.batch?.state === 'paused'
    if (connection?.runner || running) { autoFilled.current = false; return }
    if (connection?.batch?.state !== 'completed' || !missingSalaries) return
    autoFilled.current = true
    void fillSalaries(true)
  }, [autoFillSalary, salaryBusy, connection, missingSalaries])

  async function readExperienceBands(silent = false) {
    if (expBusy) return
    setExpBusy(true)
    if (!silent) setFilterNote('')
    try {
      const reply = await consoleExtension('read-experience-filter')
      if (!reply.ok || !reply.options?.length) {
        if (!silent) setFilterNote(reply.error || '没有读到经验档位。')
        return
      }
      // 不限 is a real BOSS option, and choosing it is the unfiltered search.
      const bands = reply.options.filter(row => row.label !== '不限')
      setExpBands(bands)
      setExpFromSite(true)
      try {
        window.localStorage.setItem(EXP_BANDS_KEY, JSON.stringify(bands))
        window.localStorage.setItem(EXP_SOURCE_KEY, 'site')
      } catch { /* ignore */ }
      if (!silent) {
        setFilterNote(`读到 ${bands.length} 个经验档位。请核对档位和代码是否和 BOSS 页面一致，再选择。`)
      }
    } catch (err) {
      if (!silent) setFilterNote(err instanceof Error ? err.message : '读取失败')
    } finally { setExpBusy(false) }
  }

  async function readSalaryBands() {
    if (bandBusy) return
    setBandBusy(true); setFilterNote('')
    try {
      const reply = await consoleExtension('read-salary-filter')
      if (!reply.ok || !reply.options?.length) {
        setFilterNote(reply.error || '没有读到薪资档位。')
        return
      }
      // 不限 is a real BOSS option and never a useful segment - a segment that
      // filters nothing is the unfiltered search again.
      const bands = reply.options.filter(row => row.label !== '不限')
      setSalaryBands(bands)
      saveBands(bands)
      setFilterNote(`读到 ${bands.length} 个薪资档位。请核对下面的档位与代码是否和 BOSS 页面一致，再勾选要拆分的档位。`)
    } catch (err) {
      setFilterNote(err instanceof Error ? err.message : '读取失败')
    } finally { setBandBusy(false) }
  }

  function toggleBand(code: string) {
    setChosenBands(current => {
      const next = current.includes(code) ? current.filter(v => v !== code) : [...current, code]
      saveChosenBands(next)
      return next
    })
  }

  async function readFiltersFromBoss() {
    if (filterBusy) return
    setFilterBusy(true); setFilterNote('')
    try {
      const reply = await consoleExtension('read-search-filters')
      if (!reply.ok || !reply.filterUrl) {
        setFilterNote(reply.error || '没有读到筛选条件。')
        return
      }
      const next = reply.filterUrl
      // Appended, not replaced: several segments is the point - one per salary
      // band, say - and silently discarding the ones already set would undo
      // work the user did on BOSS.
      const already = filterLines.includes(next)
      if (already) {
        setFilterNote('这条筛选已经在列表里了。')
        return
      }
      const merged = [...filterLines, next].join(NEWLINE)
      setFilterUrls(merged)
      saveSegments(merged)
      setFilterNote('已读取当前 BOSS 标签页的筛选条件。')
    } catch (err) {
      setFilterNote(err instanceof ApiError ? err.message : '读取失败。')
    } finally {
      setFilterBusy(false)
    }
  }

  /** The only place this feature spends money, and only on a click. */
  async function analyzeDirections(force: boolean) {
    if (aiBusy) return
    setAiBusy(true); setAiError(null)
    try {
      setAiPlan(await api.analyzeDirections(force))
      // The chosen-directions table was produced by the previous ranking and
      // is now stale, so it is cleared rather than left showing old reasons.
      setChosen([]); setChosenNotes([])
    } catch (err) {
      setAiError(err instanceof ApiError ? err.message : 'AI 方向分析失败')
    } finally {
      setAiBusy(false)
    }
  }

  // Only status reads on mount/visibility or an explicit refresh. No task auto-start.
  useEffect(() => {
    alive.current = true
    void loadSetup()
    void refresh()
    void loadMissingSalaries()
    void loadUnanalysed()
    void loadReadiness()
    void loadQueue()
    const visible = () => { if (document.visibilityState === 'visible') void refresh() }
    document.addEventListener('visibilitychange', visible)
    return () => { alive.current = false; refreshSequence.current++; document.removeEventListener('visibilitychange', visible) }
  }, [loadSetup, refresh, loadMissingSalaries, loadUnanalysed, loadReadiness, loadQueue])

  async function prepare(event: React.FormEvent) {
    event.preventDefault()
    if (admission.current || !city.trim() || !keyword.trim()) return
    admission.current = true; setBusy(true); setError(''); setMessage('')
    try {
      await api.generateSearchPlan(city.trim(), keyword.trim())
      const plan = await api.listSearchPlan()
      setTasks(plan.items)
      const found = plan.items.find(row => row.city === city.trim() && row.keywords === keyword.trim())
      if (found) { setSelected(found.id); onSelect(found.id) }
      setMessage('搜索计划已准备；尚未访问 BOSS。请确认下方任务后点击开始。已结束的同条件任务不会重置。')
    } catch (err) { setError(err instanceof Error ? err.message : '准备任务失败') }
    finally { admission.current = false; setBusy(false) }
  }

  async function prepareQuick(event: React.FormEvent) {
    event.preventDefault()
    if (admission.current || !cities.length) return
    if (!Number.isInteger(targetCount) || targetCount < 1 || targetCount > 60) {
      setError('岗位数量必须是 1–60 的整数。')
      return
    }
    // The loaded extension is the one that enforces this, and a build older
    // than the page refuses the number without ever naming it.
    const workerTarget = connection?.limits?.target
    if (workerTarget !== undefined && targetCount > workerTarget) {
      setError(`浏览器里的扩展是旧版本，每个方向最多只接受 ${workerTarget} 个。`
        + '请在 chrome://extensions 重新加载扩展，或把数量调到该上限以内。')
      return
    }
    if (!connection?.capabilities?.includes('console-batch-v1')) {
      setError('当前扩展版本不支持综合搜索，请更新扩展后刷新连接。')
      return
    }
    admission.current = true; setBusy(true); setError(''); setMessage('')
    try {
      const prepared = await api.prepareResumeSearch(
        cities, targetCount, filterLines, activeBandCodes, activeExpCode)
      const nextTasks = prepared.tasks
      if (!nextTasks.length) throw new Error('没有生成可执行的搜索任务。')
      // Pydantic ignores fields it does not know, so a backend still running
      // code from before `experience_code` existed dropped it in silence -
      // three runs went out unfiltered while the console showed the bands
      // selected, and the obvious-looking conclusion ("they never ticked it")
      // was wrong every time. The returned `search_url` is what the runner
      // will actually navigate to, so it can simply be checked.
      if (activeExpCode && !nextTasks.some(row => (row.search_url || '').includes('experience='))) {
        throw new Error(
          '已选择经验档，但后端建出来的搜索没有带上它——后端进程多半还是改动前的版本。'
          + '请重启后端（scripts\dev.ps1 start）后重试；这批任务不带筛选，建议删掉重建。',
        )
      }
      const nextIds = new Set(nextTasks.map(row => row.id))
      setTasks(current => [...current.filter(row => !nextIds.has(row.id)), ...nextTasks])
      setSelected(nextTasks[0].id)
      setPortfolioTaskIds(nextTasks.map(row => row.id))
      setCap(targetCount)
      onSelect(nextTasks[0].id)
      setBatchSize(nextTasks.length)
      setBatchCap(targetCount)
      setBatchConfirmation({
        tasks: nextTasks.map(row => ({ id: row.id, city: row.city, keywords: row.keywords })),
        cap: targetCount,
      })
      setChosen(prepared.directions || [])
      setChosenNotes(prepared.direction_notes || [])
      const directions = [...new Set(nextTasks.map(row => row.keywords).filter(Boolean))]
      setMessage(`已根据当前简历「${prepared.active_resume_name}」准备综合搜索：`
        + `${cities.length} 个城市、${directions.length} 个方向，共 ${nextTasks.length} 个有限搜索单元。`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '准备简历匹配搜索失败')
    } finally {
      admission.current = false; setBusy(false)
    }
  }

  async function command(action: ConsoleAction, approved = false) {
    if (admission.current || !task) return
    if ((action === 'start' || action === 'resume') && !connection && !checking) {
      setError(`扩展状态不可用，未发送请求：${bridgeDetail || '未知原因'}。可在「高级设置与诊断」里刷新连接。`)
      return
    }
    if ((action === 'start' || action === 'resume') && (!backendReady || !connection || checking)) return
    // Call the bridge immediately in the trusted click turn: no async preparation
    // before browser handoff, and never include a paid match approval.
    if (action === 'start' && (!Number.isInteger(cap) || cap < 1 || cap > 60)) {
      setError('候选上限必须是 1–60 的整数。'); return
    }
    if (action === 'start' || action === 'resume') {
      if (!approved) {
        setConfirmation({ action, taskId: task.id, city: task.city, keywords: task.keywords, cap })
        return // no native JS dialog and no browser/task operation until the second click
      }
      const valid = confirmation?.action === action && confirmation.taskId === task.id
        && confirmation.city === task.city && confirmation.keywords === task.keywords
        && confirmation.cap === cap
        && (action === 'start' ? task.state === 'pending' && !connection?.runner
          : connection?.runner?.taskId === task.id && connection.runner.paused && !connection.runner.paid)
      setConfirmation(null)
      if (!valid) { setError('任务或运行状态已变化，请重新确认；尚未发送启动请求。'); return }
    }
    admission.current = true; setBusy(true); setError(''); setMessage('')
    try {
      // Only a start carries the choice. A resume restores whatever the run
      // was started as, from the run's own pointer.
      const result = await consoleExtension(
        action, task.id, cap, undefined, undefined, undefined, undefined,
        action === 'start' ? runInBackground : undefined,
      )
      if (!result.ok) throw new Error(result.error || '未确认执行，请刷新状态。')
      setMessage(runInBackground && action === 'start'
        ? '请求已接收（后台搜索）。可以切到别的窗口，但别把 BOSS 窗口最小化或完全盖住，否则 BOSS 不再加载新岗位；关闭它或离开 BOSS 仍会停止。进度回到本页查看。'
        : '请求已接收。BOSS 页顶部显示执行进度；返回控制台会刷新后端状态。切换离开 BOSS 会停止后续浏览器动作。')
      await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : '请求失败') }
    finally { admission.current = false; setBusy(false) }
  }

  function prepareBatch() {
    if (admission.current || busy || checking || !backendReady || !connection
      || connection.runner || connection.batch?.state === 'running' || connection.batch?.state === 'paused') return
    if (!connection.capabilities?.includes('console-batch-v1')) {
      setError('当前扩展版本不支持有界批次，请更新扩展后刷新连接。'); return
    }
    if (!Number.isInteger(batchCap) || batchCap < 1 || batchCap > 60) {
      setError('批次候选上限必须是 1–60 的整数。'); return
    }
    let pending: SearchPlanTask[]
    try { pending = selectBoundedPendingTasks(tasks, batchSize) }
    catch (err) { setError(err instanceof Error ? err.message : '批次任务数无效。'); return }
    if (!pending.length) { setError('当前没有可加入批次的 pending 搜索任务。'); return }
    setError(''); setMessage('')
    setBatchConfirmation({ tasks: pending.map(row => ({ id: row.id, city: row.city, keywords: row.keywords })), cap: batchCap })
  }

  async function batchCommand(action: 'start-batch' | 'pause-batch' | 'resume-batch' | 'cancel-batch', approved = false) {
    if (admission.current) return
    // Never a silent no-op. `connection` is null whenever the handshake failed
    // its own validation, and returning quietly here meant a healthy worker
    // reporting one out-of-range number made every button do nothing at all,
    // with nothing on screen to say why. `bridgeDetail` is that reason.
    if (!connection) {
      setError(`扩展状态不可用，未发送请求：${bridgeDetail || '未知原因'}。可在「高级设置与诊断」里刷新连接。`)
      return
    }
    if ((action === 'start-batch' || action === 'resume-batch') && (!backendReady || checking)) return
    let taskIds: number[] | undefined
    let approvedBatchCap: number | undefined
    if (action === 'start-batch') {
      if (!approved || !batchConfirmation) return
      taskIds = batchConfirmation.tasks.map(row => row.id)
      approvedBatchCap = batchConfirmation.cap
      const currentConfirmed = batchConfirmation.tasks.map(approvedTask =>
        tasks.find(row => row.id === approvedTask.id))
      const tasksUnchanged = batchConfirmation.cap === batchCap
        && batchSize === batchConfirmation.tasks.length
        && currentConfirmed.length === taskIds.length
        && currentConfirmed.every((row, index) => row?.state === 'pending'
          && row.id === taskIds![index]
          && row.city === batchConfirmation.tasks[index].city
          && row.keywords === batchConfirmation.tasks[index].keywords)
      // Split from the task check on purpose. Both used to fail into one
      // sentence about the tasks having changed, so a leftover batch pointer -
      // by far the commoner cause - read as "press it again and nothing
      // happens". The remedy differs too: one wants a refresh, the other
      // wants 取消.
      const busyElsewhere = !!connection.runner
        || connection.batch?.state === 'running' || connection.batch?.state === 'paused'
      setBatchConfirmation(null)
      if (busyElsewhere) {
        setError(
          connection.runner
            ? '扩展里还有一个搜索单元在运行，先让它结束或点「取消」，再开始新批次；尚未发送请求。'
            : `扩展里还留着上一个批次（${connection.batch?.state === 'paused' ? '已暂停' : '运行中'}），`
              + '先点「取消」清掉它，再开始新批次；尚未发送请求。',
        )
        return
      }
      if (!tasksUnchanged) { setError('批次任务或状态已变化，请刷新后重新确认；尚未发送请求。'); return }
    }
    admission.current = true; setBusy(true); setError(''); setMessage('')
    try {
      const result = await consoleExtension(
        action, undefined, approvedBatchCap, taskIds, undefined, undefined, undefined,
        action === 'start-batch' ? runInBackground : undefined,
      )
      if (!result.ok) throw new Error(result.error || '批次操作未确认，请刷新状态。')
      setMessage(action === 'start-batch'
        ? '综合搜索已接收。扩展一次只运行一个搜索单元；任何失败或验证都会停止后续搜索。'
        : '批次请求已接收，请刷新状态确认。')
      await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : '批次请求失败') }
    finally { admission.current = false; setBusy(false) }
  }

  const owned = connection?.runner?.taskId === selected
  // A chosen code that is no longer among the read bands is dropped rather
  // than sent: it would be a code with no label behind it.
  const activeBandCodes = salaryBands
    .filter(band => chosenBands.includes(band.code))
    .map(band => band.code)
  // Dropped rather than sent if the band is no longer among those read: it
  // would be a code with no label behind it.
  // A chosen code with no band behind it any more is dropped rather than sent.
  const activeExpBands = expBands.filter(b => expChosen.includes(b.code))
  const activeExpCode = activeExpBands.length
    ? activeExpBands.map(b => b.code).join(',')
    : null
  const activeExpLabel = activeExpBands.map(b => b.label).join('、') || null
  const batchActive = connection?.batch?.state === 'running' || connection?.batch?.state === 'paused'
  const setupReady = setupLoaded && hasResume && hasRoles && cities.length > 0
  //: No title: the page header directly above already carries it.
  return <Card>
    <div className="row mb-1">
      <span className={`badge ${backendReady && connection ? 'badge-good' : 'badge-neutral'}`}>
        {backendReady && connection ? '已准备好' : '连接未就绪'}
      </span>
      <span className="small faint">
        需要保持已登录的 BOSS 标签页在正常 Chrome 中打开；首次识别特殊字体薪资时，请在该标签页点击一次 JobAgent 图标并关闭弹窗。
      </span>
    </div>

    {readiness && !readiness.ready ? (
      <Alert tone="warn">
        <strong>还差几项才能开始</strong>
        <ul className="bullet-list mt-1">
          {readiness.checks.filter(check => !check.ok).map(check => (
            <li key={check.key}>
              {check.label}：{check.detail}
              {check.fix ? <span className="faint">　—　{check.fix}</span> : null}
            </li>
          ))}
        </ul>
      </Alert>
    ) : null}

    {setupLoaded && !setupReady ? <Alert tone="warn">
      一键搜索需要当前简历、至少一个受支持城市和至少一个岗位方向。
      <Link to="/setup">完成个人设置</Link>
    </Alert> : null}

    <form onSubmit={prepareQuick} className="form-grid">
      {/* Toggle chips rather than a checkbox grid: four cities is a choice,
          not a form. `aria-pressed` carries the state for assistive tech and
          drives the accent styling that already exists in app.css. */}
      <fieldset className="choice-group"><legend>意向城市</legend>
        <div className="btn-row">{searchOptions?.supported_cities.map(option =>
          <button
            key={option}
            type="button"
            className="btn-sm"
            aria-pressed={cities.includes(option)}
            onClick={() => setCities(current => {
              if (current.includes(option)) return current.filter(value => value !== option)
              if (current.length >= (searchOptions?.max_selected_cities ?? 4)) {
                setError(`最多选择 ${searchOptions?.max_selected_cities ?? 4} 个城市。`)
                return current
              }
              setError('')
              return [...current, option]
            })}
          >{option}</button>)}</div>
      </fieldset>
      <div className="field-inline">
        <label htmlFor="target-count">每个方向收集</label>
        <input id="target-count" type="number" min={1} max={60} value={targetCount}
          onChange={e => {
            const next = Number(e.target.value)
            setTargetCount(next)
            try { window.localStorage.setItem(TARGET_KEY, String(next)) } catch { /* ignore */ }
          }} />
        <span>个新岗位</span>
        <span className="small faint">库里已有的会跳过，不占名额；每个方向最多打开 60 个详情。</span>
      </div>

      {/* Not folded away. Which experience bands to search is one of the
          three things that define a run - alongside the cities and the
          target count, both of which are already out here. It spent two
          runs inside the collapsed 搜索选项 block and was never once
          ticked, which is a fair verdict on hiding it. */}
      <div className="field">
          <label>只搜这些经验档（可多选，不勾＝不限）</label>
          <div className="row">
            {expBands.map(band => (
              <label key={band.code} className="checkbox-row">
                <input
                  type="checkbox"
                  checked={expChosen.includes(band.code)}
                  onChange={e => {
                    const next = e.target.checked
                      ? [...expChosen, band.code]
                      : expChosen.filter(code => code !== band.code)
                    setExpChosen(next)
                    try {
                      if (next.length) window.localStorage.setItem(EXP_CHOSEN_KEY, next.join(','))
                      else window.localStorage.removeItem(EXP_CHOSEN_KEY)
                    } catch { /* ignore */ }
                  }}
                />
                <span>{band.label}</span>
              </label>
            ))}
          </div>
          <p className="small faint">
            {activeExpLabel
              ? `本次只搜：${activeExpLabel}。BOSS 直接按这些档返回结果，不是搜回来再扔掉。`
              : '一个都不勾 = 不限，搜全部经验要求。勾上「经验不限」和「1-3年」是最常用的组合。'}
            {' '}它作用在每一次搜索上，不会像薪资分段那样把名额切开。
          </p>
          <details>
            <summary className="small faint">
              档位来源：{expFromSite
                ? 'BOSS 页面（已自动读取）'
                : '内置默认（2026-09-07 读自 BOSS；经验不限·101 与 1-3年·104 已对照地址栏核对一致）'}
            </summary>
            <div className="row mt-1">
              {expBands.map(band => (
                <span key={band.code} className="chip">{band.label} · {band.code}</span>
              ))}
            </div>
            <p className="small faint">
              选中后 JobAgent 就是把这些代码拼进搜索地址（experience=101,104），
              BOSS 打开后「工作经验」里会直接是勾上的状态——和你自己在页面上点是同一件事。
              核对方法：在 BOSS 上点一下同名档位，看地址栏 experience= 后面的数字对不对得上。
              开着 BOSS 搜索结果页时会自动重读一次；也可以现在手动重读。
            </p>
            <button
              type="button"
              className="btn-sm"
              disabled={expBusy || !connection?.capabilities?.includes('read-experience-filter-v1')}
              onClick={() => void readExperienceBands()}
            >
              {expBusy ? '读取中…' : '从 BOSS 页面重新读取'}
            </button>
          </details>
        </div>

      {/* Both of these are remembered and both default to on, so the page
          does not need to ask again every time. Open the row to change them. */}
      <details>
      <summary className="small">
        搜索选项：{runInBackground
          ? '后台搜索（窗口别盖住）'
          : <strong>前台搜索（切走会立即停止）</strong>} ·{' '}
        {autoFillSalary ? '结束后自动补薪资' : '不自动补薪资'}
        {activeBandCodes.length ? ` · 按 ${activeBandCodes.length} 个薪资档分段` : ''}
        {activeExpLabel ? ` · 只搜 ${activeExpLabel}` : ''}
      </summary>
      <div className="checkbox-row mt-1">
        <input
          id="run-background"
          type="checkbox"
          checked={runInBackground}
          onChange={e => {
            setRunInBackground(e.target.checked)
            try { window.localStorage.setItem(BACKGROUND_KEY, e.target.checked ? 'on' : 'off') } catch { /* ignore */ }
          }}
        />
        <label htmlFor="run-background">
          后台运行：可以切到别的窗口去做别的事，搜索和补薪资继续
        </label>
      </div>
      <p className="small faint indent">
        BOSS 窗口不能最小化、也不能被完全盖住——Chrome 会停掉看不见的标签页的渲染，
        BOSS 就不再往下加载新岗位，一个方向只剩首屏十几个。露出一条边就够了。
        遇到这种情况会暂停并在上面告诉你，不会当成“搜完了”。
      </p>
      <p className="small faint indent">
        只影响搜索，投递仍需前台确认。登录、验证码、风控照样立即停止，但你不会当场看见——回本页查看。
      </p>
      <div className="checkbox-row">
        <input
          id="auto-fill-salary"
          type="checkbox"
          checked={autoFillSalary}
          onChange={e => {
            setAutoFillSalary(e.target.checked)
            try { window.localStorage.setItem(AUTO_FILL_KEY, e.target.checked ? 'on' : 'off') } catch { /* ignore */ }
          }}
        />
        <label htmlFor="auto-fill-salary">搜索结束后自动补全薪资</label>
      </div>
      <p className="small faint indent">
        BOSS 的列表和详情面板里薪资是特殊字体，读不到；补全会去岗位详情页取。
      </p>


      {salaryBands.length && !activeBandCodes.length ? (
        <div className="row">
          <span className="small">
            读到 {salaryBands.length} 个薪资档位。按薪资分段搜索可以让 BOSS 换出不同的列表——
            可触及岗位翻几倍，代价是一次能跑的岗位方向变少。
          </span>
          <button type="button" className="btn-sm" onClick={() => {
            const codes = salaryBands.map(band => band.code)
            setChosenBands(codes)
            saveChosenBands(codes)
          }}>按薪资分段搜索</button>
        </div>
      ) : null}
      {activeBandCodes.length ? (
        <p className="small faint">
          已按 {activeBandCodes.length} 个薪资档分段搜索（每段各有完整名额）。
          <button type="button" className="btn-ghost btn-sm" onClick={() => {
            setChosenBands([])
            saveChosenBands([])
          }}>取消分段</button>
        </p>
      ) : null}
      {searchOptions && cities.length ? (
        <p className="small faint">
          本次将创建{' '}
          <strong>
            {Math.min(searchOptions.max_batch_tasks,
              Math.max(1, Math.floor(searchOptions.max_batch_tasks
                / (cities.length * Math.max(1, activeBandCodes.length))))
              * cities.length * Math.max(1, activeBandCodes.length))}
          </strong>{' '}
          个搜索单元（上限 {searchOptions.max_batch_tasks}）：{cities.length} 城市 ×{' '}
          最多 {Math.min(searchOptions.max_directions,
            Math.max(1, Math.floor(searchOptions.max_batch_tasks
              / (cities.length * Math.max(1, activeBandCodes.length)))))} 个方向
          {activeBandCodes.length ? ` × ${activeBandCodes.length} 个薪资档` : ''}。
        </p>
      ) : null}
      </details>
      <button className="btn btn-primary btn-lg" disabled={busy || checking || !backendReady || !connection
        || !setupReady || !!connection.runner || batchActive}>
        {busy ? '正在准备…' : '开始搜索'}
      </button>
    </form>

    {portfolioTasks.length ? <div className="card-block mt-1">
      <div><strong>本次综合搜索</strong></div>
      <div className="small faint">
        {portfolioCities.join('、')} · {portfolioDirections.length} 个岗位方向 · {portfolioTasks.length} 个有限搜索单元
      </div>
      <div className="grid grid-stats mt-1">
        <div className="stat">
          <span className="stat-label">进度</span>
          <span className="stat-value">{portfolioCompleted}<span className="stat-hint"> / {portfolioTasks.length}</span></span>
        </div>
        <div className="stat">
          <span className="stat-label">看到岗位卡片</span>
          <span className="stat-value">{portfolioObserved}</span>
        </div>
        <div className="stat">
          <span className="stat-label">新入库</span>
          <span className="stat-value">{portfolioImported}</span>
        </div>
      </div>
      {/* "已发现 960 · 已入库 65" invited exactly the wrong reading: that 895
          jobs were lost. Cards seen and jobs imported are different quantities
          measured at different stages, so the line now says which is which. */}
      {portfolioObserved > portfolioImported ? (
        <div className="small faint">
          两者不该相等：一张卡片只有在库里没有时才会被打开，
          每个方向最多打开 60 个详情、最多滚动 30 轮；
          同一岗位在不同方向、不同轮次里会重复出现——已在库中的不会重复入库。
        </div>
      ) : null}
      {missingSalaries && !connection?.runner && !batchActive ? (
        <div className="row mt-1">
          <span className="small">
            {missingSalaries} 个岗位还没有薪资{autoFillSalary ? '（搜索结束后会自动补全）' : ''}。
          </span>
          <button type="button" className="btn-primary btn-sm" disabled={salaryBusy}
            onClick={() => void fillSalaries()}>
            {salaryBusy ? '正在启动…' : '补全薪资'}
          </button>
        </div>
      ) : null}
      {salaryNote ? <p className="small faint">{salaryNote}</p> : null}
      {unanalysed && !connection?.runner && !batchActive ? (
        <div className="row mt-1">
          <span className="small">
            {unanalysed} 个岗位还没做 AI 分析——在分析之前，你看不出哪些值得投。
          </span>
          <button type="button" className="btn-sm btn-primary" disabled={analyseBusy}
            onClick={() => setAnalyseConfirm(unanalysed)}>
            {analyseBusy ? '分析中…' : '全部分析'}
          </button>
          <Link className="btn-sm" to="/jobs?analyzed=false">逐个挑选</Link>
        </div>
      ) : null}
      {analyseNote ? <p className="small faint">{analyseNote}</p> : null}
      {queueWaiting && !connection?.runner && !batchActive ? (
        <div className="row mt-1">
          <span className="small">
            投递队列里有 {queueWaiting} 个岗位在等你决定投或不投。
          </span>
          <Link className="btn-sm btn-primary" to="/queue">去看看</Link>
        </div>
      ) : null}
      {portfolioTasks.some(row => row.state === 'paused_login_required' || row.paused_reason === 'login_required')
        ? <div className="small text-danger mt-1" role="alert">请在当前 BOSS 标签页完成登录；登录成功后回到这里点击“恢复”。JobAgent 不会读取或填写登录凭据。</div>
        : null}
      {portfolioTasks.some(row => row.paused_reason === 'background_not_rendering')
        ? (
          <div className="small text-danger mt-1" role="alert">
            BOSS 窗口被完全盖住或最小化了，Chrome 停止了它的渲染，BOSS 就不再加载新岗位——继续跑下去每个方向只会读到首屏的十几个。
            把 BOSS 窗口露出来（不用点它，露出一部分即可），再点“恢复”，已用的额度都保留着。
          </div>
        )
        : null}
      <div className="actions mt-1">
        <button className="btn btn-secondary" disabled={busy || !batchActive || !connection?.runner}
          onClick={() => void batchCommand('pause-batch')}>暂停</button>
        <button className="btn btn-secondary" disabled={busy || checking || !backendReady || !batchActive
          || !connection?.batch?.requiresResume} onClick={() => void batchCommand('resume-batch')}>恢复</button>
        <button className="btn btn-secondary" disabled={busy || !batchActive || !connection?.runner}
          onClick={() => void batchCommand('cancel-batch')}>取消</button>
        <Link className="btn btn-secondary" to="/jobs">查看岗位库</Link>
      </div>
    </div> : null}


    {connection?.capabilities && !connection.capabilities.includes('skip-stored-candidates-v1') ? (
      <div className="card-block mt-1" role="alert">
        <strong>浏览器里加载的扩展是旧版本。</strong>
        <p className="small mt-1">
          它仍会把候选名额花在已入库的岗位上，所以本次搜索的新增数会明显偏低。
          请打开 <code>chrome://extensions</code>，在 JobAgent 上点一次「重新加载」，再刷新本页。
        </p>
      </div>
    ) : null}

    {aiPlan ? (
      <div className="card-block mt-1">
        <div>
          <strong>简历方向分析</strong>
          <span className="small faint">
            {aiPlan.cached
              ? ` · 已分析并缓存，${aiPlan.model}，重复使用不再收费`
              : ' · 尚未分析，当前排序只能靠用词重合度猜测'}
          </span>
        </div>
        {aiPlan.summary ? <p className="small mt-1">{aiPlan.summary}</p> : null}
        {!aiPlan.openai_configured ? (
          <p className="small faint mt-1">未配置 OPENAI_API_KEY，无法进行 AI 方向分析。</p>
        ) : !aiPlan.resume_id ? (
          <p className="small faint mt-1">尚未设置「当前分析简历」，请先在简历页上传或激活一份。</p>
        ) : (
          <div className="row mt-1">
            <button
              type="button"
              className="btn-sm"
              disabled={aiBusy}
              onClick={() => void analyzeDirections(aiPlan.cached)}
            >
              {aiBusy
                ? '分析中…'
                : aiPlan.cached
                  ? '重新分析（1 次调用）'
                  : `用 AI 分析我的方向（${aiPlan.pending_calls} 次调用）`}
            </button>
            <span className="small faint">
              一次调用覆盖全部 {aiPlan.candidates.length} 个方向；简历或职业策略变了会自动重新分析。
            </span>
          </div>
        )}
        {aiError ? <p className="small mt-1">{aiError}</p> : null}
        {aiPlan.directions.length ? (
          <details>
          <summary className="small">按方向看简历支撑度</summary>
          <table className="mt-1">
            <thead><tr><th>方向</th><th>简历支撑度</th><th>依据</th></tr></thead>
            <tbody>
              {aiPlan.directions.map(d => (
                <tr key={d.keyword}>
                  <td className="nowrap">
                    {d.keyword}
                    {d.suggested ? <span className="chip" style={{ marginLeft: 6 }}>AI 补充</span> : null}
                  </td>
                  <td className="nowrap">{Math.round(d.fit * 100)}/100</td>
                  <td className="small">{d.reasons.join('；') || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </details>
        ) : null}
      </div>
    ) : null}


    {chosen.length ? (
      <div className="card-block mt-1">
        <div><strong>本次选用的岗位方向</strong>
          <span className="small faint"> · 结合简历判断与历史结果，排序本身不消耗 AI 额度</span>
        </div>
        <table className="mt-1">
          <thead><tr><th>方向</th><th>历史</th><th>匹配度</th><th>依据</th></tr></thead>
          <tbody>
            {chosen.map(d => (
              <tr key={d.keyword}>
                <td className="nowrap">
                  {d.keyword}
                  {d.suggested ? <span className="chip" style={{ marginLeft: 6 }}>AI 补充</span> : null}
                </td>
                <td className="nowrap">
                  {d.jobs ? `${d.recommended}/${d.jobs}` : '—'}
                  {d.jobs && !d.has_evidence ? <span className="small faint"> 样本不足</span> : null}
                </td>
                {/* An AI judgement and a character count are different claims,
                    so they never render as the same bare number. */}
                <td className="nowrap small">
                  {d.fit_source === 'ai'
                    ? `${Math.round(d.fit * 100)}/100`
                    : d.fit_source === 'unjudged'
                      ? '未判断'
                      : `用词重合 ${Math.round(d.fit * 100)}%`}
                </td>
                <td className="small">{d.reasons.join('；') || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {chosenNotes.map(line => (
          <p key={line} className="small faint mt-1">{line}</p>
        ))}
      </div>
    ) : null}

    <button type="button" className="btn-sm mt-1" aria-expanded={showAdvanced}
      onClick={() => setShowAdvanced(current => !current)}>
      {showAdvanced ? '收起高级设置' : '高级设置与诊断'}
    </button>

    {showAdvanced ? <div className="card-block mt-1">
      <section aria-label="薪资分段">
    <details className="mt-1">
    <summary className="small">按粘贴的筛选链接分段（可选）</summary>
    <div className="field mt-1">
      <label htmlFor="filter-urls">每行一个 BOSS 搜索链接</label>
      <textarea
        id="filter-urls"
        rows={3}
        value={filterUrls}
        placeholder={'https://www.zhipin.com/web/geek/jobs?city=101010100&salary=406&query=...'}
        onChange={e => {
          setFilterUrls(e.target.value)
          saveSegments(e.target.value)
        }}
      />
      <p className="small faint">
        同一个关键词每次命中同一批岗位，换筛选才会换结果。每行一个分段，各有完整名额；
        只读取筛选参数，城市和方向仍由上面决定。
      </p>
      <div className="row mt-1">
        <button
          type="button"
          className="btn-sm"
          disabled={filterBusy || !connection?.capabilities?.includes('read-search-filters-v1')}
          onClick={() => void readFiltersFromBoss()}
        >
          {filterBusy ? '读取中…' : '读取当前 BOSS 标签页的筛选条件'}
        </button>
        {portfolioTasks[0]?.search_url ? (
          <a href={portfolioTasks[0].search_url} target="_blank" rel="noreferrer" className="small">
            打开 BOSS 搜索页 ↗
          </a>
        ) : null}
        <span className="small faint">在 BOSS 上点好筛选，回来点这里，不用复制粘贴。</span>
      </div>
      {filterNote ? <p className="small faint">{filterNote}</p> : null}
      {connection?.capabilities && !connection.capabilities.includes('read-search-filters-v1') ? (
        <p className="small faint">浏览器里的扩展还是旧版本，读取按钮不可用；重新加载扩展后可用。</p>
      ) : null}

      {filterLines.length ? (
        <p className="small faint">
          {filterLines.length} 个分段 · 岗位方向会相应减少，总搜索单元数不变（上限 16）。
          已记住，下次打开仍然生效；清空这个框即可停用。
          {segmentSummary ? <><br />识别到的筛选：{segmentSummary}</> : null}
        </p>
      ) : null}
    </div>
    </details>
      <details className="mt-1">
        <summary className="small">薪资分段（可选：把一个方向拆成几段，各自有独立名额）</summary>
      <div className="field mt-1">
        <div className="row">
          <button
            type="button"
            className="btn-sm"
            disabled={bandBusy || !connection?.capabilities?.includes('read-salary-filter-v1')}
            onClick={() => void readSalaryBands()}
          >
            {bandBusy ? '读取中…' : salaryBands.length ? '重新读取薪资档位' : '从 BOSS 页面读取薪资档位'}
          </button>
          <span className="small faint">
            在你打开的 BOSS 搜索页点开「薪资待遇」菜单，再点这里。只读取，不点击、不改动页面。
          </span>
        </div>
        {salaryBands.length ? (
          <div className="chip-list mt-1">
            {salaryBands.map(band => (
              <label key={band.code} className="checkbox-row">
                <input
                  type="checkbox"
                  checked={chosenBands.includes(band.code)}
                  onChange={() => toggleBand(band.code)}
                />
                <span>{band.label} <span className="faint mono">salary={band.code}</span></span>
              </label>
            ))}
          </div>
        ) : null}
        {activeBandCodes.length ? (
          <p className="small faint">
            已选 {activeBandCodes.length} 段；一次最多 16 个搜索单元，所以分段会占用方向数——
            {cities.length} 城 × {activeBandCodes.length} 段 最多剩{' '}
            {Math.max(1, Math.floor(16 / Math.max(1, cities.length * activeBandCodes.length)))} 个方向。
          </p>
        ) : (
          <p className="small faint">
            档位和代码都来自 BOSS 页面本身，这里不猜——用之前请核对一眼。
          </p>
        )}
      </div>
      </details>
      </section>
      <section aria-label="连接诊断">
        <p>本机服务：{backendDetail}。任务接口：{planDetail}。</p>
        <p>扩展诊断：{bridgeDetail}</p>
        <p>诊断码：{diagnosticCode} · 最近检查：{checkedAt}{checking ? ' · 检查中…' : ''}</p>
      </section>
      <button className="btn btn-secondary" disabled={busy || checking} onClick={() => void refresh()}>刷新连接</button>
    {keywordStats && keywordStats.cohorts.some(c => c.actionable) ? (
        <section className="mt-1" aria-label="搜索方向历史表现">
          <div><strong>搜索方向历史表现</strong><span className="small faint"> · 本地统计，不消耗 AI 额度</span></div>
          <table className="mt-1"><thead><tr><th>方向</th><th>岗位</th><th>均分</th><th>推荐率（95% 区间）</th></tr></thead>
            <tbody>{keywordStats.cohorts.filter(c => c.actionable).map(c => <tr key={c.keyword}>
              <td>{c.keyword}</td><td>{c.jobs}</td><td>{c.average_score ?? '—'}</td>
              <td>{c.recommend_rate === null ? '—' : `${c.recommended}/${c.jobs} · ${(c.recommend_rate * 100).toFixed(0)}%`}
                {c.interval_low !== null && c.interval_high !== null
                  ? <span className="small faint"> [{(c.interval_low * 100).toFixed(0)}–{(c.interval_high * 100).toFixed(0)}%]</span> : null}</td>
            </tr>)}</tbody></table>
          {keywordStats.observations.map(line => <p key={line} className="small faint mt-1">{line}</p>)}
        </section>
      ) : null}
      <form onSubmit={prepare} className="form-grid mt-1">
        <label>自定义搜索城市<select value={city} onChange={e => setCity(e.target.value)}>
          {searchOptions?.supported_cities.map(option => <option key={option} value={option}>{option}</option>)}
        </select></label>
        <label>自定义岗位方向<input value={keyword} maxLength={100} required
          onChange={e => setKeyword(e.target.value)} /></label>
        <button className="btn btn-secondary" disabled={busy || !backendReady}>准备自定义搜索</button>
      </form>
      <label>选择已有搜索任务<select value={selected ?? ''} disabled={busy} onChange={e => {
        const id = Number(e.target.value); setSelected(id || null); if (id) onSelect(id)
      }}><option value="">请选择</option>{tasks.map(row => <option key={row.id} value={row.id}>
        #{row.id} · {row.city} · {row.keywords} · {row.state}
      </option>)}</select></label>
      <label>单任务候选上限（1–60）<input type="number" min={1} max={60} value={cap}
        disabled={busy} onChange={e => setCap(Number(e.target.value))} /></label>

      <section aria-label="有界搜索批次" className="mt-1">
        <h3>批量搜索</h3>
        <p className="small faint">内部逐个运行最多 {MAX_CONSOLE_BATCH_TASKS} 个待执行搜索单元。</p>
        <label>搜索单元数（1–{MAX_CONSOLE_BATCH_TASKS}）<input type="number" min={1} max={MAX_CONSOLE_BATCH_TASKS} value={batchSize}
          disabled={busy || batchActive} onChange={e => setBatchSize(Number(e.target.value))} /></label>
        <label>每个任务候选上限（1–60）<input type="number" min={1} max={60} value={batchCap}
          disabled={busy || batchActive} onChange={e => setBatchCap(Number(e.target.value))} /></label>
        <div className="actions">
          <button className="btn btn-primary" disabled={busy || checking || !backendReady || !connection
            || !!connection.runner || batchActive} onClick={prepareBatch}>开始批量搜索</button>
          <button className="btn btn-secondary" disabled={busy || !batchActive || !connection?.runner}
            onClick={() => void batchCommand('pause-batch')}>暂停批次</button>
          <button className="btn btn-secondary" disabled={busy || checking || !backendReady || !batchActive
            || !connection?.batch?.requiresResume} onClick={() => void batchCommand('resume-batch')}>恢复批次</button>
          <button className="btn btn-secondary" disabled={busy || !batchActive || !connection?.runner}
            onClick={() => void batchCommand('cancel-batch')}>取消批次</button>
        </div>
        {connection?.batch && <p className="small">批次：{connection.batch.state} · 当前 {connection.batch.currentIndex + 1}/
          {connection.batch.taskIds.length}（任务 #{connection.batch.currentTaskId}）</p>}
      </section>
      {task ? <section className="mt-1" aria-label="当前单任务详情">
        <p><strong>{task.city} · {task.keywords}</strong> · {taskStatusLabel(task)} · 看到 {task.observed_jobs} 张 · 新入库 {task.imported_jobs} 个</p>
        <div className="actions">
          <button className="btn btn-primary" disabled={busy || checking || !backendReady || !connection
            || !!connection.runner || batchActive || task.state !== 'pending'} onClick={() => void command('start')}>开始单任务</button>
          {owned ? <><button className="btn btn-secondary" disabled={busy || batchActive} onClick={() => void command('pause')}>暂停单任务</button>
            <button className="btn btn-secondary" disabled={busy || checking || !backendReady || batchActive
              || !connection?.runner?.paused || connection.runner.paid} onClick={() => void command('resume')}>恢复单任务</button>
            <button className="btn btn-secondary" disabled={busy || batchActive} onClick={() => void command('cancel')}>取消单任务</button></> : null}
          <button className="btn btn-secondary" onClick={() => onSelect(task.id)}>查看该任务候选</button>
        </div>
      </section> : null}
    </div> : null}
    {analyseConfirm ? <Modal
      title="确认批量分析"
      onClose={() => setAnalyseConfirm(null)}
      footer={<>
        <button type="button" onClick={() => setAnalyseConfirm(null)}>取消</button>
        <button type="button" className="btn-primary" onClick={() => void analyseBacklog(analyseConfirm)}>
          确认分析 {analyseConfirm} 个
        </button>
      </>}
    >
      <p><strong>{analyseConfirm} 个岗位</strong>还没有分析，将产生<strong>最多 {analyseConfirm} 次</strong>
        AI 调用（已缓存的不再收费）。</p>
      <p>用当前分析简历和常规模型。失败的不会自动重试，也不会退回已经发出的调用。</p>
      <p className="small faint">分析只给出建议评分，不会改变任何岗位的状态，更不会投递。</p>
    </Modal> : null}

    {confirmation && <Modal title="确认免费采集" onClose={() => setConfirmation(null)} footer={<>
      <button className="btn btn-secondary" autoFocus onClick={() => setConfirmation(null)}>暂不执行</button>
      <button className="btn btn-primary" disabled={busy || checking || !backendReady || !connection}
        onClick={() => void command(confirmation.action, true)}>确认{confirmation.action === 'start' ? '开始' : '恢复'}免费采集</button>
    </>}>
      <p>任务 #{confirmation.taskId} · {confirmation.city} · {confirmation.keywords}</p>
      <p>最多 {confirmation.action === 'resume' ? '原批准数量（不重置）' : confirmation.cap} 个候选尝试、5 次滚动。失败也占名额。</p>
      <p>扩展将切换到前台 BOSS 标签页，请保持 Chrome 前台。无 AI 匹配费用，不投递、不收藏、不发消息。</p>
    </Modal>}
    {batchConfirmation && <Modal title="确认有界搜索批次" onClose={() => setBatchConfirmation(null)} footer={<>
      <button className="btn btn-secondary" autoFocus onClick={() => setBatchConfirmation(null)}>暂不执行</button>
      <button className="btn btn-primary" disabled={busy || checking || !backendReady || !connection}
        onClick={() => void batchCommand('start-batch', true)}>确认开始批次</button>
    </>}>
      <p>这次综合搜索将覆盖：</p>
      <p><strong>城市：</strong>{[...new Set(batchConfirmation.tasks.map(row => row.city).filter(Boolean))].join('、')}</p>
      <p><strong>岗位方向：</strong>{[...new Set(batchConfirmation.tasks.map(row => row.keywords).filter(Boolean))].join('、')}</p>
      <p>共 {batchConfirmation.tasks.length} 个有限搜索单元；每个方向最多 {batchConfirmation.cap} 个候选尝试、5 次滚动。</p>
      {/* The unit count alone understates the session: a comprehensive
          portfolio can be several times longer than a single search. Show
          the ceiling that actually determines how long the browser runs. */}
      <p><strong>最多 {batchConfirmation.tasks.length * batchConfirmation.cap} 次候选尝试</strong>，全部在前台可见的 BOSS 标签页里逐个进行，会持续较长时间。中途可以随时暂停或取消。</p>
      <p>系统内部一次只执行一个；失败、取消、验证或前台丢失会停止整批，不会自动继续。</p>
      <p>无定时后台启动、无 AI 匹配费用，不投递、不收藏、不发消息。</p>
    </Modal>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {(() => {
      // The most recent stopped run in this plan - a failure the user has not
      // been shown is indistinguishable from "nothing happened".
      const stopped = [...portfolioTasks, ...(task ? [task] : [])]
        .filter(row => row.last_error && (row.state === 'failed'
          || row.last_action === 'skipped_capture_timeout'))
        .sort((a, b) => (a.updated_at || '').localeCompare(b.updated_at || ''))
        .pop()
      // A batch that stopped between tasks leaves its reason on the batch, not
      // on any task - and that is precisely the "it just stopped" case.
      const batchStopped = connection?.batch?.state === 'stopped' ? connection.batch.lastError : null
      const hint = stopHint(batchStopped) || stopHint(stopped?.last_error)
      return hint ? <Alert tone="warn">{hint}</Alert> : null
    })()}
    {showAdvanced && task ? <div className="small faint mt-1">
      任务 #{task.id} · 滚动 {task.scroll_round}/5 · 已渲染 {task.visible_jobs} · 新增 {task.new_jobs} ·
      重复 {task.duplicate_jobs} · 连续无新增 {task.no_new_rounds}<br />
      最近动作：{task.last_action || '无'} · 暂停：{task.paused_reason || '无'} · 错误：{task.last_error || '无'}
    </div> : null}
  </Card>
}
