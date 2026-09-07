import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { ApiError, api, type JobFilters } from '@/api/client'
import { nextJobSelection } from '@/pages/jobSelection'
import { transitionBlockedReason } from '@/pages/statusTransitions'
import {
  Alert,
  Card,
  EmptyState,
  Loading,
  Modal,
  ScoreBadge,
  StatusBadge,
  VerdictBadge,
} from '@/components/ui'
import type {
  BatchAnalyzePlan,
  JobCleanupPlan,
  JobCreatePayload,
  JobListItem,
  JobListResponse,
  JobStatus,
  Verdict,
} from '@/types'

/** One request covers the whole library.

 * The backend already loads and sorts every matching row before slicing, so
 * asking for all of them costs only serialization. Paging existed here as a
 * default nobody chose, and it made 全选 quietly mean "page one" - which is
 * exactly the bug that once reported 选中 0 个 while 18 jobs matched.
 */
const LIST_LIMIT = 2000

interface JobTab {
  key: string
  label: string
  /** Undefined means "every status". Module-level so the reference is stable:
   *  the list reloads on any change to `filters`, and a fresh array literal
   *  each render would reload forever. */
  statuses?: readonly JobStatus[]
}

/** The job library's top-level views.
 *
 * 已投递 groups the whole post-application pipeline on purpose. Applying is a
 * stage, not an endpoint - a job that later got a reply or reached an
 * interview is still one you applied to, and a tab that quietly dropped it
 * would answer "我投过哪些岗位" wrongly the moment anything progressed.
 */
const JOB_TABS: readonly JobTab[] = [
  { key: 'all', label: '全部' },
  { key: 'open', label: '待处理', statuses: ['new', 'reviewed', 'saved'] },
  { key: 'applied', label: '已投递', statuses: ['applied', 'replied', 'interview', 'offer', 'rejected'] },
  { key: 'skipped', label: '已跳过', statuses: ['skipped'] },
]

/** The stages inside 已投递, shown only once more than one of them is in use. */
const PIPELINE_STAGES: readonly { status: JobStatus; label: string }[] = [
  { status: 'applied', label: '待回复' },
  { status: 'replied', label: '已回复' },
  { status: 'interview', label: '面试中' },
  { status: 'offer', label: '已offer' },
  { status: 'rejected', label: '已拒绝' },
]

const EMPTY_FORM: JobCreatePayload = {
  title: '',
  company: '',
  city: '',
  salary_text: '',
  experience_text: '',
  education_text: '',
  source_url: '',
  raw_description: '',
}

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

//: Mirrors `_STATUS_LABEL` in backend/app/services/application_workflow.py.
const STATUS_LABEL: Record<JobStatus, string> = {
  new: '待处理',
  reviewed: '已查看',
  saved: '已收藏',
  skipped: '已跳过',
  applied: '已投递',
  replied: '已回复',
  interview: '面试中',
  offer: '已offer',
  rejected: '已拒绝',
}

