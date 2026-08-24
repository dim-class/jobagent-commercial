import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import {
  Alert,
  Card,
  EmptyState,
  Loading,
  ScoreBadge,
  StatusBadge,
  VerdictBadge,
} from '@/components/ui'
import type {
  ConsoleAttentionOut,
  JobListItem,
  OrchestrationEventOut,
  OrchestrationEventType,
  ResumeListItem,
  TaskCandidateOut,
  TaskCreatePayload,
  TaskOut,
} from '@/types'

const EVENT_TYPE_LABEL: Record<OrchestrationEventType, string> = {
  opened: '已打开',
  reviewed: '已复核',
  dismissed: '已忽略',
}

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

const EMPTY_FORM: TaskCreatePayload = {
  name: '',
  keywords: '',
  city: '',
  experience_text: '',
  education_text: '',
  salary_min: null,
  salary_max: null,
  exclusions: null,
  resume_id: null,
  max_candidates: null,
  min_score: null,
  notes: '',
}

/** "" for an untouched optional field, so it round-trips to `undefined`/`null`. */
function trimmedOrNull(value: string | null | undefined): string | null {
  const trimmed = (value ?? '').trim()
  return trimmed ? trimmed : null
}

export default function ConsolePage() {
  const [tasks, setTasks] = useState<TaskOut[]>([])
  const [tasksLoading, setTasksLoading] = useState(true)
  const [resumes, setResumes] = useState<ResumeListItem[]>([])
  const [feedback, setFeedback] = useState<Feedback>(null)

  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null)
  const [candidates, setCandidates] = useState<TaskCandidateOut[] | null>(null)
  const [candidatesLoading, setCandidatesLoading] = useState(false)

  const [form, setForm] = useState<TaskCreatePayload>(EMPTY_FORM)
  const [exclusionsInput, setExclusionsInput] = useState('')
  const [creating, setCreating] = useState(false)

  const [jobKeyword, setJobKeyword] = useState('')
  const [jobResults, setJobResults] = useState<JobListItem[] | null>(null)
  const [jobSearchLoading, setJobSearchLoading] = useState(false)
  const [associatingJobId, setAssociatingJobId] = useState<number | null>(null)

  const [events, setEvents] = useState<OrchestrationEventOut[] | null>(null)
  const [eventsLoading, setEventsLoading] = useState(false)
  const [eventsError, setEventsError] = useState<string | null>(null)
  const [recordingEventKey, setRecordingEventKey] = useState<string | null>(null)

  const [attention, setAttention] = useState<ConsoleAttentionOut | null>(null)
  const [attentionLoading, setAttentionLoading] = useState(true)
  const [attentionError, setAttentionError] = useState<string | null>(null)

  const loadAttention = useCallback(async () => {
    setAttentionLoading(true)
    setAttentionError(null)
    try {
      const response = await api.getConsoleAttention()
      setAttention(response)
    } catch (err) {
      setAttentionError(err instanceof ApiError ? err.message : '加载待处理事项失败')
      setAttention(null)
    } finally {
      setAttentionLoading(false)
    }
  }, [])

  const loadTasks = useCallback(async () => {
    setTasksLoading(true)
    try {
      const response = await api.listTasks()
      setTasks(response.items)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载任务列表失败' })
    } finally {
      setTasksLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadTasks()
    void loadAttention()
    api.listResumes().then(setResumes).catch(() => setResumes([]))
  }, [loadTasks, loadAttention])

  const loadCandidates = useCallback(async (taskId: number) => {
    setCandidatesLoading(true)
    try {
      const response = await api.listTaskCandidates(taskId)
      setCandidates(response.items)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载候选人失败' })
      setCandidates(null)
    } finally {
      setCandidatesLoading(false)
    }
  }, [])

  const loadEvents = useCallback(async (taskId: number) => {
    setEventsLoading(true)
    setEventsError(null)
    try {
      const response = await api.listTaskEvents(taskId)
      setEvents(response.items)
    } catch (err) {
      setEventsError(err instanceof ApiError ? err.message : '加载事件记录失败')
      setEvents(null)
    } finally {
      setEventsLoading(false)
    }
  }, [])

  function selectTask(taskId: number) {
    setSelectedTaskId(taskId)
    setJobResults(null)
    setJobKeyword('')
    void loadCandidates(taskId)
    void loadEvents(taskId)
  }

  /** Only ever called from an explicit button click - never on mount/select/poll. */
  async function handleRecordEvent(
    taskId: number,
    eventType: OrchestrationEventType,
    jobId: number | null,
    label: string,
  ) {
    const key = `${eventType}-${jobId ?? 'task'}`
    setRecordingEventKey(key)
    try {
      await api.addTaskEvent(taskId, { event_type: eventType, job_id: jobId })
      setFeedback({ tone: 'success', text: `已记录：${label}` })
      await loadEvents(taskId)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '记录事件失败' })
    } finally {
      setRecordingEventKey(null)
    }
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault()
    if (!form.name.trim()) {
      setFeedback({ tone: 'error', text: '请填写任务名称' })
      return
    }
    setCreating(true)
    try {
      const exclusions = exclusionsInput.trim()
        ? exclusionsInput
            .split(/[,，]/)
            .map((item) => item.trim())
            .filter(Boolean)
        : null
      const payload: TaskCreatePayload = {
        name: form.name.trim(),
        keywords: trimmedOrNull(form.keywords),
        city: trimmedOrNull(form.city),
        experience_text: trimmedOrNull(form.experience_text),
        education_text: trimmedOrNull(form.education_text),
        salary_min: form.salary_min ?? null,
        salary_max: form.salary_max ?? null,
        exclusions,
        resume_id: form.resume_id ?? null,
        max_candidates: form.max_candidates ?? null,
        min_score: form.min_score ?? null,
        notes: trimmedOrNull(form.notes),
      }
      const task = await api.createTask(payload)
      setFeedback({ tone: 'success', text: `任务「${task.name}」已创建` })
      setForm(EMPTY_FORM)
      setExclusionsInput('')
      await loadTasks()
      selectTask(task.id)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '创建任务失败' })
    } finally {
      setCreating(false)
    }
  }

  async function handleSearchJobs(event: React.FormEvent) {
    event.preventDefault()
    setJobSearchLoading(true)
    try {
      const response = await api.listJobs({ keyword: jobKeyword.trim() || undefined, limit: 20 })
      setJobResults(response.items)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '搜索岗位失败' })
    } finally {
      setJobSearchLoading(false)
    }
  }

  async function handleAssociate(job: JobListItem) {
    if (!selectedTaskId) return
    setAssociatingJobId(job.id)
    try {
      await api.addTaskCandidate(selectedTaskId, job.id)
      setFeedback({ tone: 'success', text: `已将「${job.title}」关联到当前任务` })
      await loadCandidates(selectedTaskId)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '关联岗位失败' })
    } finally {
      setAssociatingJobId(null)
    }
  }

  const selectedTask = tasks.find((t) => t.id === selectedTaskId) ?? null
  const associatedJobIds = new Set(candidates?.map((c) => c.job_id) ?? [])

  return (
    <>
      <header className="page-head">
        <div>
          <h1>求职任务控制台</h1>
          <p>
            先设定一个任务的搜索条件，再把你在其他渠道找到的岗位关联进来集中查看。
            控制台本身不搜索、不抓取、不投递 —— 岗位仍然通过快速采集 / 浏览器采集 /
            插件等现有方式进入系统。
          </p>
        </div>
      </header>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      <Card
        title="待处理事项"
        sub="汇总现有各页面的真实数据，只读展示，不做任何自动操作 —— 点击可跳转到对应页面处理"
      >
        {attentionLoading ? (
          <Loading />
        ) : attentionError ? (
          <Alert tone="error">
            {attentionError}
            <div className="mt-1">
              <button type="button" className="btn-sm" onClick={() => void loadAttention()}>
                重试
              </button>
            </div>
          </Alert>
        ) : !attention ? null : (
          <div className="grid grid-3">
            <div className="subscore">
              <div className="subscore-label">待分析岗位</div>
              <div className="subscore-value">{attention.analysis_pending.count}</div>
              {attention.analysis_pending.items.length === 0 ? (
                <div className="small faint">没有待分析的岗位</div>
              ) : (
                <ul className="plain-list">
                  {attention.analysis_pending.items.map((job) => (
                    <li key={job.id}>
                      <Link to={`/jobs/${job.id}`}>
                        {job.company} · {job.title}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
              <div className="btn-row mt-1">
                <Link className="btn btn-sm" to="/jobs">
                  打开岗位库
                </Link>
              </div>
            </div>

            <div className="subscore">
              <div className="subscore-label">投递队列待处理</div>
              <div className="subscore-value">{attention.queue.summary.pending}</div>
              {attention.queue.items.length === 0 ? (
                <div className="small faint">队列中没有待处理的推荐岗位</div>
              ) : (
                <ul className="plain-list">
                  {attention.queue.items.map((item) => (
                    <li key={item.job_id}>
                      <Link to={`/jobs/${item.job_id}`}>
                        {item.company} · {item.title}
                      </Link>{' '}
                      <VerdictBadge verdict={item.verdict} />
                    </li>
                  ))}
                </ul>
              )}
              <div className="btn-row mt-1">
                <Link className="btn btn-sm" to="/queue">
                  打开投递队列
                </Link>
              </div>
            </div>

            <div className="subscore">
              <div className="subscore-label">HR沟通待回复/待跟进</div>
              <div className="subscore-value">
                {attention.recruiter.summary.needs_reply + attention.recruiter.summary.follow_up_due}
              </div>
              {attention.recruiter.items.length === 0 ? (
                <div className="small faint">没有待回复或到期跟进的沟通</div>
              ) : (
                <ul className="plain-list">
                  {attention.recruiter.items.map((item) => (
                    <li key={item.id}>
                      <Link to={`/recruiter/${item.id}`}>
                        {item.company || item.job_company || '未命名'} ·{' '}
                        {item.title || item.job_title || '—'}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
              <div className="btn-row mt-1">
                <Link className="btn btn-sm" to="/recruiter">
                  打开HR沟通
                </Link>
              </div>
            </div>

            <div className="subscore">
              <div className="subscore-label">近期面试</div>
              <div className="subscore-value">{attention.interviews.total}</div>
              {attention.interviews.items.length === 0 ? (
                <div className="small faint">{attention.interviews.message}</div>
              ) : (
                <ul className="plain-list">
                  {attention.interviews.items.map((item) => (
                    <li key={item.round_id}>
                      <Link to={`/interviews/${item.process_id}`}>
                        {item.company} · {item.round_label}
                      </Link>{' '}
                      <span className="small faint">{item.day_key}</span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="btn-row mt-1">
                <Link className="btn btn-sm" to="/interviews">
                  打开面试看板
                </Link>
              </div>
            </div>

            <div className="subscore">
              <div className="subscore-label">Offer待决定</div>
              <div className="subscore-value">
                {attention.offers.pending_count + attention.offers.negotiating_count}
              </div>
              {attention.offers.items.length === 0 ? (
                <div className="small faint">没有待决定或谈薪中的 Offer</div>
              ) : (
                <ul className="plain-list">
                  {attention.offers.items.map((offer) => (
                    <li key={offer.id}>
                      <Link to={`/offers/${offer.id}`}>
                        {offer.company} · {offer.title}
                      </Link>{' '}
                      <span className="small faint">{offer.status_label}</span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="btn-row mt-1">
                <Link className="btn btn-sm" to="/offers">
                  打开Offer看板
                </Link>
              </div>
            </div>
          </div>
        )}
      </Card>

      <Card title="新建任务" sub="保存这次求职的搜索条件">
        <form onSubmit={handleCreate}>
          <div className="field-row">
            <div className="field">
              <label htmlFor="t-name">任务名称 *</label>
              <input
                id="t-name"
                required
                value={form.name}
                placeholder="例如：云计算运维-北京"
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="t-keywords">关键词</label>
              <input
                id="t-keywords"
                value={form.keywords ?? ''}
                placeholder="云计算 运维"
                onChange={(e) => setForm({ ...form, keywords: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="t-city">城市</label>
              <input
                id="t-city"
                value={form.city ?? ''}
                placeholder="北京"
                onChange={(e) => setForm({ ...form, city: e.target.value })}
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="t-exp">经验要求</label>
              <input
                id="t-exp"
                value={form.experience_text ?? ''}
                placeholder="3-5年"
                onChange={(e) => setForm({ ...form, experience_text: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="t-edu">学历要求</label>
              <input
                id="t-edu"
                value={form.education_text ?? ''}
                placeholder="本科"
                onChange={(e) => setForm({ ...form, education_text: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="t-salary-min">薪资下限（元/月）</label>
              <input
                id="t-salary-min"
                type="number"
                min={0}
                value={form.salary_min ?? ''}
                onChange={(e) =>
                  setForm({ ...form, salary_min: e.target.value ? Number(e.target.value) : null })
                }
              />
            </div>
            <div className="field">
              <label htmlFor="t-salary-max">薪资上限（元/月）</label>
              <input
                id="t-salary-max"
                type="number"
                min={0}
                value={form.salary_max ?? ''}
                onChange={(e) =>
                  setForm({ ...form, salary_max: e.target.value ? Number(e.target.value) : null })
                }
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="t-exclusions">排除关键词 / 公司（逗号分隔）</label>
              <input
                id="t-exclusions"
                value={exclusionsInput}
                placeholder="外包, 某黑名单公司"
                onChange={(e) => setExclusionsInput(e.target.value)}
              />
              <div className="field-hint">留空表示尚未设置；不需要排除项时无需填写。</div>
            </div>
            <div className="field">
              <label htmlFor="t-resume">匹配简历</label>
              <select
                id="t-resume"
                value={form.resume_id ?? ''}
                onChange={(e) =>
                  setForm({ ...form, resume_id: e.target.value ? Number(e.target.value) : null })
                }
              >
                <option value="">未指定</option>
                {resumes.map((resume) => (
                  <option key={resume.id} value={resume.id}>
                    {resume.label}
                    {resume.is_active ? '（当前AI分析简历）' : ''}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="t-max">候选人上限</label>
              <input
                id="t-max"
                type="number"
                min={1}
                value={form.max_candidates ?? ''}
                onChange={(e) =>
                  setForm({
                    ...form,
                    max_candidates: e.target.value ? Number(e.target.value) : null,
                  })
                }
              />
            </div>
            <div className="field">
              <label htmlFor="t-min-score">最低匹配分</label>
              <input
                id="t-min-score"
                type="number"
                min={0}
                max={100}
                value={form.min_score ?? ''}
                onChange={(e) =>
                  setForm({ ...form, min_score: e.target.value ? Number(e.target.value) : null })
                }
              />
            </div>
          </div>

          <div className="field">
            <label htmlFor="t-notes">备注</label>
            <textarea
              id="t-notes"
              rows={2}
              value={form.notes ?? ''}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
            />
          </div>

          <div className="field">
            <span className="badge badge-neutral">执行模式：manual_review_only</span>
            <div className="field-hint">
              当前只支持「仅人工审核」模式：任务只是保存条件、归集你手动关联的岗位，
              不会自动搜索、点击、翻页或投递。其他执行模式需要先修改
              CLAUDE.md 的安全策略并获得单独授权，本页面不提供切换入口。
            </div>
          </div>

          <div className="row mt-1">
            <button type="submit" className="btn-primary" disabled={creating}>
              {creating ? '创建中…' : '创建任务'}
            </button>
          </div>
        </form>
      </Card>

      <Card title="任务列表" sub={tasksLoading ? '加载中…' : `${tasks.length} 个任务`}>
        {tasksLoading ? (
          <Loading />
        ) : tasks.length === 0 ? (
          <EmptyState icon="🗂️" title="还没有任务" text="先在上面创建一个求职任务。" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>名称</th>
                  <th>条件摘要</th>
                  <th>候选人上限</th>
                  <th>最低匹配分</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => (
                  <tr key={task.id}>
                    <td>{task.name}</td>
                    <td className="small faint">
                      {[task.keywords, task.city, task.experience_text, task.education_text]
                        .filter(Boolean)
                        .join(' · ') || '未设置条件'}
                    </td>
                    <td className="nowrap">{task.max_candidates ?? '不限'}</td>
                    <td className="nowrap">{task.min_score ?? '不限'}</td>
                    <td className="nowrap">
                      <button type="button" className="btn-sm" onClick={() => selectTask(task.id)}>
                        {task.id === selectedTaskId ? '已选中' : '查看候选人'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {selectedTask ? (
        <>
          <Card
            title={`关联已有岗位 - ${selectedTask.name}`}
            sub="从已经采集到系统里的岗位中选择，不会新建或抓取任何岗位"
          >
            <form onSubmit={handleSearchJobs} className="row">
              <input
                value={jobKeyword}
                placeholder="按公司 / 职位关键词搜索岗位库"
                onChange={(e) => setJobKeyword(e.target.value)}
              />
              <button type="submit" className="btn-sm" disabled={jobSearchLoading}>
                {jobSearchLoading ? '搜索中…' : '搜索'}
              </button>
            </form>

            {jobResults === null ? null : jobResults.length === 0 ? (
              <EmptyState
                icon="🔍"
                title="没有找到匹配的岗位"
                text="可以先通过快速采集 / 浏览器采集 / 插件把岗位加入岗位库，再回来关联。"
              />
            ) : (
              <div className="table-wrap mt-1">
                <table>
                  <thead>
                    <tr>
                      <th>公司 / 职位</th>
                      <th>城市</th>
                      <th>薪资</th>
                      <th>状态</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobResults.map((job) => {
                      const already = associatedJobIds.has(job.id)
                      return (
                        <tr key={job.id}>
                          <td>
                            <div className="cell-title">{job.company}</div>
                            <div className="cell-sub">{job.title}</div>
                          </td>
                          <td className="nowrap">{job.city ?? '—'}</td>
                          <td className="nowrap">{job.salary_text ?? '—'}</td>
                          <td className="nowrap">
                            <StatusBadge status={job.status} />
                          </td>
                          <td className="nowrap">
                            <button
                              type="button"
                              className="btn-sm"
                              disabled={already || associatingJobId === job.id}
                              onClick={() => void handleAssociate(job)}
                            >
                              {already
                                ? '已关联'
                                : associatingJobId === job.id
                                  ? '关联中…'
                                  : '关联到该任务'}
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card
            title={`候选人 - ${selectedTask.name}`}
            sub={candidatesLoading ? '加载中…' : `${candidates?.length ?? 0} 个候选人`}
            actions={
              <button
                type="button"
                className="btn-sm"
                disabled={recordingEventKey === 'opened-task'}
                onClick={() =>
                  void handleRecordEvent(selectedTask.id, 'opened', null, '任务已打开')
                }
              >
                标记任务已打开
              </button>
            }
          >
            {candidatesLoading ? (
              <Loading />
            ) : !candidates || candidates.length === 0 ? (
              <EmptyState
                icon="📭"
                title="这个任务下还没有候选人"
                text="在上面搜索并关联已经采集到岗位库里的岗位。"
              />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>匹配分</th>
                      <th>公司 / 职位</th>
                      <th>城市</th>
                      <th>薪资</th>
                      <th>结论</th>
                      <th>状态</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {candidates.map((candidate) => (
                      <tr key={candidate.job_id}>
                        <td className="nowrap">
                          <ScoreBadge score={candidate.analysis?.overall_score ?? null} />
                        </td>
                        <td>
                          <div className="cell-title">
                            <Link to={`/jobs/${candidate.job_id}`}>{candidate.company}</Link>
                          </div>
                          <div className="cell-sub">{candidate.title}</div>
                        </td>
                        <td className="nowrap">{candidate.city ?? '—'}</td>
                        <td className="nowrap">{candidate.salary_text ?? '—'}</td>
                        <td className="nowrap">
                          <VerdictBadge verdict={candidate.analysis?.verdict ?? null} />
                        </td>
                        <td className="nowrap">
                          <StatusBadge status={candidate.status} />
                        </td>
                        <td className="nowrap">
                          <div className="btn-row">
                            <Link className="btn btn-sm" to={`/jobs/${candidate.job_id}`}>
                              查看详情
                            </Link>
                            <Link className="btn btn-sm" to="/queue">
                              投递队列
                            </Link>
                            <button
                              type="button"
                              className="btn-sm"
                              disabled={recordingEventKey === `reviewed-${candidate.job_id}`}
                              onClick={() =>
                                void handleRecordEvent(
                                  selectedTask.id,
                                  'reviewed',
                                  candidate.job_id,
                                  `${candidate.company} 已复核`,
                                )
                              }
                            >
                              标记已复核
                            </button>
                            <button
                              type="button"
                              className="btn-sm"
                              disabled={recordingEventKey === `dismissed-${candidate.job_id}`}
                              onClick={() =>
                                void handleRecordEvent(
                                  selectedTask.id,
                                  'dismissed',
                                  candidate.job_id,
                                  `${candidate.company} 已忽略`,
                                )
                              }
                            >
                              标记已忽略
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card
            title="事件记录"
            sub={eventsLoading ? '加载中…' : `${events?.length ?? 0} 条记录`}
          >
            {eventsLoading ? (
              <Loading />
            ) : eventsError ? (
              <Alert tone="error">
                {eventsError}
                <div className="mt-1">
                  <button
                    type="button"
                    className="btn-sm"
                    onClick={() => void loadEvents(selectedTask.id)}
                  >
                    重试
                  </button>
                </div>
              </Alert>
            ) : !events || events.length === 0 ? (
              <EmptyState
                icon="🕘"
                title="还没有事件记录"
                text="只有在你点击「标记任务已打开 / 已复核 / 已忽略」后才会产生记录，不会自动生成。"
              />
            ) : (
              <ul className="plain-list">
                {events.map((event) => (
                  <li key={event.id}>
                    <span className="badge badge-neutral">{EVENT_TYPE_LABEL[event.event_type]}</span>{' '}
                    {event.job_id ? (
                      <Link to={`/jobs/${event.job_id}`}>岗位 #{event.job_id}</Link>
                    ) : (
                      <span className="small faint">任务本身</span>
                    )}
                    {event.note ? <span className="small faint"> · {event.note}</span> : null}
                    <span className="small faint"> · {new Date(event.created_at).toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      ) : null}
    </>
  )
}
