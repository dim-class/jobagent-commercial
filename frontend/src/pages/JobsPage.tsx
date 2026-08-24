import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ApiError, api, type JobFilters } from '@/api/client'
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
import type { JobCreatePayload, JobListItem, JobListResponse, JobStatus, Verdict } from '@/types'

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

export default function JobsPage() {
  const navigate = useNavigate()

  const [data, setData] = useState<JobListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [busyJobId, setBusyJobId] = useState<number | null>(null)

  const [filters, setFilters] = useState<JobFilters>({ sort: 'score', limit: 100 })
  const [keywordInput, setKeywordInput] = useState('')

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<JobCreatePayload>(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const load = useCallback(async (active: JobFilters) => {
    setLoading(true)
    try {
      setData(await api.listJobs(active))
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载岗位失败' })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(filters)
  }, [filters, load])

  const cityOptions = useMemo(() => Object.keys(data?.facets.cities ?? {}), [data])

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

  async function handleStatus(job: JobListItem, status: JobStatus) {
    setBusyJobId(job.id)
    try {
      await api.updateJob(job.id, { status })
      await load(filters)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '更新状态失败' })
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

  return (
    <>
      <header className="page-head">
        <div>
          <h1>岗位库</h1>
          <p>
            共 {data?.total ?? 0} 个岗位
            {filters.min_score || filters.city || filters.verdict || filters.status || filters.keyword
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

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      <Card title="筛选">
        <div className="filters">
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
            <label htmlFor="f-status">状态</label>
            <select
              id="f-status"
              value={filters.status ?? ''}
              onChange={(e) => updateFilter('status', (e.target.value || undefined) as JobStatus)}
            >
              <option value="">全部</option>
              <option value="new">待处理</option>
              <option value="reviewed">已查看</option>
              <option value="saved">已收藏</option>
              <option value="skipped">已跳过</option>
              <option value="applied">已投递</option>
              <option value="replied">已回复</option>
              <option value="interview">面试中</option>
              <option value="offer">已offer</option>
              <option value="rejected">已拒绝</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="f-keyword">关键词</label>
            <form
              onSubmit={(e) => {
                e.preventDefault()
                updateFilter('keyword', keywordInput.trim() || undefined)
              }}
            >
              <input
                id="f-keyword"
                value={keywordInput}
                placeholder="公司 / 职位 / JD"
                onChange={(e) => setKeywordInput(e.target.value)}
              />
            </form>
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
        </div>

        <div className="row mt-1">
          <button
            type="button"
            className="btn-sm"
            onClick={() => {
              setKeywordInput('')
              setFilters({ sort: 'score', limit: 100 })
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
                {jobs.map((job) => {
                  const busy = busyJobId === job.id
                  return (
                    <tr key={job.id}>
                      <td className="nowrap">
                        <ScoreBadge score={job.latest_analysis?.overall_score ?? null} />
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
                          <button
                            type="button"
                            className="btn-sm"
                            disabled={busy}
                            onClick={() => void handleStatus(job, 'saved')}
                          >
                            收藏
                          </button>
                          <button
                            type="button"
                            className="btn-sm"
                            disabled={busy}
                            onClick={() => void handleStatus(job, 'skipped')}
                          >
                            跳过
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

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
