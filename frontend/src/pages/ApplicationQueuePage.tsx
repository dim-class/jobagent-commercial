import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import AppliedBackfillPanel from '@/pages/AppliedBackfillPanel'
import { ApiError, api, type QueueFilters } from '@/api/client'
import { consoleExtension } from '@/pages/consoleExtension'
import {
  ResumePicker,
  UNKNOWN_CHOICE,
  useSelectableResumes,
  type ResumeChoice,
} from '@/components/ResumePicker'
import {
  Alert,
  Card,
  EmptyState,
  Loading,
  Modal,
  ScoreBadge,
  StatusBadge,
  VerdictBadge,
  formatDateTime,
} from '@/components/ui'
import type {
  ApplicationApprovalOut,
  ApplicationProposal,
  JobStatus,
  QueueResponse,
  Verdict,
} from '@/types'

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

const SORT_LABEL: Record<string, string> = {
  recommended: '推荐优先',
  score: '匹配分',
  newest: '最新',
  salary: '薪资',
}

const STATE_LABEL: Record<string, string> = {
  ready: '优先处理',
  pending: '待处理',
  later: '稍后处理',
  dismissed: '已跳过',
  completed: '已处理',
}

export default function ApplicationQueuePage() {
  const [data, setData] = useState<QueueResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [busyJob, setBusyJob] = useState<number | null>(null)
  const [copiedJob, setCopiedJob] = useState<number | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [selected, setSelected] = useState<Set<number>>(new Set())

  const [filters, setFilters] = useState<QueueFilters>({ sort: 'recommended', limit: 100 })
  //: Off by default: this records real actions, so it is opened deliberately.
  const [showBackfill, setShowBackfill] = useState(false)
  const [keywordInput, setKeywordInput] = useState('')

  // Confirmation dialogs: applying and skipping are real decisions.
  const [confirmApply, setConfirmApply] = useState<ApplicationProposal | null>(null)
  const [confirmApplyFromM6, setConfirmApplyFromM6] = useState(false)
  const [applyNote, setApplyNote] = useState('')
  // Which resume the user says they actually submitted. Defaults to the
  // active analysis resume as a convenience; never substituted silently.
  const [appliedResume, setAppliedResume] = useState<ResumeChoice>(UNKNOWN_CHOICE)
  const { resumes, activeId } = useSelectableResumes()
  const [skipTarget, setSkipTarget] = useState<ApplicationProposal | null>(null)
  const [skipReason, setSkipReason] = useState('')
  const [m6Target, setM6Target] = useState<ApplicationProposal | null>(null)
  const [m6ResumeId, setM6ResumeId] = useState<number | null>(null)
  const [m6DynamicAccepted, setM6DynamicAccepted] = useState(false)
  const [m6Approval, setM6Approval] = useState<ApplicationApprovalOut | null>(null)
  const [m6Busy, setM6Busy] = useState(false)
  const [m6Attempted, setM6Attempted] = useState(false)
  const [m6StatusUnknown, setM6StatusUnknown] = useState(false)
  //: Why the last attempt did not happen, shown *in* the dialog. The first
  //: live run failed with a precise reason that went to the page-level
  //: banner behind the modal, so the only thing visible was a button that
  //: had turned itself off.
  const [m6Error, setM6Error] = useState<string | null>(null)

  const load = useCallback(async (active: QueueFilters) => {
    setLoading(true)
    try {
      setData(await api.applicationQueue(active))
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载队列失败' })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(filters)
  }, [filters, load])

  const summary = data?.summary
  const items = data?.items ?? []
  const cityOptions = useMemo(() => Object.keys(data?.facets.cities ?? {}), [data])
  const skipReasons = data?.facets.skip_reasons ?? []

  function updateFilter<K extends keyof QueueFilters>(key: K, value: QueueFilters[K]) {
    setFilters((prev) => ({ ...prev, [key]: value }))
  }

  async function run(jobId: number, action: () => Promise<{ message: string }>) {
    setBusyJob(jobId)
    try {
      const result = await action()
      setFeedback({ tone: 'success', text: result.message })
      setSelected((prev) => {
        const next = new Set(prev)
        next.delete(jobId)
        return next
      })
      await load(filters)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '操作失败' })
    } finally {
      setBusyJob(null)
    }
  }

  async function copyGreeting(proposal: ApplicationProposal) {
    // Copying is not a decision: it must never change the job's status.
    try {
      await navigator.clipboard.writeText(proposal.greeting_message)
      setCopiedJob(proposal.job_id)
      window.setTimeout(() => setCopiedJob(null), 2000)
    } catch {
      setFeedback({ tone: 'warn', text: '浏览器拒绝了剪贴板访问，请手动选中招呼语复制。' })
    }
  }

  /** Put the greeting on the clipboard as the posting's own page opens.
   *
   * Rendered as a real anchor so the browser treats the new tab as a plain user
   * navigation (a popup blocker would eat a scripted `window.open`). Neither
   * half is a decision: opening a page and filling the clipboard change no
   * status and send nothing. This remains the fully manual path: you paste,
   * read it, click send, and only then come back and record it. The separate
   * M6 path is deliberately not reachable from this helper.
   */
  function openForApplying(proposal: ApplicationProposal) {
    if (proposal.greeting_message) void copyGreeting(proposal)
    setFeedback({
      tone: 'info',
      text: `已打开「${proposal.title}」的岗位页${
        proposal.greeting_message ? '，招呼语已复制到剪贴板' : ''
      }。发送后回到这里点「标记已投递」才会记录。`,
    })
  }

  async function bulk(action: 'later' | 'skip') {
    const ids = [...selected]
    if (ids.length === 0) return
    setFeedback({ tone: 'info', text: `正在处理 ${ids.length} 个岗位…` })
    try {
      for (const id of ids) {
        if (action === 'later') await api.deferJob(id, 'tomorrow')
        else await api.skipJob(id)
      }
      setSelected(new Set())
      setFeedback({ tone: 'success', text: `已${action === 'later' ? '稍后处理' : '跳过'} ${ids.length} 个岗位` })
      await load(filters)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '批量操作失败' })
    }
  }

  function toggleSelected(jobId: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(jobId)) next.delete(jobId)
      else next.add(jobId)
      return next
    })
  }

  function toggleExpanded(jobId: number) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(jobId)) next.delete(jobId)
      else next.add(jobId)
      return next
    })
  }

  function openM6Confirmation(proposal: ApplicationProposal) {
    setM6Target(proposal)
    setM6ResumeId(activeId ?? null)
    setM6DynamicAccepted(false)
    setM6Approval(null)
    setM6Attempted(false)
    setM6StatusUnknown(false)
  }

  /** Bind the approval and execute it, from the one confirmation.
   *
   * There used to be two screens: one to bind the snapshot, another to look at
   * it and execute. The second guarded against a *time gap* - "confirming once
   * in a queue days earlier is not enough" - which does not exist when the two
   * are seconds apart, and the check it performed by eye is done far more
   * strictly by `validate` on the backend, which refuses a snapshot that no
   * longer matches the job, the resume or the page identity.
   *
   * So the dialog shows the exact job, URL, resume and greeting warning, and
   * one confirmation both binds and executes. What is not merged away: the
   * per-job confirmation itself, the acceptance checkbox, and the fact that a
   * confirmation authorizes exactly one attempt.
   */
  async function confirmAndExecuteM6() {
    if (!m6Target || !m6ResumeId || !m6DynamicAccepted || m6Attempted) return
    setM6Error(null)
    setM6Attempted(true) // One confirmation can dispatch at most one command.
    setM6Busy(true)
    let approval: ApplicationApprovalOut
    try {
      approval = await api.createApplicationApproval(m6Target.job_id, m6ResumeId)
      setM6Approval(approval)
    } catch (err) {
      setM6Attempted(false) // Nothing was dispatched, so this may be retried.
      setM6Busy(false)
      const message = err instanceof ApiError ? err.message : '生成确认失败'
      setM6Error(message)
      setFeedback({ tone: 'error', text: message })
      return
    }
    await executeM6Approval(approval)
  }

  async function executeM6Approval(bound?: ApplicationApprovalOut) {
    // Takes the approval explicitly: when called straight after creating one,
    // React has not committed `m6Approval` yet, and reading the stale state
    // here would dispatch nothing at all.
    const approval = bound ?? m6Approval
    if (!approval || approval.state !== 'pending') return
    setM6Attempted(true) // One confirmation can dispatch at most one command.
    setM6Busy(true)
    // Call immediately in this click handler so the extension bridge receives
    // a real browser user activation before any await occurs.
    const pending = consoleExtension(
      'execute-application', undefined, undefined, undefined, undefined, approval.id,
    )
    try {
      const reply = await pending
      if (!reply.ok) throw new Error(reply.error || '扩展未确认执行结果')
      const attemptedJob = m6Target
      const attemptedResumeId = approval.resume_id
      setFeedback({
        tone: 'warn',
        text: '已执行一次「立即沟通」，并记录为结果待确认。请先在 BOSS 核对沟通是否建立，勿直接重试；再在弹窗中确认，确认后会通过现有唯一记录路径进入「已投递」列表。',
      })
      setM6Target(null)
      setM6Approval(null)
      setApplyNote('M6 单次确认投递；已在 BOSS 人工核对结果')
      setAppliedResume({ resumeId: attemptedResumeId, usage: 'used' })
      setConfirmApplyFromM6(true)
      setConfirmApply(attemptedJob)
      await load(filters)
    } catch (err) {
      // If the worker claimed the attempt and then vanished, retain the real
      // executing state so the user can close it as unknown. Never re-enable
      // this approval's execute button after an ambiguous command.
      setM6StatusUnknown(true)
      try {
        setM6Approval(await api.applicationApproval(approval.id))
        setM6StatusUnknown(false)
      } catch { /* backend may be unavailable; keep modal open and approval non-retryable */ }
      const message = err instanceof Error ? err.message : '执行结果未知；请到 BOSS 人工核对，勿重复点击。'
      setM6Error(message)
      setFeedback({ tone: 'error', text: message })
    } finally {
      setM6Busy(false)
    }
  }

  async function refreshM6Attempt() {
    if (!m6Approval || !m6Attempted) return
    setM6Busy(true)
    try {
      setM6Approval(await api.applicationApproval(m6Approval.id))
      setM6StatusUnknown(false)
    } catch (err) {
      setM6StatusUnknown(true)
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '尝试状态仍无法确认；不会重试' })
    } finally {
      setM6Busy(false)
    }
  }

  async function abandonM6Attempt() {
    if (!m6Approval || m6Approval.state !== 'executing') return
    setM6Busy(true)
    try {
      const closed = await api.abandonApplicationAttempt(m6Approval.id)
      setM6Approval(closed)
      setFeedback({
        tone: 'warn',
        text: '已由你确认结束这次未返回结果的尝试，并记为「结果待确认」。请先到 BOSS 核对；系统不会自动重试。',
      })
      await load(filters)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '结束未知尝试失败' })
    } finally {
      setM6Busy(false)
    }
  }

  const target = summary?.daily_target ?? 10
  const appliedToday = summary?.applied_today ?? 0

  return (
    <>
      <header className="page-head">
        <div>
          <h1>今日投递队列</h1>
          <p>
            AI 只负责推荐。你可以完全手动投递并回来记录，或对单个岗位使用 M6 双重人工确认入口。
          </p>
        </div>
        <div className="page-actions">
          <button type="button" onClick={() => void load(filters)} disabled={loading}>
            刷新
          </button>
          <Link className="btn" to="/quick-capture">
            快速采集
          </Link>
        </div>
      </header>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      {/* Filtering the queue without saying so would be a silent exclusion.
          The count comes from the API and always matches what vanished. */}
      {summary && summary.early_career_hidden > 0 ? (
        <Alert tone="info">
          按当前「候选阶段」设置（
          {summary.early_career_policy === 'exclude'
            ? '排除应届/校招/实习'
            : summary.early_career_policy === 'only'
              ? '只看应届/校招/实习'
              : summary.early_career_policy}
          ），已隐藏 <strong>{summary.early_career_hidden}</strong> 个岗位。{' '}
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={() =>
              updateFilter('include_early_career', !filters.include_early_career)
            }
          >
            {filters.include_early_career ? '重新隐藏' : '查看这些岗位'}
          </button>
          <Link className="btn-ghost btn-sm" to="/strategy">
            修改设置
          </Link>
        </Alert>
      ) : null}


      <div className="grid grid-stats">
        <section className="card stat">
          <span className="stat-label">待处理</span>
          <span className="stat-value">{summary?.pending ?? 0}</span>
        </section>
        <section className="card stat">
          <span className="stat-label">强烈推荐</span>
          <span className="stat-value">{summary?.strong_apply ?? 0}</span>
        </section>
        <section className="card stat">
          <span className="stat-label">建议投递</span>
          <span className="stat-value">{summary?.apply ?? 0}</span>
        </section>
        <section className="card stat">
          <span className="stat-label">稍后处理</span>
          <span className="stat-value">{summary?.later ?? 0}</span>
        </section>
        <section className="card stat">
          <span className="stat-label">今日已投</span>
          <span className="stat-value">
            {appliedToday} <span className="small faint">/ {target}</span>
          </span>
          <span className="stat-hint">
            仅供参考 · {summary?.timezone ?? 'Asia/Tokyo'}
            {summary && summary.replied_today > 0 ? ` · 今日回复 ${summary.replied_today}` : ''}
          </span>
        </section>
      </div>

      <div className="row mb-1">
        <button
          type="button"
          className="btn-sm"
          aria-expanded={showBackfill}
          onClick={() => setShowBackfill(current => !current)}
        >
          {showBackfill ? '收起补录' : '补录已投递（粘贴 BOSS 沟通列表）'}
        </button>
        <span className="small faint">
          在 BOSS 上自己投递过、但这里没记录的岗位，可以在此补上。
        </span>
      </div>

      {showBackfill ? (
        <AppliedBackfillPanel onDone={() => { setShowBackfill(false); void load(filters) }} />
      ) : null}

      <Card title="筛选">
        <div className="filters">
          <div className="field">
            <label htmlFor="q-city">城市</label>
            <select
              id="q-city"
              value={filters.city ?? ''}
              onChange={(e) => updateFilter('city', e.target.value || undefined)}
            >
              <option value="">全部城市</option>
              {cityOptions.map((city) => (
                <option key={city} value={city}>
                  {city}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label htmlFor="q-verdict">AI 结论</label>
            <select
              id="q-verdict"
              value={filters.verdict ?? ''}
              onChange={(e) => updateFilter('verdict', (e.target.value || undefined) as Verdict)}
            >
              <option value="">全部</option>
              <option value="strong_apply">强烈推荐</option>
              <option value="apply">推荐投递</option>
              <option value="maybe">可以考虑</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="q-score">最低匹配分</label>
            <select
              id="q-score"
              value={filters.min_score ?? ''}
              onChange={(e) =>
                updateFilter('min_score', e.target.value ? Number(e.target.value) : undefined)
              }
            >
              <option value="">不限</option>
              <option value="90">90 分以上</option>
              <option value="80">80 分以上</option>
              <option value="70">70 分以上</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="q-status">状态</label>
            <select
              id="q-status"
              value={filters.status ?? ''}
              onChange={(e) => updateFilter('status', (e.target.value || undefined) as JobStatus)}
            >
              <option value="">全部</option>
              <option value="new">待处理</option>
              <option value="reviewed">已查看</option>
              <option value="saved">已收藏</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="q-keyword">关键词</label>
            <form
              onSubmit={(e) => {
                e.preventDefault()
                updateFilter('keyword', keywordInput.trim() || undefined)
              }}
            >
              <input
                id="q-keyword"
                value={keywordInput}
                placeholder="职位 / 公司 / 技能"
                onChange={(e) => setKeywordInput(e.target.value)}
              />
            </form>
          </div>

          <div className="field">
            <label htmlFor="q-sort">排序</label>
            <select
              id="q-sort"
              value={filters.sort ?? 'recommended'}
              onChange={(e) => updateFilter('sort', e.target.value as QueueFilters['sort'])}
            >
              {(data?.facets.sorts ?? ['recommended', 'score', 'newest', 'salary']).map((key) => (
                <option key={key} value={key}>
                  {SORT_LABEL[key] ?? key}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="row mt-1">
          <div className="checkbox-row">
            <input
              id="q-maybe"
              type="checkbox"
              checked={Boolean(filters.include_maybe)}
              onChange={(e) => updateFilter('include_maybe', e.target.checked || undefined)}
            />
            <label htmlFor="q-maybe">包含 Maybe</label>
          </div>
          <button
            type="button"
            className="btn-sm"
            onClick={() => {
              setKeywordInput('')
              setFilters({ sort: 'recommended', limit: 100 })
            }}
          >
            重置筛选
          </button>
        </div>
      </Card>

      {selected.size > 0 ? (
        <Alert tone="info">
          <div className="row-between">
            <span>已选择 {selected.size} 个岗位</span>
            <div className="btn-row">
              <button type="button" className="btn-sm" onClick={() => void bulk('later')}>
                批量稍后处理
              </button>
              <button type="button" className="btn-sm" onClick={() => void bulk('skip')}>
                批量跳过
              </button>
              <button type="button" className="btn-sm" onClick={() => setSelected(new Set())}>
                取消选择
              </button>
            </div>
          </div>
          <div className="small faint mt-1">
            「已投递」不支持批量操作 —— 它代表一次真实的外部行为，需要逐个确认。
          </div>
        </Alert>
      ) : null}

      {loading && items.length === 0 ? <Loading text="正在加载投递队列…" /> : null}

      {!loading && items.length === 0 ? (
        <Card>
          {summary && summary.pending === 0 && summary.applied_today > 0 ? (
            <EmptyState
              icon="✅"
              title="今天的投递队列已经处理完了。"
              text={`今日已记录 ${summary.applied_today} 次投递。`}
              action={
                <Link className="btn" to="/jobs">
                  查看岗位库
                </Link>
              }
            />
          ) : (
            <EmptyState
              icon="📭"
              title="暂无待投递岗位。先采集并分析几个职位吧。"
              action={
                <Link className="btn btn-primary" to="/quick-capture">
                  快速采集
                </Link>
              }
            />
          )}
        </Card>
      ) : null}

      {items.map((proposal) => {
        const busy = busyJob === proposal.job_id
        const isOpen = expanded.has(proposal.job_id)
        return (
          <section className="card" key={proposal.job_id}>
            <div className="row-between">
              <div className="row">
                <input
                  type="checkbox"
                  style={{ width: 'auto' }}
                  aria-label={`选择 ${proposal.title}`}
                  checked={selected.has(proposal.job_id)}
                  onChange={() => toggleSelected(proposal.job_id)}
                />
                <ScoreBadge score={proposal.overall_score} />
                <div>
                  <div className="cell-title">
                    <Link to={`/jobs/${proposal.job_id}`}>{proposal.company}</Link>
                  </div>
                  <div className="cell-sub">{proposal.title}</div>
                </div>
              </div>
              <div className="row">
                <VerdictBadge verdict={proposal.verdict} />
                <StatusBadge status={proposal.job_status} />
                {proposal.proposal_state === 'later' ? (
                  <span className="badge badge-neutral">
                    {STATE_LABEL.later}
                    {proposal.review_after ? ` · ${formatDateTime(proposal.review_after)}` : ''}
                  </span>
                ) : null}
              </div>
            </div>

            <div className="job-card-meta mt-1">
              {proposal.early_career ? (
                <span className="chip chip-bad" title="标题或 JD 显示这是应届/校招/实习岗位">
                  应届/校招
                </span>
              ) : null}
              {proposal.city ? <span className="chip">{proposal.city}</span> : null}
              {proposal.salary_text ? <span className="chip">{proposal.salary_text}</span> : null}
              <span className="chip">{proposal.source}</span>
              {proposal.matched_skills.slice(0, 3).map((skill) => (
                <span key={skill} className="chip chip-good">
                  {skill}
                </span>
              ))}
              {proposal.missing_skills.slice(0, 2).map((skill) => (
                <span key={skill} className="chip chip-bad">
                  缺 {skill}
                </span>
              ))}
            </div>

            {proposal.reasoning_summary ? (
              <p className="small muted mt-1">{proposal.reasoning_summary}</p>
            ) : null}

            {proposal.greeting_message ? (
              <div className="mt-1">
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  onClick={() => toggleExpanded(proposal.job_id)}
                >
                  {isOpen ? '收起招呼语 ▲' : '展开招呼语 ▼'}
                </button>
                {isOpen ? (
                  <div className="greeting mt-1">{proposal.greeting_message}</div>
                ) : (
                  <div className="job-card-summary">{proposal.greeting_message}</div>
                )}
              </div>
            ) : null}

            <div className="btn-row mt-2">
              <Link className="btn btn-sm" to={`/jobs/${proposal.job_id}`}>
                查看岗位
              </Link>
              {proposal.source_url ? (
                <a
                  className="btn btn-sm"
                  href={proposal.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="在新标签页打开岗位原页面，并把招呼语复制到剪贴板"
                  onClick={() => openForApplying(proposal)}
                >
                  去投递 ↗
                </a>
              ) : (
                <span className="small faint" title="该岗位没有可核对的原始链接，可能是早期导入或测试数据">
                  无可用岗位链接
                </span>
              )}
              <button
                type="button"
                className="btn-sm"
                disabled={!proposal.greeting_message}
                onClick={() => void copyGreeting(proposal)}
              >
                {copiedJob === proposal.job_id ? '已复制 ✓' : '复制招呼语'}
              </button>
              <button
                type="button"
                className="btn-primary btn-sm"
                disabled={busy}
                onClick={() => {
                  setApplyNote('')
                  setAppliedResume(
                    activeId ? { resumeId: activeId, usage: 'used' } : UNKNOWN_CHOICE,
                  )
                  setConfirmApplyFromM6(false)
                  setConfirmApply(proposal)
                }}
              >
                标记已投递
              </button>
              {proposal.source_url ? (
                <button
                  type="button"
                  className="btn-sm"
                  disabled={busy || m6Busy}
                  onClick={() => openM6Confirmation(proposal)}
                >
                  单次确认投递（M6）
                </button>
              ) : null}
              <button
                type="button"
                className="btn-sm"
                disabled={busy}
                onClick={() => void run(proposal.job_id, () => api.deferJob(proposal.job_id, 'tomorrow'))}
              >
                稍后处理
              </button>
              <button
                type="button"
                className="btn-sm"
                disabled={busy}
                onClick={() => {
                  setSkipReason('')
                  setSkipTarget(proposal)
                }}
              >
                跳过
              </button>
            </div>
          </section>
        )
      })}

      {confirmApply ? (
        <Modal
          title={confirmApplyFromM6 ? '核对并记录本次投递' : '确认已投递？'}
          onClose={() => {
            setConfirmApply(null)
            setConfirmApplyFromM6(false)
          }}
          footer={
            <>
              <button type="button" onClick={() => {
                setConfirmApply(null)
                setConfirmApplyFromM6(false)
              }}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  const proposal = confirmApply
                  setConfirmApply(null)
                  setConfirmApplyFromM6(false)
                  void run(proposal.job_id, () =>
                    api.markApplied(proposal.job_id, applyNote, appliedResume),
                  )
                }}
              >
                确认已投递
              </button>
            </>
          }
        >
          <p className="mt-0">
            <strong>{confirmApply.company}</strong>
            <br />
            {confirmApply.title}
          </p>
          <p className="muted">
            {confirmApplyFromM6
              ? '请先查看 BOSS：该岗位的沟通是否已经成功建立？只有确认成功后才记录为已投递；取消会保留“结果待确认”的审计记录。'
              : '你已经在招聘平台完成了实际投递或沟通吗？'}
          </p>
          <ResumePicker
            resumes={resumes}
            value={appliedResume}
            onChange={setAppliedResume}
          />
          <div className="field">
            <label htmlFor="apply-note">备注（可选）</label>
            <input
              id="apply-note"
              value={applyNote}
              placeholder="例如：已通过 BOSS 打招呼"
              onChange={(e) => setApplyNote(e.target.value)}
            />
          </div>
          <div className="field-hint">
            {confirmApplyFromM6
              ? '确认后复用现有 mark_applied 唯一路径进入已投递列表；不会创建第二套投递记录。'
              : '这是完全手动记录入口，不会操作招聘网站；M6 单岗位确认入口与此处相互独立。'}
          </div>
        </Modal>
      ) : null}

      {m6Target ? (
        <Modal
          title="确认投递这一个岗位？"
          onClose={() => {
            if (!m6Busy && m6Approval?.state !== 'executing' && !m6StatusUnknown) {
              setM6Target(null)
              setM6Approval(null)
            }
          }}
          footer={
            <>
              <button type="button" disabled={m6Busy || m6Approval?.state === 'executing' || m6StatusUnknown} onClick={() => {
                setM6Target(null)
                setM6Approval(null)
                setM6Error(null)
              }}>
                取消
              </button>
              {m6StatusUnknown ? (
                <button type="button" disabled={m6Busy} onClick={() => void refreshM6Attempt()}>
                  {m6Busy ? '正在刷新…' : '刷新尝试状态'}
                </button>
              ) : null}
              {m6Error ? (
            <Alert tone="error">
              {m6Error}
              {m6Approval?.state === 'pending' || (!m6Approval && m6Attempted) ? (
                <>
                  <br />
                  这次没有点击任何按钮，本确认也没有被消耗。关闭本窗口后可以重新确认一次。
                </>
              ) : null}
            </Alert>
          ) : null}
          {m6Approval?.state === 'executing' ? (
                <button
                  type="button"
                  disabled={m6Busy}
                  onClick={() => void abandonM6Attempt()}
                >
                  {m6Busy ? '正在记录…' : '我已人工核对，结束为结果未知'}
                </button>
              ) : (
                <button
                  type="button"
                  className="btn-primary"
                  disabled={m6Busy || !m6ResumeId || !m6DynamicAccepted || m6Attempted}
                  onClick={() => void confirmAndExecuteM6()}
                >
                  {m6Busy
                    ? '正在执行…'
                    : m6Attempted
                      ? '本确认已发出，不可重试'
                      : '确认并执行一次投递'}
                </button>
              )}
            </>
          }
        >
          <p className="mt-0">
            <strong>{m6Approval?.company ?? m6Target.company}</strong>
            <br />
            {m6Approval?.title ?? m6Target.title}
          </p>
          <Alert tone="warn">
            BOSS 会在点击「立即沟通」时发送平台动态决定的首次招呼语。
            <strong> JobAgent 无法在点击前预览、独立核实或控制其正文</strong>，实际发送内容可能变化。
          </Alert>
          <div className="field">
            <label>岗位链接</label>
            <div className="small">{m6Approval?.canonical_url ?? m6Target.source_url ?? '（无可打开的链接）'}</div>
          </div>
          <div className="field">
            <label htmlFor="m6-resume">本次简历</label>
            <select
              id="m6-resume"
              value={m6ResumeId ?? ''}
              disabled={m6Busy || m6Attempted}
              onChange={(e) => setM6ResumeId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">请选择</option>
              {resumes.filter((resume) => !resume.archived).map((resume) => (
                <option key={resume.id} value={resume.id}>{resume.label}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>首次招呼语</label>
            <div className="greeting">未知（由 BOSS 动态生成，JobAgent 无法预览或控制）</div>
          </div>
          <div className="checkbox-row">
            <input
              id="m6-dynamic-accept"
              type="checkbox"
              checked={m6DynamicAccepted}
              disabled={m6Busy || m6Attempted}
              onChange={(e) => setM6DynamicAccepted(e.target.checked)}
            />
            <label htmlFor="m6-dynamic-accept">
              我接受 BOSS 为这个岗位动态生成未知的首次招呼语，并理解 JobAgent 无法预览、核实或控制正文。
            </label>
          </div>
          <div className="field-hint">
            确认后立即执行这一个岗位的一次尝试；不会批量、后台、自动重试或发送后续消息。
            此处不要求、生成、保存或沿用任何预计正文；AI 也不能代替你勾选确认。
            当前没有可靠的站点成功状态 fixture，因此点击后先记为「结果待确认」，不会自动标记已投递。
          </div>
          {m6Approval?.state === 'executing' ? (
            <Alert tone="warn">
              扩展已领取这次尝试，但没有返回终态。请先到 BOSS 人工核对。上方按钮只把本地记录
              结束为「结果未知」，不会点击网页或重试投递。
            </Alert>
          ) : null}
          {m6StatusUnknown ? (
            <Alert tone="warn">
              后端暂时无法确认本次尝试是否已领取。为避免重复发送，本窗口不会允许再次执行或关闭；
              恢复本地服务后请点“刷新尝试状态”。
            </Alert>
          ) : null}
        </Modal>
      ) : null}

      {skipTarget ? (
        <Modal
          title="跳过这个岗位？"
          onClose={() => setSkipTarget(null)}
          footer={
            <>
              <button type="button" onClick={() => setSkipTarget(null)}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  const proposal = skipTarget
                  setSkipTarget(null)
                  void run(proposal.job_id, () => api.skipJob(proposal.job_id, skipReason))
                }}
              >
                确认跳过
              </button>
            </>
          }
        >
          <p className="mt-0">
            <strong>{skipTarget.company}</strong> · {skipTarget.title}
          </p>
          <div className="field">
            <label>跳过原因（可选）</label>
            <div className="chip-list">
              {skipReasons.map((reason) => (
                <button
                  key={reason}
                  type="button"
                  className={skipReason === reason ? 'btn-primary btn-sm' : 'btn-sm'}
                  onClick={() => setSkipReason(reason)}
                >
                  {reason}
                </button>
              ))}
            </div>
          </div>
          <div className="field-hint">跳过之后仍然可以在岗位详情里「恢复待处理」。</div>
        </Modal>
      ) : null}
    </>
  )
}
