import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, api } from '@/api/client'
import { Alert, Card, Modal } from '@/components/ui'
import { assessConsoleConnection, ConsoleConnectionError, consoleExtension,
  DEFAULT_BATCH_CANDIDATE_CAP, selectBoundedPendingTasks } from '@/pages/consoleExtension'
import type { ConsoleAction, ConsoleReply } from '@/pages/consoleExtension'
import type { SearchKeywordAnalytics, SearchPlanOptions, SearchPlanTask } from '@/types'

function taskStatusLabel(task: SearchPlanTask): string {
  if (task.state === 'paused_login_required' || task.paused_reason === 'login_required') return '需要登录 BOSS'
  if (task.state === 'paused_verification' || task.paused_reason === 'verification') return '等待人工验证'
  return task.state || '未开始'
}

export default function ConsoleSearchPanel({ onSelect }: { onSelect: (id: number) => void }) {
  const [city, setCity] = useState('')
  const [cities, setCities] = useState<string[]>([])
  const [keyword, setKeyword] = useState('')
  const [searchOptions, setSearchOptions] = useState<SearchPlanOptions | null>(null)
  const [hasResume, setHasResume] = useState(false)
  const [hasRoles, setHasRoles] = useState(false)
  const [setupLoaded, setSetupLoaded] = useState(false)
  const [cap, setCap] = useState(3)
  const [targetCount, setTargetCount] = useState(3)
  const [batchSize, setBatchSize] = useState(2)
  const [batchCap, setBatchCap] = useState(DEFAULT_BATCH_CANDIDATE_CAP)
  const [tasks, setTasks] = useState<SearchPlanTask[]>([])
  const [keywordStats, setKeywordStats] = useState<SearchKeywordAnalytics | null>(null)
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
    return () => { alive = false }
  }, [])

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
    if (cities.length > 1 && !connection?.capabilities?.includes('console-batch-v1')) {
      setError('当前扩展版本不支持多城市串行搜索，请更新扩展后刷新连接。')
      return
    }
    admission.current = true; setBusy(true); setError(''); setMessage('')
    try {
      const prepared = await api.prepareResumeSearch(cities, targetCount)
      const nextTasks = prepared.tasks
      if (!nextTasks.length) throw new Error('没有生成可执行的搜索任务。')
      const nextIds = new Set(nextTasks.map(row => row.id))
      setTasks(current => [...current.filter(row => !nextIds.has(row.id)), ...nextTasks])
      setSelected(nextTasks[0].id)
      setCap(targetCount)
      onSelect(nextTasks[0].id)
      if (nextTasks.length === 1) {
        const nextTask = nextTasks[0]
        setConfirmation({
          action: 'start', taskId: nextTask.id, city: nextTask.city,
          keywords: nextTask.keywords, cap: targetCount,
        })
      } else {
        setBatchSize(nextTasks.length)
        setBatchCap(targetCount)
        setBatchConfirmation({
          tasks: nextTasks.map(row => ({ id: row.id, city: row.city, keywords: row.keywords })),
          cap: targetCount,
        })
      }
      const directions = [...new Set(nextTasks.map(row => row.keywords).filter(Boolean))]
      setMessage(`已使用当前简历「${prepared.active_resume_name}」的岗位方向准备 ${nextTasks.length} 个搜索任务`
        + `（城市 × 方向）：${directions.join('、')}。确认后串行搜索，最多 5 个任务。`)
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
        ? '有界批次已接收。扩展只会按确认顺序串行运行；任何失败或验证都会停止后续任务。'
        : '批次请求已接收，请刷新状态确认。')
      await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : '批次请求失败') }
    finally { admission.current = false; setBusy(false) }
  }

  const owned = connection?.runner?.taskId === selected
  const batchActive = connection?.batch?.state === 'running' || connection?.batch?.state === 'paused'
  const setupReady = setupLoaded && hasResume && hasRoles && cities.length > 0
  return <Card title="搜索适合我的岗位" sub="选择城市和数量；岗位方向取自当前简历策略，一次最多 5 个「城市 × 方向」组合。">
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
      <label>每个方向搜索岗位数量（1–20）<input type="number" min={1} max={20} value={targetCount}
        onChange={e => setTargetCount(Number(e.target.value))} /></label>
      <button className="btn btn-primary" disabled={busy || checking || !backendReady || !connection
        || !setupReady || !!connection.runner || batchActive}>
        {busy ? '正在准备…' : '一键搜索适合我的岗位'}
      </button>
    </form>

    {task ? <div className="card-block mt-1">
      <div><strong>{task.city} · {task.keywords}</strong></div>
      <div className="small faint">状态：{taskStatusLabel(task)} · 已发现 {task.observed_jobs} 个 · 已入库 {task.imported_jobs} 个</div>
      {task.state === 'paused_login_required' || task.paused_reason === 'login_required'
        ? <div className="small text-danger mt-1" role="alert">请在当前 BOSS 标签页完成登录；登录成功后回到这里点击“恢复”。JobAgent 不会读取或填写登录凭据。</div>
        : null}
      <div className="actions mt-1">
        <button className="btn btn-primary" disabled={busy || checking || !backendReady || !connection
          || !!connection.runner || batchActive || task.state !== 'pending'}
          onClick={() => void command('start')}>开始搜索</button>
        {owned ? <>
          <button className="btn btn-secondary" disabled={busy || batchActive}
            onClick={() => void command('pause')}>暂停</button>
          <button className="btn btn-secondary" disabled={busy || checking || !backendReady || batchActive
            || !connection?.runner?.paused || connection.runner.paid}
            onClick={() => void command('resume')}>恢复</button>
          <button className="btn btn-secondary" disabled={busy || batchActive}
            onClick={() => void command('cancel')}>取消</button>
        </> : null}
        <button className="btn btn-secondary" onClick={() => onSelect(task.id)}>查看候选岗位</button>
      </div>
    </div> : <p className="small faint mt-1">只需选择城市和数量；岗位方向来自当前简历与职业策略，一次最多 5 个「城市 × 方向」组合（城市越多，每个城市分到的方向越少）。</p>}

    {keywordStats && keywordStats.cohorts.some(c => c.actionable) ? (
      <div className="card-block mt-1">
        <div><strong>搜索方向表现</strong>
          <span className="small faint"> · 本地统计，不消耗 AI 额度</span>
        </div>
        <table className="mt-1">
          <thead>
            <tr><th>方向</th><th>岗位</th><th>均分</th><th>推荐率（95% 区间）</th></tr>
          </thead>
          <tbody>
            {keywordStats.cohorts.filter(c => c.actionable).map(c => (
              <tr key={c.keyword}>
                <td>{c.keyword}</td>
                <td className="nowrap">{c.jobs}</td>
                <td className="nowrap">{c.average_score ?? '—'}</td>
                <td className="nowrap">
                  {c.recommend_rate === null
                    ? '—'
                    : `${c.recommended}/${c.jobs} · ${(c.recommend_rate * 100).toFixed(0)}%`}
                  {c.interval_low !== null && c.interval_high !== null ? (
                    <span className="small faint">
                      {' '}[{(c.interval_low * 100).toFixed(0)}–{(c.interval_high * 100).toFixed(0)}%]
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {keywordStats.observations.map(line => (
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
        <p className="small faint">按任务列表顺序运行最多 5 个待执行任务。</p>
        <label>任务数（1–5）<input type="number" min={1} max={5} value={batchSize}
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
      <p>将按以下固定顺序串行运行 {batchConfirmation.tasks.length} 个任务：</p>
      <ol>{batchConfirmation.tasks.map(row => <li key={row.id}>#{row.id} · {row.city} · {row.keywords}</li>)}</ol>
      <p>每个任务最多 {batchConfirmation.cap} 个候选尝试、5 次滚动；失败、取消、验证或前台丢失会停止整批，不会继续下一个。</p>
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