export default function JobsPage() {
  const navigate = useNavigate()
  const location = useLocation()

  const [data, setData] = useState<JobListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [busyJobId, setBusyJobId] = useState<number | null>(null)
  const [selectedJobIds, setSelectedJobIds] = useState<Set<number>>(new Set())
  const [batchPlan, setBatchPlan] = useState<BatchAnalyzePlan | null>(null)
  // Plan then confirm, like every other irreversible action here. Reading the
  // plan deletes nothing; the confirmation carries the exact count, so a set
  // that moved in between cancels rather than deleting a different one.
  const [purgePlan, setPurgePlan] = useState<JobCleanupPlan | null>(null)
  const [purgeThreshold, setPurgeThreshold] = useState(40)
  const [purgeBusy, setPurgeBusy] = useState(false)
  const [batchPlanIds, setBatchPlanIds] = useState<number[]>([])
  const [batchBusy, setBatchBusy] = useState(false)
  const [cleanupConfirm, setCleanupConfirm] = useState(false)
  const [cleanupBusy, setCleanupBusy] = useState(false)
  //: Set just before switching the filter to 未分析, consumed when the reloaded
  //: list arrives. A ref, not state: putting it in the effect's deps would make
  //: it select the *stale* list the moment it is set.
  const pendingSelectAll = useRef(false)
  const pendingSelectEarlyCareer = useRef(false)

  const [filters, setFilters] = useState<JobFilters>({ sort: 'score', limit: LIST_LIMIT })
  const [keywordInput, setKeywordInput] = useState('')
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false)

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<JobCreatePayload>(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const load = useCallback(async (active: JobFilters) => {
    setLoading(true)
    try {
      setData(await api.listJobs(active))
    } catch (err) {
      // A failed load never leaves a select-all armed for some later, unrelated
      // refresh to consume.
      pendingSelectAll.current = false
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载岗位失败' })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(filters)
  }, [filters, load])

  useEffect(() => {
    if (!data) return
    const visibleIds = new Set(data.items.map((job) => job.id))
    setBatchPlan(null)
    const selectAll = pendingSelectAll.current
    const selectEarlyCareer = pendingSelectEarlyCareer.current
    pendingSelectAll.current = false
    pendingSelectEarlyCareer.current = false
    setSelectedJobIds((current) => (
      selectEarlyCareer
        ? new Set(data.items.map((job) => job.id))
        : nextJobSelection(current, visibleIds, selectAll)
    ))
  }, [data])

  const cityOptions = useMemo(() => Object.keys(data?.facets.cities ?? {}), [data])

  const activeTab = useMemo(() => {
    const current = filters.status_in
    if (!current?.length) return 'all'
    const key = [...current].sort().join(',')
    return JOB_TABS.find((tab) => tab.statuses && [...tab.statuses].sort().join(',') === key)?.key
      ?? 'all'
  }, [filters.status_in])

  // Counted from the facets, which the backend deliberately computes without
  // the status filter applied - so every tab keeps showing its real size once
  // one of them is selected.
  const tabCounts = useMemo(() => {
    const statuses = data?.facets.statuses ?? {}
    const counts: Record<string, number> = {
      all: Object.values(statuses).reduce((sum, n) => sum + n, 0),
    }
    for (const tab of JOB_TABS) {
      if (!tab.statuses) continue
      counts[tab.key] = tab.statuses.reduce((sum, key) => sum + (statuses[key] ?? 0), 0)
    }
    return counts
  }, [data])

  /** The stages actually in use inside 已投递. One stage needs no sub-filter. */
  const pipelineStages = useMemo(() => {
    const statuses = data?.facets.statuses ?? {}
    const used = PIPELINE_STAGES.filter((stage) => (statuses[stage.status] ?? 0) > 0)
    return used.length > 1 ? used : []
  }, [data])

  function selectTab(tab: JobTab) {
    setBatchPlan(null)
    setSelectedJobIds(new Set())
    // `status` is the stage sub-filter inside a tab; switching tabs drops it,
    // otherwise 已投递 + 已跳过 would silently produce an empty list.
    setFilters((prev) => ({ ...prev, status: undefined, status_in: tab.statuses }))
  }

  function updateFilter<K extends keyof JobFilters>(key: K, value: JobFilters[K]) {
    setFilters((prev) => ({ ...prev, [key]: value }))
  }

  async function handleAnalyze(job: JobListItem, smart: boolean) {
    setBusyJobId(job.id)
    setFeedback({ tone: 'info', text: `正在${smart ? '用高质量模型' : ''}分析「${job.title}」…` })
    try {
      const response = smart ? await api.reanalyzeSmart(job.id) : await api.analyzeJob(job.id)
      setFeedback({
        tone: 'success',
        text: `「${job.title}」分析完成：${response.result.overall_score} 分${
          response.meta.cached ? '（读取缓存，未消耗 API）' : ''
        }`,
      })
      await load(filters)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '分析失败' })
    } finally {
      setBusyJobId(null)
    }
  }

  // Two steps on purpose, the same shape every paid action in this app uses:
  // asking what a run would cost writes nothing and calls no model; only the
  // explicit confirmation spends anything.
  async function openPurgePlan() {
    setPurgeBusy(true)
    setFeedback(null)
    try {
      setPurgePlan(await api.getJobCleanupPlan(purgeThreshold))
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '生成清理计划失败' })
    } finally { setPurgeBusy(false) }
  }

  async function runPurge() {
    if (!purgePlan) return
    setPurgeBusy(true)
    try {
      const result = await api.runJobCleanup(purgePlan.threshold, purgePlan.deletable)
      setPurgePlan(null)
      setFeedback({ tone: 'success', text: result.message })
      await load(filters)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '清理失败' })
    } finally { setPurgeBusy(false) }
  }

  async function openBatchPlan() {
    const jobIds = jobs.filter((job) => selectedJobIds.has(job.id)).map((job) => job.id)
    if (jobIds.length === 0) return
    setBatchBusy(true)
    setFeedback(null)
    try {
      const plan = await api.analyzeBatchPlan(jobIds)
      setBatchPlanIds(jobIds)
      setBatchPlan(plan)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '生成批量分析计划失败' })
    } finally {
      setBatchBusy(false)
    }
  }

  async function runBatchAnalyze() {
    if (!batchPlan || batchPlanIds.length === 0) return
    setBatchBusy(true)
    setFeedback({ tone: 'info', text: `正在分析 ${batchPlan.in_batch} 个岗位…` })
    try {
      // The existing capped endpoint, the existing cache, the existing
      // JobAnalysis rows. Failures are reported, never retried automatically.
      const result = await api.analyzeBatch(false, batchPlanIds)
      setBatchPlan(null)
      setBatchPlanIds([])
      const failedText = result.failed > 0 ? `，失败 ${result.failed} 个（不会自动重试）` : ''
      const deferredText =
        result.requested > result.items.length
          ? `；仍有 ${result.requested - result.items.length} 个岗位超出本批次上限，未处理`
          : ''
      setFeedback({
        tone: result.failed > 0 ? 'warn' : 'success',
        text: `批量分析完成：新分析 ${result.analyzed} 个，读取缓存 ${result.cached} 个${failedText}${deferredText}。`,
      })
      await load(filters)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '批量分析失败' })
    } finally {
      setBatchBusy(false)
    }
  }

  async function handleStatus(job: JobListItem, status: JobStatus) {
    setBusyJobId(job.id)
    try {
      await api.updateJob(job.id, { status })
      // Without this the only visible effect was a small badge change on one
      // row, which reads as "the button did nothing".
      setFeedback({ tone: 'success', text: `「${job.title}」已标记为${STATUS_LABEL[status]}` })
      await load(filters)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '更新状态失败' })
    } finally {
      setBusyJobId(null)
    }
  }

  async function handleDelete(job: JobListItem) {
    if (!window.confirm(
      `确认删除「${job.company} · ${job.title}」？
`
      + '该岗位的分析结果与事件记录会一并删除，且无法恢复。',
    )) return
    setBusyJobId(job.id)
    try {
      await api.deleteJob(job.id)
      setSelectedJobIds((current) => {
        const next = new Set(current)
        next.delete(job.id)
        return next
      })
      setBatchPlan(null)
      setFeedback({ tone: 'success', text: `已删除「${job.title}」` })
      await load(filters)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '删除岗位失败' })
    } finally {
      setBusyJobId(null)
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setFormError(null)
    try {
      const payload: JobCreatePayload = {
        ...form,
        city: form.city?.trim() || null,
        salary_text: form.salary_text?.trim() || null,
        experience_text: form.experience_text?.trim() || null,
        education_text: form.education_text?.trim() || null,
        source_url: form.source_url?.trim() || null,
      }
      const created = await api.createJob(payload)
      setShowForm(false)
      setForm(EMPTY_FORM)
      setFeedback({ tone: 'success', text: `已添加「${created.job.title}」` })
      navigate(`/jobs/${created.job.id}`)
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const existing = err.existingJobId
        setFormError(
          existing ? `${err.message}（岗位 #${existing}），可以直接打开已有记录。` : err.message,
        )
      } else {
        setFormError(err instanceof ApiError ? err.message : '添加岗位失败')
      }
    } finally {
      setSubmitting(false)
    }
  }

  const jobs = data?.items ?? []
  const allVisibleSelected = jobs.length > 0 && jobs.every((job) => selectedJobIds.has(job.id))
  // Truncation is possible for any filter, so it is reported from the response
  // itself rather than assumed away.
  const listTruncated = data !== null && data.total > jobs.length

  function toggleJob(jobId: number) {
    // A plan is bound to the exact set it was generated for; changing the
    // selection clears it rather than leaving stale counts on screen.
    setBatchPlan(null)
    setSelectedJobIds((current) => {
      const next = new Set(current)
      if (next.has(jobId)) next.delete(jobId)
      else next.add(jobId)
      return next
    })
  }

  function toggleAllVisible() {
    setBatchPlan(null)
    setSelectedJobIds(allVisibleSelected ? new Set() : new Set(jobs.map((job) => job.id)))
  }

  /** Narrow the list to unanalysed jobs, then select the whole result.
   *
   * It filters rather than picking out of the current page: the list is capped
   * by `filters.limit` and 按匹配分 sorts unanalysed jobs (no score) last, so
   * they are exactly the rows a full page drops. Selecting "the unanalysed ones
   * I can currently see" reported 0 while 18 existed.
   */
  //: `/jobs?analyzed=false` - the console links here when a search has left
  //: jobs nobody has looked at yet. Landing on the unfiltered list and asking
  //: the user to find the right control is how that prompt gets ignored.
  const landedUnanalyzed = useRef(false)
  useEffect(() => {
    if (landedUnanalyzed.current) return
    if (new URLSearchParams(location.search).get('analyzed') !== 'false') return
    landedUnanalyzed.current = true
    selectUnanalyzed()
  }, [location.search])

  function selectUnanalyzed() {
    setBatchPlan(null)
    setSelectedJobIds(new Set())
    pendingSelectAll.current = true
    setFilters((prev) => ({ ...prev, analyzed: false }))
  }

  function selectEarlyCareerCleanup() {
    setBatchPlan(null)
    setSelectedJobIds(new Set())
    pendingSelectEarlyCareer.current = true
    setKeywordInput('')
    setShowAdvancedFilters(false)
    setFilters({ sort: 'created_at', limit: LIST_LIMIT, early_career_cleanup: true })
  }

  async function runEarlyCareerCleanup() {
    const candidates = jobs.filter((job) => selectedJobIds.has(job.id))
    if (candidates.length === 0) return
    setCleanupBusy(true)
    setCleanupConfirm(false)
    setFeedback({ tone: 'info', text: `正在把 ${candidates.length} 个应届/校招/实习岗位标记为已跳过…` })
    const failed = new Set<number>()
    let completed = 0
    for (const job of candidates) {
      try {
        await api.skipJob(job.id, '当前不是应届生；历史岗位库清理')
        completed += 1
      } catch {
        failed.add(job.id)
      }
    }
    setSelectedJobIds(failed)
    setFeedback({
      tone: failed.size > 0 ? 'warn' : 'success',
      text: `历史岗位清理完成：已剔除 ${completed} 个${failed.size ? `，失败 ${failed.size} 个（未自动重试）` : ''}。岗位与历史记录仍保留在“已跳过”状态。`,
    })
    await load(filters)
    setCleanupBusy(false)
  }

  return (
    <>
      <header className="page-head">
        <div>
          <h1>岗位库</h1>
          <p>
            共 {data?.total ?? 0} 个岗位
            {filters.min_score || filters.city || filters.verdict || filters.status || filters.status_in || filters.max_required_years || filters.min_heuristic || filters.keyword
              || filters.early_career_cleanup
              ? '（已筛选）'
              : ''}
          </p>
        </div>
        <div className="page-actions">
          <button type="button" onClick={() => void load(filters)} disabled={loading}>
            刷新
          </button>
          <button type="button" className="btn-primary" onClick={() => setShowForm(true)}>
            添加岗位
          </button>
        </div>
      </header>

      {/* Sticky: the action buttons live far down the table, and a result the
          user has to scroll up to read is a result they never see. */}
      {feedback ? (
        <div style={{ position: 'sticky', top: 0, zIndex: 20 }}>
          <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
            {feedback.text}
          </Alert>
        </div>
      ) : null}

      <Card title="查找岗位" sub="先用关键词快速搜索；需要时再展开高级筛选">
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault()
            updateFilter('keyword', keywordInput.trim() || undefined)
          }}
        >
          <input
            id="f-keyword"
            value={keywordInput}
            placeholder="搜索公司 / 职位 / JD"
            onChange={(e) => setKeywordInput(e.target.value)}
          />
          <button type="submit" className="btn-primary">搜索</button>
          <button
            type="button"
            className="btn-sm"
            aria-expanded={showAdvancedFilters}
            onClick={() => setShowAdvancedFilters((current) => !current)}
          >
            {showAdvancedFilters ? '收起高级筛选' : '展开高级筛选'}
          </button>
        </form>

        {showAdvancedFilters ? <div className="filters mt-1">
          <div className="field">
            <label htmlFor="f-city">城市</label>
            <select
              id="f-city"
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
            <label htmlFor="f-score">最低匹配分</label>
            <select
              id="f-score"
              value={filters.min_score ?? ''}
              onChange={(e) =>
                updateFilter('min_score', e.target.value ? Number(e.target.value) : undefined)
              }
            >
              <option value="">不限</option>
              <option value="90">90 分以上</option>
              <option value="80">80 分以上</option>
              <option value="70">70 分以上</option>
              <option value="60">60 分以上</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="f-verdict">AI 结论</label>
            <select
              id="f-verdict"
              value={filters.verdict ?? ''}
              onChange={(e) => updateFilter('verdict', (e.target.value || undefined) as Verdict)}
            >
              <option value="">全部</option>
              <option value="strong_apply">强烈推荐</option>
              <option value="apply">推荐投递</option>
              <option value="maybe">可以考虑</option>
              <option value="skip">不建议</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="f-heuristic">预估匹配（未分析岗位）</label>
            <select
              id="f-heuristic"
              value={filters.min_heuristic ?? ''}
              onChange={(e) =>
                updateFilter('min_heuristic', e.target.value ? Number(e.target.value) : undefined)
              }
            >
              <option value="">不限</option>
              <option value="45">45 以上（排除几乎必然不匹配的）</option>
              <option value="55">55 以上（推荐率约 3 倍）</option>
              <option value="65">65 以上（推荐率最高的一档）</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="f-years">经验要求</label>
            <select
              id="f-years"
              value={filters.max_required_years ?? ''}
              onChange={(e) =>
                updateFilter(
                  'max_required_years',
                  e.target.value ? Number(e.target.value) : undefined,
                )
              }
            >
              <option value="">不限</option>
              <option value="2">最多 2 年</option>
              <option value="4">最多 4 年（保留 3-5 年，排除 5-10 年）</option>
              <option value="6">最多 6 年</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="f-analyzed">分析状态</label>
            <select
              id="f-analyzed"
              value={filters.analyzed === undefined ? '' : String(filters.analyzed)}
              onChange={(e) =>
                updateFilter('analyzed', e.target.value === '' ? undefined : e.target.value === 'true')
              }
            >
              <option value="">全部</option>
              <option value="false">未分析</option>
              <option value="true">已分析</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="f-sort">排序</label>
            <select
              id="f-sort"
              value={filters.sort ?? 'score'}
              onChange={(e) => updateFilter('sort', e.target.value as JobFilters['sort'])}
            >
              <option value="score">按匹配分</option>
              <option value="created_at">按添加时间</option>
              <option value="company">按公司名</option>
            </select>
          </div>
        </div> : null}

        <div className="row mt-1">
          <button
            type="button"
            className="btn-sm"
            disabled={loading || cleanupBusy}
            title="使用与自动搜索相同的确定性规则，筛选并全选尚未处理的应届、校招、实习等岗位"
            onClick={selectEarlyCareerCleanup}
          >
            筛选并全选应届/校招/实习岗位
          </button>
          <button
            type="button"
            className="btn-sm"
            onClick={() => {
              setKeywordInput('')
              setFilters({ sort: 'score', limit: LIST_LIMIT })
            }}
          >
            重置筛选
          </button>
          <span className="small faint">
            提示：AI 分析结果会缓存，重复点击不会重复消耗 API 额度。
          </span>
        </div>
      </Card>

      <Card title="岗位列表" sub={loading ? '加载中…' : `${jobs.length} 条`}>
        <div className="tabs" role="tablist" aria-label="岗位分类">
          {JOB_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              role="tab"
              className="tab"
              aria-selected={activeTab === tab.key}
              onClick={() => selectTab(tab)}
            >
              {tab.label}
              <span className="tab-count">{tabCounts[tab.key] ?? 0}</span>
            </button>
          ))}
        </div>

        {activeTab === 'applied' && pipelineStages.length ? (
          <div className="chip-list mb-1">
            <button
              type="button"
              className="btn-sm"
              aria-pressed={!filters.status}
              onClick={() => updateFilter('status', undefined)}
            >
              全部阶段
            </button>
            {pipelineStages.map((stage) => (
              <button
                key={stage.status}
                type="button"
                className="btn-sm"
                aria-pressed={filters.status === stage.status}
                onClick={() => updateFilter('status', stage.status)}
              >
                {stage.label}
              </button>
            ))}
          </div>
        ) : null}

        {loading && jobs.length === 0 ? (
          <Loading />
        ) : jobs.length === 0 ? (
          <EmptyState
            icon="🔍"
            title="没有符合条件的岗位"
            text="换个筛选条件，或粘贴一份新的 JD。"
            action={
              <button type="button" className="btn-primary" onClick={() => setShowForm(true)}>
                添加岗位
              </button>
            }
          />
        ) : (
          <>
          <div className="row mb-1">
            <button type="button" className="btn-sm" onClick={toggleAllVisible}>
              {allVisibleSelected ? '取消全选' : `全选当前 ${jobs.length} 个`}
            </button>
            <button
              type="button"
              className="btn-sm"
              disabled={loading}
              title="把列表筛选为未分析岗位，并全选筛选结果"
              onClick={selectUnanalyzed}
            >
              筛选并全选未分析岗位
            </button>
            <button
              type="button"
              className="btn-sm"
              disabled={selectedJobIds.size === 0 || batchBusy}
              onClick={() => void openBatchPlan()}
            >
              批量 AI 分析
            </button>
            {filters.early_career_cleanup && selectedJobIds.size > 0 ? (
              <button
                type="button"
                className="btn-sm"
                disabled={cleanupBusy}
                onClick={() => setCleanupConfirm(true)}
              >
                批量标记为已跳过
              </button>
            ) : null}
            {selectedJobIds.size > 0 ? (
              <>
                <span className="small">已选择 {selectedJobIds.size} 个岗位</span>
                <button
                  type="button"
                  className="btn-sm"
                  onClick={() => {
                    setBatchPlan(null)
                    setSelectedJobIds(new Set())
                  }}
                >
                  清空选择
                </button>
              </>
            ) : <span className="small faint">可全选，也可逐项选择；选择本身不会执行任何操作。</span>}
            {listTruncated ? (
              <span className="small faint">
                共 {data?.total} 个符合条件，超过单次上限 {LIST_LIMIT}，当前只显示前 {jobs.length} 个；
                全选只覆盖显示出来的部分。可以先用筛选条件缩小范围。
              </span>
            ) : null}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>
                    <input
                      type="checkbox"
                      aria-label="全选当前岗位"
                      checked={allVisibleSelected}
                      onChange={toggleAllVisible}
                    />
                  </th>
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
                {jobs.map((job) => {
                  const busy = busyJobId === job.id
                  return (
                    <tr key={job.id}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`选择 ${job.company} ${job.title}`}
                          checked={selectedJobIds.has(job.id)}
                          onChange={() => toggleJob(job.id)}
                        />
                      </td>
                      <td className="nowrap">
                        <ScoreBadge score={job.latest_analysis?.overall_score ?? null} />
                        {!job.latest_analysis && job.heuristic_score !== null ? (
                          <div
                            className="small faint"
                            title="本地免费预估，不是 AI 结论。在已分析的岗位上，它与真实分相关系数 0.74"
                          >
                            预估 {job.heuristic_score}
                          </div>
                        ) : null}
                      </td>
                      <td>
                        <div className="cell-title">
                          <Link to={`/jobs/${job.id}`}>{job.company}</Link>
                        </div>
                        <div className="cell-sub">{job.title}</div>
                      </td>
                      <td className="nowrap">{job.city ?? '—'}</td>
                      <td className="nowrap">{job.salary_text ?? '—'}</td>
                      <td className="nowrap">
                        <VerdictBadge verdict={job.latest_analysis?.verdict ?? null} />
                      </td>
                      <td className="nowrap">
                        <StatusBadge status={job.status} />
                      </td>
                      <td className="nowrap">
                        <div className="btn-row">
                          <Link className="btn btn-sm" to={`/jobs/${job.id}`}>
                            查看
                          </Link>
                          <button
                            type="button"
                            className="btn-sm"
                            disabled={busy}
                            onClick={() => void handleAnalyze(job, false)}
                          >
                            {busy ? '处理中…' : 'AI分析'}
                          </button>
                          <button
                            type="button"
                            className="btn-sm"
                            disabled={busy}
                            title="使用高质量模型重新分析（消耗更多额度）"
                            onClick={() => void handleAnalyze(job, true)}
                          >
                            高质量重分析
                          </button>
                          {/* Two ways a status click can only disappoint:
                              re-sending the status a job already has returns
                              200 and changes nothing, and a transition the
                              workflow forbids (跳过 on an applied job) returns
                              422. Both look like "点了没反应", so the button is
                              disabled with the reason on hover instead. */}
                          {(['saved', 'skipped'] as const).map((target) => {
                            const blocked = transitionBlockedReason(job.status, target)
                            return (
                              <button
                                key={target}
                                type="button"
                                className="btn-sm"
                                disabled={busy || blocked !== null}
                                title={blocked ?? undefined}
                                onClick={() => void handleStatus(job, target)}
                              >
                                {target === 'saved' ? '收藏' : '跳过'}
                              </button>
                            )
                          })}
                          <button
                            type="button"
                            className="btn-sm"
                            disabled={busy}
                            title="永久删除该岗位及其分析与事件记录"
                            onClick={() => void handleDelete(job)}
                          >
                            删除
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          </>
        )}
      </Card>

      <div className="row mt-1">
        <button
          type="button"
          className="btn-sm"
          disabled={purgeBusy}
          onClick={() => void openPurgePlan()}
        >
          {purgeBusy ? '处理中…' : `清理低分岗位（低于 ${purgeThreshold} 分）`}
        </button>
        <select
          value={purgeThreshold}
          onChange={e => setPurgeThreshold(Number(e.target.value))}
          disabled={purgeBusy}
        >
          <option value={30}>30 分以下</option>
          <option value={40}>40 分以下</option>
          <option value={50}>50 分以下</option>
          <option value={60}>60 分以下</option>
        </select>
        <span className="small faint">
          删除是不可逆的。投过、跳过、回复过的岗位一律保留，不论分数。
        </span>
      </div>

      {purgePlan ? (
        <Modal
          title="确认清理低分岗位"
          onClose={() => setPurgePlan(null)}
          footer={
            <>
              <button className="btn btn-secondary" onClick={() => setPurgePlan(null)}>
                取消
              </button>
              <button
                className="btn btn-danger"
                disabled={purgeBusy || purgePlan.deletable === 0}
                onClick={() => void runPurge()}
              >
                {purgeBusy ? '删除中…' : `确认删除 ${purgePlan.deletable} 个岗位`}
              </button>
            </>
          }
        >
          <ul className="small">
            <li>已分析的岗位：{purgePlan.analyzed} 个</li>
            <li>
              低于 {purgePlan.threshold} 分且从未处理过：
              <strong>{purgePlan.deletable} 个 —— 会被删除</strong>
            </li>
            <li>低于 {purgePlan.threshold} 分但有操作记录：{purgePlan.protected} 个（保留）</li>
            <li>还没分析过、没有分数：{purgePlan.unscored} 个（不动）</li>
          </ul>
          <p className="small text-warn">
            删除不可撤销，岗位的分析记录也会一并消失。
            {purgePlan.protected > 0
              ? `已投递／跳过／回复过的 ${purgePlan.protected} 个岗位会原样保留——删掉它们会让投递漏斗和简历转化率对不上。`
              : ''}
          </p>
        </Modal>
      ) : null}

      {batchPlan ? (
        <Modal
          title="确认批量 AI 分析"
          onClose={() => setBatchPlan(null)}
          footer={
            <>
              <button type="button" onClick={() => setBatchPlan(null)}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={batchBusy || batchPlan.in_batch === 0}
                onClick={() => void runBatchAnalyze()}
              >
                {batchBusy ? '分析中…' : `确认分析 ${batchPlan.in_batch} 个岗位`}
              </button>
            </>
          }
        >
          <ul className="small">
            <li>选中岗位：{batchPlan.selected} 个</li>
            <li>本批次上限（MAX_ANALYSES_PER_RUN）：{batchPlan.limit} 个</li>
            <li>本次实际处理：{batchPlan.in_batch} 个</li>
            <li>已命中缓存（不消耗 API）：{batchPlan.cached} 个</li>
            <li>
              <strong>预计新增 AI 调用：{batchPlan.pending} 次</strong>
              （模型 {batchPlan.model}，简历「{batchPlan.resume_name}」）
            </li>
          </ul>
          {batchPlan.deferred > 0 ? (
            <Alert tone="warn">
              选中的岗位超出本批次上限，本次只会处理前 {batchPlan.in_batch} 个，剩余{' '}
              {batchPlan.deferred} 个不会被分析。系统不会自行放宽上限；如需继续，请在本次完成后再次选择并确认。
            </Alert>
          ) : null}
          {batchPlan.pending > 0 ? (
            <Alert tone="info">
              新增的 {batchPlan.pending} 次调用会产生 API 费用；命中缓存的部分不额外收费。
              失败的岗位只会被报告，不会自动重试。分析不会投递、不会收藏、不会发消息，
              也不会写入任何人工决定的状态（仅沿用「新建 → 已查看」这一既有规则）。
            </Alert>
          ) : (
            <Alert tone="success">全部命中缓存，本次不会产生新的 API 费用。</Alert>
          )}
          {batchPlan.pending > 0 ? (
            <p className="small faint">
              每个新调用大约需要 10 秒，本次预计 {batchPlan.pending} 次，约需{' '}
              {Math.max(1, Math.round((batchPlan.pending * 10) / 60))} 分钟。请保持本页打开，
              完成后顶部会显示成功 / 缓存 / 失败数量。
            </p>
          ) : null}
          {batchPlan.missing_job_ids.length > 0 ? (
            <Alert tone="warn">
              有 {batchPlan.missing_job_ids.length} 个岗位已不存在，已从本次计划中剔除。
            </Alert>
          ) : null}
        </Modal>
      ) : null}

      {cleanupConfirm ? (
        <Modal
          title="确认剔除应届岗位？"
          onClose={() => setCleanupConfirm(false)}
          footer={
            <>
              <button type="button" disabled={cleanupBusy} onClick={() => setCleanupConfirm(false)}>
                取消
              </button>
              <button type="button" className="btn-primary" disabled={cleanupBusy} onClick={() => void runEarlyCareerCleanup()}>
                确认标记 {selectedJobIds.size} 个为已跳过
              </button>
            </>
          }
        >
          <Alert tone="warn">
            这些岗位由与自动搜索相同的确定性规则识别为应届、校招、校园招聘、毕业生、管培生或实习岗位。
            操作会逐条追加“已跳过”记录，不会删除岗位、分析或历史事件，也不会调用 AI。
          </Alert>
          <p className="small">
            已投递、已回复、面试中、Offer、已拒绝及已经跳过的岗位不会出现在本清理列表，也不会被改写。
          </p>
        </Modal>
      ) : null}

      {showForm ? (
        <Modal
          title="添加岗位"
          onClose={() => setShowForm(false)}
          footer={
            <>
              <button type="button" onClick={() => setShowForm(false)}>
                取消
              </button>
              <button type="submit" form="job-form" className="btn-primary" disabled={submitting}>
                {submitting ? '保存中…' : '保存岗位'}
              </button>
            </>
          }
        >
          {formError ? <Alert tone="error">{formError}</Alert> : null}
          <form id="job-form" onSubmit={handleSubmit}>
            <div className="field-row">
              <div className="field">
                <label htmlFor="j-title">职位名称 *</label>
                <input
                  id="j-title"
                  required
                  value={form.title}
                  placeholder="云计算工程师"
                  onChange={(e) => setForm({ ...form, title: e.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="j-company">公司名称 *</label>
                <input
                  id="j-company"
                  required
                  value={form.company}
                  placeholder="某某科技"
                  onChange={(e) => setForm({ ...form, company: e.target.value })}
                />
              </div>
            </div>

            <div className="field-row">
              <div className="field">
                <label htmlFor="j-city">城市</label>
                <input
                  id="j-city"
                  value={form.city ?? ''}
                  placeholder="北京"
                  onChange={(e) => setForm({ ...form, city: e.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="j-salary">薪资</label>
                <input
                  id="j-salary"
                  value={form.salary_text ?? ''}
                  placeholder="20k-30k"
                  onChange={(e) => setForm({ ...form, salary_text: e.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="j-exp">经验要求</label>
                <input
                  id="j-exp"
                  value={form.experience_text ?? ''}
                  placeholder="1-3年"
                  onChange={(e) => setForm({ ...form, experience_text: e.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="j-edu">学历要求</label>
                <input
                  id="j-edu"
                  value={form.education_text ?? ''}
                  placeholder="本科"
                  onChange={(e) => setForm({ ...form, education_text: e.target.value })}
                />
              </div>
            </div>

            <div className="field">
              <label htmlFor="j-url">岗位链接（可选）</label>
              <input
                id="j-url"
                value={form.source_url ?? ''}
                placeholder="https://…"
                onChange={(e) => setForm({ ...form, source_url: e.target.value })}
              />
            </div>

            <div className="field">
              <label htmlFor="j-jd">JD 原文 *</label>
              <textarea
                id="j-jd"
                required
                rows={12}
                value={form.raw_description}
                placeholder="把招聘网站上的岗位职责与任职要求整段粘贴到这里…"
                onChange={(e) => setForm({ ...form, raw_description: e.target.value })}
              />
              <div className="field-hint">
                原文会完整保留；系统只做空白与项目符号的规范化，并据此计算去重哈希。
              </div>
            </div>
          </form>
        </Modal>
      ) : null}
    </>
  )
}
