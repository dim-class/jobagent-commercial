import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, api } from '@/api/client'
import { Alert, Card, Modal } from '@/components/ui'
import { assessConsoleConnection, ConsoleConnectionError, consoleExtension,
  DEFAULT_BATCH_CANDIDATE_CAP, MAX_CONSOLE_BATCH_TASKS, selectBoundedPendingTasks } from '@/pages/consoleExtension'
import type { ConsoleAction, ConsoleReply } from '@/pages/consoleExtension'
import type { DirectionAnalysisPlan, DirectionChoice, SearchKeywordAnalytics, SearchPlanOptions, SearchPlanTask } from '@/types'

function taskStatusLabel(task: SearchPlanTask): string {
  if (task.state === 'paused_login_required' || task.paused_reason === 'login_required') return '需要登录 BOSS'
  if (task.state === 'paused_verification' || task.paused_reason === 'verification') return '等待人工验证'
  return task.state || '未开始'
}

/** A pasted block is split on the newline character itself; `trim()` on
 *  each line removes the CR that a Windows clipboard leaves behind. */
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
  const [targetCount, setTargetCount] = useState(8)
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
  const [aiPlan, setAiPlan] = useState<DirectionAnalysisPlan | null>(null)
  const [aiBusy, setAiBusy] = useState(false)
  const [aiError, setAiError] = useState<string | null>(null)
  //: BOSS search URLs the user built in their own browser. Each one narrows a
  //: search so its top results differ - the only way to reach past the first
  //: page without moving a ceiling. One per line.
  const [filterUrls, setFilterUrls] = useState('')
  const filterLines = filterUrls.split(NEWLINE).map(line => line.trim()).filter(Boolean)
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
    const visible = () => { if (document.visibilityState === 'visible') void refresh() }
    document.addEventListener('visibilitychange', visible)
    return () => { alive.current = false; refreshSequence.current++; document.removeEventListener('visibilitychange', visible) }
  }, [loadSetup, refresh])

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
    if (!Number.isInteger(targetCount) || targetCount < 1 || targetCount > 20) {
      setError('岗位数量必须是 1–20 的整数。')
      return
    }
    if (!connection?.capabilities?.includes('console-batch-v1')) {
      setError('当前扩展版本不支持综合搜索，请更新扩展后刷新连接。')
      return
    }
    admission.current = true; setBusy(true); setError(''); setMessage('')
    try {
      const prepared = await api.prepareResumeSearch(cities, targetCount, filterLines)
      const nextTasks = prepared.tasks
      if (!nextTasks.length) throw new Error('没有生成可执行的搜索任务。')
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
    if ((action === 'start' || action === 'resume') && (!backendReady || !connection || checking)) return
    // Call the bridge immediately in the trusted click turn: no async preparation
    // before browser handoff, and never include a paid match approval.
    if (action === 'start' && (!Number.isInteger(cap) || cap < 1 || cap > 20)) {
      setError('候选上限必须是 1–20 的整数。'); return
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
      const result = await consoleExtension(action, task.id, cap)
      if (!result.ok) throw new Error(result.error || '未确认执行，请刷新状态。')
      setMessage('请求已接收。BOSS 页顶部显示执行进度；返回控制台会刷新后端状态。切换离开 BOSS 会停止后续浏览器动作。')
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
    if (!Number.isInteger(batchCap) || batchCap < 1 || batchCap > 20) {
      setError('批次候选上限必须是 1–20 的整数。'); return
    }
    let pending: SearchPlanTask[]
    try { pending = selectBoundedPendingTasks(tasks, batchSize) }
    catch (err) { setError(err instanceof Error ? err.message : '批次任务数无效。'); return }
    if (!pending.length) { setError('当前没有可加入批次的 pending 搜索任务。'); return }
    setError(''); setMessage('')
    setBatchConfirmation({ tasks: pending.map(row => ({ id: row.id, city: row.city, keywords: row.keywords })), cap: batchCap })
  }

  async function batchCommand(action: 'start-batch' | 'pause-batch' | 'resume-batch' | 'cancel-batch', approved = false) {
    if (admission.current || !connection) return
    if ((action === 'start-batch' || action === 'resume-batch') && (!backendReady || checking)) return
    let taskIds: number[] | undefined
    let approvedBatchCap: number | undefined
    if (action === 'start-batch') {
      if (!approved || !batchConfirmation) return
      taskIds = batchConfirmation.tasks.map(row => row.id)
      approvedBatchCap = batchConfirmation.cap
      const currentConfirmed = batchConfirmation.tasks.map(approvedTask =>
        tasks.find(row => row.id === approvedTask.id))
      const unchanged = batchConfirmation.cap === batchCap && batchSize === batchConfirmation.tasks.length
        && currentConfirmed.length === taskIds.length
        && currentConfirmed.every((row, index) => row?.state === 'pending'
          && row.id === taskIds![index]
          && row.city === batchConfirmation.tasks[index].city
          && row.keywords === batchConfirmation.tasks[index].keywords)
        && !connection.runner && connection.batch?.state !== 'running' && connection.batch?.state !== 'paused'
      setBatchConfirmation(null)
      if (!unchanged) { setError('批次任务或状态已变化，请刷新后重新确认；尚未发送请求。'); return }
    }
    admission.current = true; setBusy(true); setError(''); setMessage('')
    try {
      const result = await consoleExtension(action, undefined, approvedBatchCap, taskIds)
      if (!result.ok) throw new Error(result.error || '批次操作未确认，请刷新状态。')
      setMessage(action === 'start-batch'
        ? '综合搜索已接收。扩展一次只运行一个搜索单元；任何失败或验证都会停止后续搜索。'
        : '批次请求已接收，请刷新状态确认。')
      await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : '批次请求失败') }
    finally { admission.current = false; setBusy(false) }
  }

  const owned = connection?.runner?.taskId === selected
  const batchActive = connection?.batch?.state === 'running' || connection?.batch?.state === 'paused'
  const setupReady = setupLoaded && hasResume && hasRoles && cities.length > 0
  return <Card title="搜索适合我的岗位" sub="选择城市和数量；JobAgent 会综合当前简历与职业方向，覆盖多个相关岗位方向。">
    <div className="row mb-1">
      <span className={`badge ${backendReady && connection ? 'badge-good' : 'badge-neutral'}`}>
        {backendReady && connection ? '已准备好' : '连接未就绪'}
      </span>
      <span className="small faint">
        需要保持已登录的 BOSS 标签页在正常 Chrome 中打开；首次识别特殊字体薪资时，请在该标签页点击一次 JobAgent 图标并关闭弹窗。
      </span>
    </div>

    {setupLoaded && !setupReady ? <Alert tone="warn">
      一键搜索需要当前简历、至少一个受支持城市和至少一个岗位方向。
      <Link to="/setup">完成个人设置</Link>
    </Alert> : null}

    <form onSubmit={prepareQuick} className="form-grid">
      <fieldset className="choice-group"><legend>意向城市（可多选）</legend>
        <div className="row">{searchOptions?.supported_cities.map(option =>
          <label key={option} className="check-label"><input type="checkbox" checked={cities.includes(option)}
            onChange={e => setCities(current => {
              if (!e.target.checked) return current.filter(value => value !== option)
              if (current.length >= (searchOptions?.max_selected_cities ?? 4)) {
                setError(`最多选择 ${searchOptions?.max_selected_cities ?? 4} 个城市。`)
                return current
              }
              setError('')
              return [...current, option]
            })} />{option}</label>)}</div>
      </fieldset>
      <label>每个方向希望搜索的岗位数（1–20）<input type="number" min={1} max={20} value={targetCount}
        onChange={e => setTargetCount(Number(e.target.value))} /></label>
      <button className="btn btn-primary" disabled={busy || checking || !backendReady || !connection
        || !setupReady || !!connection.runner || batchActive}>
        {busy ? '正在准备…' : '一键搜索适合我的岗位'}
      </button>
    </form>

    {portfolioTasks.length ? <div className="card-block mt-1">
      <div><strong>本次综合搜索</strong></div>
      <div className="small faint">
        {portfolioCities.join('、')} · {portfolioDirections.length} 个岗位方向 · {portfolioTasks.length} 个有限搜索单元
      </div>
      <div className="mt-1">
        整体进度：{portfolioCompleted}/{portfolioTasks.length} · 看到岗位卡片 {portfolioObserved} 张 · 新入库 {portfolioImported} 个
      </div>
      {/* "已发现 960 · 已入库 65" invited exactly the wrong reading: that 895
          jobs were lost. Cards seen and jobs imported are different quantities
          measured at different stages, so the line now says which is which. */}
      {portfolioObserved > portfolioImported ? (
        <div className="small faint">
          两者不该相等：每个方向最多只打开 {portfolioTasks[0]?.max_candidates ?? 20} 个岗位详情，
          且同一岗位在不同方向、不同轮次里会重复出现——已在库中的不会重复入库。
        </div>
      ) : null}
      {portfolioTasks.some(row => row.state === 'paused_login_required' || row.paused_reason === 'login_required')
        ? <div className="small text-danger mt-1" role="alert">请在当前 BOSS 标签页完成登录；登录成功后回到这里点击“恢复”。JobAgent 不会读取或填写登录凭据。</div>
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
    </div> : <p className="small faint mt-1">只需选择城市和数量；系统会从当前简历关联的职业策略中选取最多 8 个相关方向，组合成一次综合搜索。</p>}


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
        ) : null}
      </div>
    ) : null}

    <div className="field mt-1">
      <label htmlFor="filter-urls">搜索分段（可选，每行一个 BOSS 搜索链接）</label>
      <textarea
        id="filter-urls"
        rows={3}
        value={filterUrls}
        placeholder={'https://www.zhipin.com/web/geek/jobs?city=101010100&salary=406&query=...'}
        onChange={e => setFilterUrls(e.target.value)}
      />
      <p className="small faint">
        BOSS 的结果按相关度排序且没有「最新发布」，所以同一个关键词每次都命中同一批岗位。
        在你自己的浏览器里点好筛选（薪资、区域…），把地址栏粘进来，
        每行会成为一个独立的搜索分段，各自拥有完整的候选名额。
        只读取筛选参数；城市与岗位方向仍由上面的选择和简历排序决定。
      </p>
      {filterLines.length ? (
        <p className="small faint">
          {filterLines.length} 个分段 · 岗位方向会相应减少，总搜索单元数不变（上限 16）。
        </p>
      ) : null}
    </div>

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
      <label>单任务候选上限（1–20）<input type="number" min={1} max={20} value={cap}
        disabled={busy} onChange={e => setCap(Number(e.target.value))} /></label>

      <section aria-label="有界搜索批次" className="mt-1">
        <h3>批量搜索</h3>
        <p className="small faint">内部逐个运行最多 {MAX_CONSOLE_BATCH_TASKS} 个待执行搜索单元。</p>
        <label>搜索单元数（1–{MAX_CONSOLE_BATCH_TASKS}）<input type="number" min={1} max={MAX_CONSOLE_BATCH_TASKS} value={batchSize}
          disabled={busy || batchActive} onChange={e => setBatchSize(Number(e.target.value))} /></label>
        <label>每个任务候选上限（1–20）<input type="number" min={1} max={20} value={batchCap}
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
    {showAdvanced && task ? <div className="small faint mt-1">
      任务 #{task.id} · 滚动 {task.scroll_round}/5 · 已渲染 {task.visible_jobs} · 新增 {task.new_jobs} ·
      重复 {task.duplicate_jobs} · 连续无新增 {task.no_new_rounds}<br />
      最近动作：{task.last_action || '无'} · 暂停：{task.paused_reason || '无'} · 错误：{task.last_error || '无'}
    </div> : null}
  </Card>
}
