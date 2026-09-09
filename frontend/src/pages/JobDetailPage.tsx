import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import ApplicationTimeline from '@/components/ApplicationTimeline'
import {
  DeadlineChip,
  OFFER_STATUS_TONE,
  RecordOfferDialog,
  money,
} from '@/components/offer'
import {
  AddRoundDialog,
  CompleteRoundDialog,
  PROCESS_STATUS_LABEL,
  RoundLine,
} from '@/components/interview'
import {
  ResumePicker,
  UNKNOWN_CHOICE,
  useSelectableResumes,
  type ResumeChoice,
} from '@/components/ResumePicker'
import {
  Alert,
  BulletList,
  Card,
  ChipList,
  Loading,
  Modal,
  ScoreBadge,
  StatusBadge,
  SubScore,
  VerdictBadge,
  formatDateTime,
} from '@/components/ui'
import type {
  AnalysisResponse,
  ApplicationEventOut,
  InterviewProcessOut,
  InterviewRound,
  InterviewRoundOut,
  JobDetail,
  JobResumeAnalyses,
  OfferOut,
  ResumeAnalysisCell,
} from '@/types'

const INTERVIEW_ROUNDS: InterviewRound[] = ['HR', '一面', '二面', '技术面', '终面', '其他']

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

export default function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const id = Number(jobId)

  const [job, setJob] = useState<JobDetail | null>(null)
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null)
  const [resumeScores, setResumeScores] = useState<JobResumeAnalyses | null>(null)
  const [selectedVariants, setSelectedVariants] = useState<Set<number>>(new Set())
  // Set when the backend reports how many uncached analyses a comparison
  // would run. The user must confirm that cost before anything is spent.
  const [pendingCost, setPendingCost] = useState<number | null>(null)
  const [comparing, setComparing] = useState(false)
  const [processes, setProcesses] = useState<InterviewProcessOut[]>([])
  const [addingRound, setAddingRound] = useState(false)
  const [completingRound, setCompletingRound] = useState<InterviewRoundOut | null>(
    null,
  )
  const [interviewBusy, setInterviewBusy] = useState(false)
  const [offers, setOffers] = useState<OfferOut[]>([])
  const [recordingOffer, setRecordingOffer] = useState(false)
  const [offerBusy, setOfferBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [analyzing, setAnalyzing] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [copied, setCopied] = useState(false)
  const [showRawJd, setShowRawJd] = useState(false)
  const [timeline, setTimeline] = useState<ApplicationEventOut[]>([])
  const [confirmApply, setConfirmApply] = useState(false)
  const [appliedResume, setAppliedResume] = useState<ResumeChoice>(UNKNOWN_CHOICE)
  const { resumes, activeId } = useSelectableResumes()

  // Suggest the active analysis resume when the dialog opens; the user is
  // free to change it, and an unanswered dialog records `unknown`.
  useEffect(() => {
    if (confirmApply && activeId) setAppliedResume({ resumeId: activeId, usage: 'used' })
  }, [confirmApply, activeId])
  const [showInterview, setShowInterview] = useState(false)
  const [acting, setActing] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const detail = await api.getJob(id)
      setJob(detail)
      try {
        setTimeline(await api.applicationEvents(id))
      } catch {
        setTimeline([])
      }
      try {
        setAnalysis(await api.getAnalysis(id))
      } catch (err) {
        // 404 simply means "not analyzed yet" - not an error state.
        if (!(err instanceof ApiError && err.status === 404)) throw err
        setAnalysis(null)
      }
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载岗位失败' })
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    if (Number.isFinite(id)) void load()
  }, [id, load])

  // Reading stored per-variant scores never calls the model.
  const loadResumeScores = useCallback(async () => {
    try {
      setResumeScores(await api.jobResumeAnalyses(id))
    } catch {
      setResumeScores(null)
    }
  }, [id])

  useEffect(() => {
    void loadResumeScores()
  }, [loadResumeScores, analysis])

  const loadInterviews = useCallback(async () => {
    try {
      setProcesses((await api.jobInterviews(id)).items)
    } catch {
      setProcesses([])
    }
  }, [id])

  useEffect(() => {
    void loadInterviews()
  }, [loadInterviews])

  const loadOffers = useCallback(async () => {
    try {
      setOffers((await api.jobOffers(id)).items)
    } catch {
      setOffers([])
    }
  }, [id])

  useEffect(() => {
    void loadOffers()
  }, [loadOffers])

  async function runAnalysis(mode: 'fast' | 'smart' | 'force') {
    setAnalyzing(true)
    setFeedback({ tone: 'info', text: mode === 'smart' ? '正在用高质量模型分析…' : '正在分析…' })
    try {
      const response =
        mode === 'smart'
          ? await api.reanalyzeSmart(id)
          : await api.analyzeJob(id, mode === 'force')
      setAnalysis(response)
      setFeedback({
        tone: 'success',
        text: response.meta.cached
          ? '已读取缓存结果（未消耗 API 额度）'
          : `分析完成，模型 ${response.meta.model}`,
      })
      setJob(await api.getJob(id))
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '分析失败' })
    } finally {
      setAnalyzing(false)
    }
  }

  /** Run a workflow action, then refresh the job and its timeline. */
  async function act(action: () => Promise<{ message: string }>) {
    setActing(true)
    try {
      const result = await action()
      setFeedback({ tone: 'success', text: result.message })
      await load()
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '操作失败' })
    } finally {
      setActing(false)
    }
  }

  /** Open this job's recruiter thread, reusing an existing one when there is
   *  exactly one - so the same job does not sprout duplicate threads. */
  async function openRecruiterThread() {
    try {
      const inbox = await api.recruiterInbox({ job_id: id })
      if (inbox.items.length === 1) {
        navigate(`/recruiter/${inbox.items[0].id}`)
        return
      }
      if (inbox.items.length > 1) {
        // Several threads exist: let the user pick in the inbox.
        navigate(`/recruiter?job=${id}`)
        return
      }
      navigate(`/recruiter?job=${id}`)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '打开失败' })
    }
  }

  async function copyGreeting(text: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setFeedback({ tone: 'warn', text: '浏览器拒绝了剪贴板访问，请手动选中文本复制。' })
    }
  }

  async function remove() {
    if (!window.confirm('确定删除这个岗位吗？该操作不可撤销。')) return
    try {
      await api.deleteJob(id)
      navigate('/jobs')
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '删除失败' })
    }
  }

  if (loading && !job) return <Loading text="正在加载岗位…" />
  if (!job) return <Alert tone="error">岗位不存在或已被删除。<Link to="/jobs">返回岗位库</Link></Alert>

  const result = analysis?.result
  const pre = analysis?.pre_analysis
  const isOpenStatus = job.status === 'new' || job.status === 'reviewed' || job.status === 'saved'


  /**
   * The process on the job's *current* application cycle. An older cycle's
   * process is history and is shown read-only below.
   */
  const activeProcess = processes.find((p) => p.status === 'ongoing') ?? processes.at(-1) ?? null

  async function runInterview(action: () => Promise<{ message: string }>) {
    setInterviewBusy(true)
    try {
      const result = await action()
      setFeedback({ tone: 'success', text: result.message })
      await Promise.all([loadInterviews(), load()])
      return true
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '操作失败' })
      return false
    } finally {
      setInterviewBusy(false)
    }
  }

  async function runOffer(action: () => Promise<{ message: string }>) {
    setOfferBusy(true)
    try {
      const result = await action()
      setFeedback({ tone: 'success', text: result.message })
      await Promise.all([loadOffers(), load()])
      return true
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '操作失败' })
      return false
    } finally {
      setOfferBusy(false)
    }
  }

  function toggleVariant(resumeId: number) {
    setSelectedVariants((prev) => {
      const next = new Set(prev)
      if (next.has(resumeId)) next.delete(resumeId)
      else next.add(resumeId)
      return next
    })
  }

  /**
   * Two-step on purpose. The first call sends confirmed=false: if any
   * combination is uncached the backend refuses with the exact count, which is
   * what the confirmation dialog shows. Nothing is spent until the user agrees.
   */
  async function compare(confirmed: boolean) {
    const ids = [...selectedVariants]
    if (!ids.length) return
    setComparing(true)
    try {
      const response = await api.compareResumes(id, ids, confirmed)
      setPendingCost(null)
      setFeedback({ tone: 'success', text: response.message })
      await loadResumeScores()
    } catch (err) {
      const pending =
        err instanceof ApiError ? Number(err.detail?.pending_analyses ?? 0) : 0
      if (pending > 0) {
        setPendingCost(pending)
      } else {
        setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '比较失败' })
      }
    } finally {
      setComparing(false)
    }
  }

  async function analyzeWith(resumeId: number) {
    setComparing(true)
    try {
      await api.analyzeWithResume(id, resumeId)
      setFeedback({ tone: 'success', text: '已按该简历分析完成' })
      await loadResumeScores()
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '分析失败' })
    } finally {
      setComparing(false)
    }
  }

  return (
    <>
      <header className="page-head">
        <div>
          <div className="row small faint mb-1">
            <Link to="/jobs">← 返回岗位库</Link>
            <span>·</span>
            <span>岗位 #{job.id}</span>
            <span>·</span>
            <span>来源：{job.source}</span>
          </div>
          <h1>{job.title}</h1>
          <p>
            {job.company}
            {job.city ? ` · ${job.city}` : ''}
            {job.salary_text ? ` · ${job.salary_text}` : ''}
          </p>
        </div>
        <div className="page-actions">
          <button type="button" disabled={analyzing} onClick={() => void runAnalysis('fast')}>
            {analyzing ? '分析中…' : 'AI分析'}
          </button>
          <button type="button" disabled={analyzing} onClick={() => void runAnalysis('smart')}>
            高质量重分析
          </button>
          <button type="button" disabled={analyzing} onClick={() => void runAnalysis('force')}>
            忽略缓存重跑
          </button>
          <button type="button" onClick={() => void openRecruiterThread()}>
            记录HR消息
          </button>
          <button type="button" className="btn-danger" onClick={() => void remove()}>
            删除
          </button>
        </div>
      </header>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      <div className="detail-grid">
        <div>
          <Card
            title="岗位描述"
            sub={showRawJd ? '原文' : '规范化后'}
            actions={
              <button type="button" className="btn-sm" onClick={() => setShowRawJd((v) => !v)}>
                {showRawJd ? '看规范化文本' : '看原文'}
              </button>
            }
          >
            <div className="jd-text">
              {showRawJd ? job.raw_description : job.normalized_description}
            </div>
          </Card>

          {result ? (
            <Card
              title="AI 匹配分析"
              sub={`${analysis?.meta.model} · ${formatDateTime(analysis!.meta.created_at)}${
                analysis?.meta.cached ? ' · 缓存结果' : ''
              }`}
            >
              <div className="row-between mb-1">
                <div className="row">
                  <ScoreBadge score={result.overall_score} large />
                  <div>
                    <VerdictBadge verdict={result.verdict} />
                    <div className="small faint mt-1">
                      确定性基线分 {pre?.heuristic_score ?? '—'} · 提示词版本 {analysis?.meta.prompt_version}
                    </div>
                  </div>
                </div>
              </div>

              <div className="subscores mt-2">
                <SubScore label="角色匹配" value={result.role_fit_score} />
                <SubScore label="技能匹配" value={result.skill_fit_score} />
                <SubScore label="经验匹配" value={result.experience_fit_score} />
                <SubScore label="地点匹配" value={result.location_fit_score} />
                <SubScore label="薪资匹配" value={result.salary_fit_score} />
              </div>

              {result.role_summary ? (
                <p className="mt-2">
                  <strong>岗位概述：</strong>
                  {result.role_summary}
                </p>
              ) : null}

              <div className="grid grid-2 mt-2">
                <div>
                  <h3 className="mb-1">匹配技能</h3>
                  <ChipList items={result.matched_skills} tone="good" />
                </div>
                <div>
                  <h3 className="mb-1">缺失技能</h3>
                  <ChipList items={result.missing_skills} tone="bad" />
                </div>
              </div>

              <div className="grid grid-2 mt-2">
                <div>
                  <h3 className="mb-1">优势</h3>
                  <BulletList items={result.strengths} />
                </div>
                <div>
                  <h3 className="mb-1">不足</h3>
                  <BulletList items={result.gaps} />
                </div>
              </div>

              <div className="mt-2">
                <h3 className="mb-1">风险提示</h3>
                <BulletList items={result.risk_flags} empty="未发现明显风险" />
              </div>

              {result.experience_gap ? (
                <p className="mt-2">
                  <strong>经验差距：</strong>
                  {result.experience_gap}
                </p>
              ) : null}

              {result.reasoning_summary ? (
                <p className="mt-1">
                  <strong>分析摘要：</strong>
                  {result.reasoning_summary}
                </p>
              ) : null}
            </Card>
          ) : (
            <Card title="AI 匹配分析">
              <p className="muted mt-0">
                这个岗位还没有分析过。点击上方「AI分析」，系统会结合当前简历与求职策略给出匹配分、
                优劣势和招呼语。结果会被缓存，重复点击不会重复扣费。
              </p>
            </Card>
          )}

          <Card
            title="Offer"
            sub="记录你收到的 Offer 与谈薪过程。JobAgent 不会替你谈判或答复"
            actions={
              !offers.length &&
              (job.status === 'applied' ||
                job.status === 'replied' ||
                job.status === 'interview' ||
                job.status === 'offer') ? (
                <button
                  type="button"
                  className="btn-primary btn-sm"
                  disabled={offerBusy}
                  onClick={() => setRecordingOffer(true)}
                >
                  记录 Offer
                </button>
              ) : null
            }
          >
            {!offers.length ? (
              <p className="muted mt-0">还没有记录 Offer。</p>
            ) : (
              offers.map((offer: OfferOut) => {
                const summary = offer.accepted_summary ?? offer.current_company_summary
                return (
                  <div key={offer.id} className="mt-1">
                    <div className="row-between">
                      <div className="cell-sub">
                        本次投递简历：{offer.resume_label ?? '未记录'}
                      </div>
                      <div>
                        <span className={OFFER_STATUS_TONE[offer.status]}>
                          {offer.status_label}
                        </span>
                        <DeadlineChip state={offer.deadline_state} />
                      </div>
                    </div>
                    <div className="meta-list">
                      <span>基础年薪 {money(summary?.base_annual ?? null, offer.currency)}</span>
                      <span>
                        首年保证现金{' '}
                        {money(summary?.first_year_guaranteed_cash ?? null, offer.currency)}
                      </span>
                    </div>
                    <div className="btn-row mt-1">
                      <Link className="btn btn-sm" to={`/offers/${offer.id}`}>
                        查看 Offer
                      </Link>
                    </div>
                  </div>
                )
              })
            )}
          </Card>

          <Card
            title="面试流程"
            sub="每一轮都由你自己记录。JobAgent 不读日历，也不会替你接受面试"
            actions={
              activeProcess && activeProcess.status === 'ongoing' ? (
                <button
                  type="button"
                  className="btn-primary btn-sm"
                  disabled={interviewBusy}
                  onClick={() => setAddingRound(true)}
                >
                  添加下一轮
                </button>
              ) : job.status === 'applied' ||
                job.status === 'replied' ||
                job.status === 'interview' ? (
                <button
                  type="button"
                  className="btn-primary btn-sm"
                  disabled={interviewBusy}
                  onClick={() => void runInterview(() => api.createInterviewProcess(id))}
                >
                  开始面试流程
                </button>
              ) : null
            }
          >
            {!processes.length ? (
              <p className="muted mt-0">
                还没有面试流程。收到面试邀请后，点「开始面试流程」记录第一轮。
              </p>
            ) : (
              processes.map((process: InterviewProcessOut) => (
                <div key={process.id} className="mt-1">
                  <div className="row-between">
                    <div className="cell-sub">
                      本次投递简历：{process.resume_label ?? '未记录'}
                      {process.resume_archived ? '（已归档）' : ''}
                    </div>
                    <span
                      className={process.status === 'ongoing' ? 'chip chip-good' : 'chip'}
                    >
                      {PROCESS_STATUS_LABEL[process.status]}
                    </span>
                  </div>
                  {process.rounds.map((round: InterviewRoundOut) => (
                    <RoundLine
                      key={round.id}
                      round={round}
                      busy={interviewBusy}
                      onComplete={
                        process.status === 'ongoing' ? setCompletingRound : undefined
                      }
                      onCancel={
                        process.status === 'ongoing'
                          ? (target) =>
                              void runInterview(() => api.cancelInterviewRound(target.id))
                          : undefined
                      }
                    />
                  ))}
                  {!process.rounds.length ? (
                    <p className="faint small">流程已创建，还没有添加轮次。</p>
                  ) : null}
                  <div className="btn-row mt-1">
                    <Link className="btn btn-sm" to={`/interviews/${process.id}`}>
                      查看完整流程
                    </Link>
                  </div>
                </div>
              ))
            )}
          </Card>

          <Card
            title="简历对比"
            sub="同一份 JD 在不同简历版本下的匹配分。这是 AI 判断的契合度，不是 HR 的反馈"
          >
            {resumeScores && resumeScores.cells.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>简历版本</th>
                      <th>匹配分</th>
                      <th>分析时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {resumeScores.cells.map((cell: ResumeAnalysisCell) => (
                      <tr key={cell.resume_id}>
                        <td>
                          <div className="cell-title">{cell.label}</div>
                          <div className="cell-sub">
                            {cell.resume_id === resumeScores.applied_resume_id
                              ? '本次实际投递简历'
                              : cell.resume_id === resumeScores.active_analysis_resume_id
                                ? '当前AI分析简历'
                                : ''}
                          </div>
                        </td>
                        <td className="nowrap">{cell.overall_score ?? '—'}</td>
                        <td className="nowrap">
                          {cell.created_at ? formatDateTime(cell.created_at) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted mt-0">还没有按简历版本分析过这个岗位。</p>
            )}

            {resumes.length > 1 ? (
              <>
                <div className="field mt-2">
                  <label>选择要比较的简历版本（2–3 份）</label>
                  <div className="stack">
                    {resumes.map((resume) => (
                      <label key={resume.id} className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={selectedVariants.has(resume.id)}
                          onChange={() => toggleVariant(resume.id)}
                        />
                        <span>
                          {resume.label}
                          {resumeScores?.cells.some(
                            (c: ResumeAnalysisCell) => c.resume_id === resume.id,
                          ) ? (
                            <span className="chip chip-good">已分析</span>
                          ) : (
                            <>
                              <span className="chip">未分析</span>
                              <button
                                type="button"
                                className="btn-ghost btn-sm"
                                disabled={comparing}
                                onClick={(event) => {
                                  event.preventDefault()
                                  void analyzeWith(resume.id)
                                }}
                              >
                                按此简历分析
                              </button>
                            </>
                          )}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
                <div className="btn-row">
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    disabled={comparing || selectedVariants.size === 0}
                    onClick={() => void compare(false)}
                  >
                    比较简历
                  </button>
                  {analysis?.meta.resume_id ? (
                    <span className="chip">
                      当前分析基于简历 #{analysis.meta.resume_id}
                    </span>
                  ) : null}
                </div>
                <p className="field-hint">
                  已分析过的组合直接读缓存，不会重复扣费；只有尚未分析的组合才会调用 API，
                  且需要你先确认。
                </p>
              </>
            ) : (
              <p className="field-hint mt-1">
                只有一份简历版本时无从比较。可以在「简历」页用「复制为新版本」建立不同侧重的版本。
              </p>
            )}
          </Card>

          {result?.greeting_message ? (
            <Card title="AI 招呼语" sub="人工确认后再自行复制发送">
              <div className="greeting">{result.greeting_message}</div>
              <div className="greeting-foot">
                <span className="small faint">
                  共 {result.greeting_message.length} 字 · 系统不会自动发送任何消息
                </span>
                <button
                  type="button"
                  className="btn-primary btn-sm"
                  onClick={() => void copyGreeting(result.greeting_message)}
                >
                  {copied ? '已复制 ✓' : '复制招呼语'}
                </button>
              </div>
            </Card>
          ) : null}
        </div>

        <div>
          <Card title="基本信息">
            <dl className="meta-list">
              <dt>公司</dt>
              <dd>{job.company}</dd>
              <dt>职位</dt>
              <dd>{job.title}</dd>
              <dt>城市</dt>
              <dd>{job.city ?? '未标注'}</dd>
              <dt>薪资</dt>
              <dd>{job.salary_text ?? '未标注'}</dd>
              <dt>经验</dt>
              <dd>{job.experience_text ?? '未标注'}</dd>
              <dt>学历</dt>
              <dd>{job.education_text ?? '未标注'}</dd>
              <dt>添加时间</dt>
              <dd>{formatDateTime(job.created_at)}</dd>
              {job.source_url ? (
                <>
                  <dt>原始链接</dt>
                  <dd>
                    <a href={job.source_url} target="_blank" rel="noreferrer noopener">
                      打开
                    </a>
                  </dd>
                </>
              ) : null}
            </dl>
          </Card>

          <Card title="求职进度" sub="AI 推荐，你来决定">
            <div className="row mb-1">
              当前状态：<StatusBadge status={job.status} />
            </div>

            {/* Actions depend on where the job actually is. Everything here
                records something the user did on the recruitment platform. */}
            <div className="btn-row mb-1">
              {isOpenStatus ? (
                <>
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    disabled={acting}
                    onClick={() => setConfirmApply(true)}
                  >
                    标记已投递
                  </button>
                  <button
                    type="button"
                    className="btn-sm"
                    disabled={acting}
                    onClick={() => void act(() => api.skipJob(id))}
                  >
                    跳过
                  </button>
                  <button
                    type="button"
                    className="btn-sm"
                    disabled={acting}
                    onClick={() => void act(() => api.deferJob(id, 'tomorrow'))}
                  >
                    稍后处理
                  </button>
                </>
              ) : null}

              {job.status === 'applied' ? (
                <button
                  type="button"
                  className="btn-sm"
                  disabled={acting}
                  onClick={() => void act(() => api.recordReply(id, 'positive'))}
                >
                  记录HR回复
                </button>
              ) : null}

              {job.status === 'applied' || job.status === 'replied' ? (
                <button
                  type="button"
                  className="btn-sm"
                  disabled={acting}
                  onClick={() => setShowInterview(true)}
                >
                  记录面试
                </button>
              ) : null}

              {job.status === 'interview' ? (
                <>
                  <button
                    type="button"
                    className="btn-sm"
                    disabled={acting}
                    onClick={() => void act(() => api.recordOffer(id))}
                  >
                    记录Offer
                  </button>
                  <button
                    type="button"
                    className="btn-sm"
                    disabled={acting}
                    onClick={() => setShowInterview(true)}
                  >
                    再记录一轮面试
                  </button>
                </>
              ) : null}

              {job.status !== 'new' && job.status !== 'rejected' && job.status !== 'offer' ? (
                <button
                  type="button"
                  className="btn-sm btn-danger"
                  disabled={acting}
                  onClick={() => void act(() => api.recordRejection(id))}
                >
                  记录拒绝
                </button>
              ) : null}

              {job.status !== 'new' && job.status !== 'reviewed' ? (
                <button
                  type="button"
                  className="btn-sm"
                  disabled={acting}
                  onClick={() => void act(() => api.resetJobStatus(id))}
                >
                  恢复待处理
                </button>
              ) : null}
            </div>

            <ApplicationTimeline events={timeline} />

            <div className="field-hint mt-1">
              这些记录只反映你本人在招聘平台上的操作，JobAgent 不会自动投递或发送消息。
            </div>
          </Card>

          {pre ? (
            <Card title="确定性特征" sub="LLM 之前的机械判断">
              <dl className="meta-list">
                <dt>城市</dt>
                <dd>{pre.city_reason}</dd>
                <dt>岗位方向</dt>
                <dd>{pre.matched_roles.length ? pre.matched_roles.join('、') : '未命中目标岗位名'}</dd>
                <dt>技能重合</dt>
                <dd>
                  {pre.skill_overlap_count} 项（{Math.round(pre.skill_overlap_ratio * 100)}%）
                </dd>
                <dt>经验要求</dt>
                <dd>{pre.experience_required}</dd>
                <dt>排除关键词</dt>
                <dd>{pre.excluded_hits.length ? pre.excluded_hits.join('、') : '无'}</dd>
                <dt>基线分</dt>
                <dd>{pre.heuristic_score}</dd>
              </dl>
            </Card>
          ) : null}

          <Card title="操作记录">
            {job.events.length === 0 ? (
              <p className="faint small mt-0">暂无记录。</p>
            ) : (
              <ul className="timeline">
                {job.events.map((event) => (
                  <li key={event.id}>
                    <time>{formatDateTime(event.created_at)}</time>
                    <span>{event.notes ?? event.event_type}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>

      {confirmApply ? (
        <Modal
          title="确认已投递？"
          onClose={() => setConfirmApply(false)}
          footer={
            <>
              <button type="button" onClick={() => setConfirmApply(false)}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  setConfirmApply(false)
                  void act(() => api.markApplied(id, undefined, appliedResume))
                }}
              >
                确认已投递
              </button>
            </>
          }
        >
          <p className="mt-0">
            <strong>{job.company}</strong>
            <br />
            {job.title}
          </p>
          <p className="muted">你已经在招聘平台完成了实际投递或沟通吗？</p>
          <ResumePicker
            resumes={resumes}
            value={appliedResume}
            onChange={setAppliedResume}
            analyzedResumeId={analysis?.meta.resume_id ?? null}
          />
          <div className="field-hint">
            这里只是记录，不会操作招聘网站。要让 JobAgent 代你点「立即沟通」，请到投递队列。
          </div>
        </Modal>
      ) : null}

      {recordingOffer ? (
        <RecordOfferDialog
          busy={offerBusy}
          onClose={() => setRecordingOffer(false)}
          onSubmit={(payload) => {
            void runOffer(() => api.createOffer(id, payload)).then((ok) => {
              if (ok) setRecordingOffer(false)
            })
          }}
        />
      ) : null}

      {addingRound && activeProcess ? (
        <AddRoundDialog
          busy={interviewBusy}
          onClose={() => setAddingRound(false)}
          onSubmit={(payload) => {
            void runInterview(() => api.addInterviewRound(activeProcess.id, payload)).then(
              (ok) => {
                if (ok) setAddingRound(false)
              },
            )
          }}
        />
      ) : null}

      {completingRound ? (
        <CompleteRoundDialog
          round={completingRound}
          busy={interviewBusy}
          onClose={() => setCompletingRound(null)}
          onSubmit={(payload) => {
            void runInterview(() =>
              api.completeInterviewRound(completingRound.id, payload),
            ).then((ok) => {
              if (ok) setCompletingRound(null)
            })
          }}
        />
      ) : null}

      {pendingCost !== null ? (
        <Modal
          title="这会产生 API 费用"
          onClose={() => setPendingCost(null)}
          footer={
            <>
              <button type="button" onClick={() => setPendingCost(null)}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={comparing}
                onClick={() => void compare(true)}
              >
                确认分析
              </button>
            </>
          }
        >
          <p className="mt-0">
            将分析 <strong>{pendingCost}</strong> 份尚未分析的简历版本，可能产生 API 费用。
          </p>
          <p className="muted">
            已经分析过的版本会直接使用缓存，不会重复计费。
          </p>
        </Modal>
      ) : null}

      {showInterview ? (
        <Modal title="记录面试" onClose={() => setShowInterview(false)}>
          <p className="muted mt-0">选择面试轮次：</p>
          <div className="btn-row">
            {INTERVIEW_ROUNDS.map((round) => (
              <button
                key={round}
                type="button"
                className="btn-sm"
                onClick={() => {
                  setShowInterview(false)
                  void act(() => api.recordInterview(id, round))
                }}
              >
                {round}
              </button>
            ))}
          </div>
        </Modal>
      ) : null}
    </>
  )
}
