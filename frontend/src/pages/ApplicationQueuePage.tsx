import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api, type QueueFilters } from '@/api/client'
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
import type { ApplicationProposal, JobStatus, QueueResponse, Verdict } from '@/types'

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
  const [keywordInput, setKeywordInput] = useState('')

  // Confirmation dialogs: applying and skipping are real decisions.
  const [confirmApply, setConfirmApply] = useState<ApplicationProposal | null>(null)
  const [applyNote, setApplyNote] = useState('')
  // Which resume the user says they actually submitted. Defaults to the
  // active analysis resume as a convenience; never substituted silently.
  const [appliedResume, setAppliedResume] = useState<ResumeChoice>(UNKNOWN_CHOICE)
  const { resumes, activeId } = useSelectableResumes()
  const [skipTarget, setSkipTarget] = useState<ApplicationProposal | null>(null)
  const [skipReason, setSkipReason] = useState('')

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

  const target = summary?.daily_target ?? 10
  const appliedToday = summary?.applied_today ?? 0

  return (
    <>
      <header className="page-head">
        <div>
          <h1>今日投递队列</h1>
          <p>
            AI 推荐，你来决定。JobAgent 不会替你投递 —— 在招聘平台完成实际投递后，回到这里记录。
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
                  setConfirmApply(proposal)
                }}
              >
                标记已投递
              </button>
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
          title="确认已投递？"
          onClose={() => setConfirmApply(null)}
          footer={
            <>
              <button type="button" onClick={() => setConfirmApply(null)}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  const proposal = confirmApply
                  setConfirmApply(null)
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
          <p className="muted">你已经在招聘平台完成了实际投递或沟通吗？</p>
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
            JobAgent 不会替你投递，也不会向招聘方发送任何消息 —— 这里只记录你自己完成的动作。
          </div>
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
