import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import {
  Alert,
  Card,
  EmptyState,
  Loading,
  ScoreBadge,
  Stat,
  VerdictBadge,
  formatDateTime,
} from '@/components/ui'
import type {
  DashboardAnalytics,
  DashboardSummary,
  InboxSummary,
  ResumeAnalyticsResult,
  OfferBoardResponse,
  UpcomingInterviewsResponse,
} from '@/types'

export default function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [recruiter, setRecruiter] = useState<InboxSummary | null>(null)
  const [analytics, setAnalytics] = useState<DashboardAnalytics | null>(null)
  const [resumeStats, setResumeStats] = useState<ResumeAnalyticsResult | null>(null)
  const [interviews, setInterviews] = useState<UpcomingInterviewsResponse | null>(
    null,
  )
  const [offers, setOffers] = useState<OfferBoardResponse | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setSummary(await api.dashboard())
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '加载仪表盘失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    api
      .recruiterInbox()
      .then((r) => setRecruiter(r.summary))
      .catch(() => setRecruiter(null))
  }, [])

  // Deterministic and free - no AI call sits behind this.
  useEffect(() => {
    api
      .dashboardAnalytics()
      .then(setAnalytics)
      .catch(() => setAnalytics(null))
  }, [])

  useEffect(() => {
    api
      .resumeAnalytics({ window: 'all' })
      .then(setResumeStats)
      .catch(() => setResumeStats(null))
  }, [])

  // A local view of interviews the user recorded - not a calendar feed.
  useEffect(() => {
    api
      .upcomingInterviews()
      .then(setInterviews)
      .catch(() => setInterviews(null))
  }, [])

  useEffect(() => {
    api
      .offerBoard()
      .then(setOffers)
      .catch(() => setOffers(null))
  }, [])

  if (loading && !summary) return <Loading text="正在加载仪表盘…" />
  if (error) return <Alert tone="error">{error}</Alert>
  if (!summary) return null

  const maxBucket = Math.max(1, ...summary.score_buckets.map((b) => b.count))
  const cities = Object.entries(summary.by_city).slice(0, 6)

  return (
    <>
      <header className="page-head">
        <div>
          <h1>仪表盘</h1>
          <p>本地岗位库与 AI 匹配结果概览</p>
        </div>
        <div className="page-actions">
          <button type="button" onClick={() => void load()} disabled={loading}>
            刷新
          </button>
          <Link className="btn btn-primary" to="/queue">
            前往投递队列
          </Link>
        </div>
      </header>

      {summary.total_jobs === 0 ? (
        <Card>
          <EmptyState
            icon="💼"
            title="岗位库还是空的"
            text="可以先运行演示数据种子命令，或直接在岗位库里粘贴一份 JD。"
            action={
              <Link className="btn btn-primary" to="/jobs">
                添加岗位
              </Link>
            }
          />
        </Card>
      ) : null}

      <div className="grid grid-stats">
        <Stat label="岗位总数" value={summary.total_jobs} />
        <Stat
          label="已分析"
          value={summary.analyzed_jobs}
          hint={summary.unanalyzed_jobs > 0 ? `${summary.unanalyzed_jobs} 个待分析` : '全部已分析'}
        />
        <Stat
          label="推荐投递"
          value={summary.recommended}
          hint={`强烈推荐 ${summary.strong_apply} · 推荐 ${summary.apply}`}
        />
        <Stat label="可以考虑" value={summary.maybe} />
        <Stat label="不建议" value={summary.skip} />
        <Stat
          label="平均匹配分"
          value={summary.average_score === null ? '—' : summary.average_score.toFixed(1)}
          hint={summary.analyzed_jobs > 0 ? `基于 ${summary.analyzed_jobs} 个已分析岗位` : '尚无分析结果'}
        />
      </div>

      <div className="grid grid-2 mt-2">
        <Card title="分数分布" sub="按最新一次分析统计">
          {summary.analyzed_jobs === 0 ? (
            <p className="faint small mt-0">还没有分析结果。</p>
          ) : (
            <div className="stack">
              {summary.score_buckets.map((bucket) => (
                <div key={bucket.label} className="row">
                  <span className="small mono" style={{ width: 58 }}>
                    {bucket.label}
                  </span>
                  <div className="bar" style={{ flex: 1, height: 8 }}>
                    <span style={{ width: `${(bucket.count / maxBucket) * 100}%` }} />
                  </div>
                  <span className="small muted" style={{ width: 28, textAlign: 'right' }}>
                    {bucket.count}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="城市分布">
          {cities.length === 0 ? (
            <p className="faint small mt-0">暂无数据。</p>
          ) : (
            <div className="chip-list">
              {cities.map(([city, count]) => (
                <span key={city} className="chip">
                  {city} · {count}
                </span>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card title="HR待处理" sub="需要你回复或跟进的招聘方消息">
        {recruiter === null ? (
          <p className="faint small mt-0">暂无招聘方消息记录。</p>
        ) : (
          <>
            <div className="row">
              <span className="chip">{recruiter.needs_reply} 条待回复</span>
              <span className="chip">{recruiter.follow_up_due} 条待跟进</span>
              <span className="chip">今天收到 {recruiter.received_today}</span>
            </div>
            <div className="btn-row mt-2">
              <Link className="btn btn-primary btn-sm" to="/recruiter">
                打开HR沟通
              </Link>
            </div>
          </>
        )}
      </Card>

      <Card title="求职漏斗" sub="只统计你本人记录的真实动作">
        {summary.funnel && summary.funnel.applied_jobs !== undefined ? (
          <>
            <div className="grid grid-3">
              {[
                { label: '岗位', value: summary.funnel.total_jobs },
                { label: 'AI推荐', value: summary.funnel.recommended_jobs },
                { label: '已投递', value: summary.funnel.applied_jobs },
                { label: 'HR回复', value: summary.funnel.replied_jobs },
                { label: '面试', value: summary.funnel.interview_jobs },
                { label: 'Offer', value: summary.funnel.offer_jobs },
              ].map((step) => (
                <div key={step.label} className="subscore">
                  <div className="subscore-label">{step.label}</div>
                  <div className="subscore-value">{step.value ?? 0}</div>
                </div>
              ))}
            </div>

            <div className="row mt-2">
              {[
                { label: '投递 → HR回复', value: summary.rates?.application_response_rate },
                { label: 'HR回复 → 面试', value: summary.rates?.response_interview_rate },
                { label: '投递 → 面试', value: summary.rates?.application_interview_rate },
              ].map((rate) => (
                <span key={rate.label} className="chip">
                  {rate.label}：
                  {rate.value === null || rate.value === undefined
                    ? '—'
                    : `${Math.round(rate.value * 100)}%`}
                </span>
              ))}
            </div>
            <div className="field-hint mt-1">
              样本还很少时比例仅供参考。今日已投 {summary.applied_today ?? 0} / {summary.daily_target ?? 10}。
            </div>
          </>
        ) : (
          <p className="faint small mt-0">还没有投递记录。</p>
        )}
      </Card>

      <Card
        title="求职策略表现"
        sub="近 30 天；样本不足时这里会保持沉默，而不是给一个漂亮的数字"
        actions={
          <Link className="btn btn-sm" to="/analytics">
            查看策略分析
          </Link>
        }
      >
        {analytics === null ? (
          <p className="faint small mt-0">加载中…</p>
        ) : analytics.has_signal && analytics.mature_reply_rate ? (
          <>
            <div className="grid grid-3">
              <div className="subscore">
                <div className="subscore-label">目前转化最好的方向</div>
                <div className="subscore-value">{analytics.best_direction}</div>
              </div>
              <div className="subscore">
                <div className="subscore-label">成熟回复率</div>
                <div className="subscore-value">
                  {Math.round((analytics.mature_reply_rate.rate ?? 0) * 100)}%
                </div>
                <div className="subscore-label">
                  {analytics.mature_reply_rate.numerator}/
                  {analytics.mature_reply_rate.denominator}
                </div>
              </div>
              <div className="subscore">
                <div className="subscore-label">面试率</div>
                <div className="subscore-value">
                  {analytics.interview_rate && analytics.interview_rate.denominator
                    ? `${Math.round((analytics.interview_rate.rate ?? 0) * 100)}%`
                    : '—'}
                </div>
                <div className="subscore-label">
                  {analytics.interview_rate
                    ? `${analytics.interview_rate.numerator}/${analytics.interview_rate.denominator}`
                    : '暂无成熟样本'}
                </div>
              </div>
            </div>
            <div className="field-hint mt-1">
              {analytics.message}。这是相关性，不是因果；调整策略前请到策略分析页看完整证据。
            </div>
          </>
        ) : (
          <p className="faint small mt-0">{analytics.message}</p>
        )}
      </Card>

      {offers ? <OfferSignal board={offers} /> : null}

      <Card
        title="近期面试"
        sub="只显示你自己记录的面试安排"
        actions={
          <Link className="btn btn-sm" to="/interviews">
            打开面试
          </Link>
        }
      >
        {interviews && interviews.items.length ? (
          <>
            <div className="stack">
              {interviews.items.map((item) => (
                <div key={item.round_id} className="row-between">
                  <div>
                    <div className="cell-title">
                      {item.day_key} · {formatDateTime(item.scheduled_at)}
                    </div>
                    <div className="cell-sub">
                      {item.company} · {item.round_label}
                    </div>
                  </div>
                  <Link className="btn btn-sm" to={`/interviews/${item.process_id}`}>
                    查看
                  </Link>
                </div>
              ))}
            </div>
            {interviews.total > interviews.items.length ? (
              <p className="field-hint mt-1">
                共 {interviews.total} 场，这里显示最近的 {interviews.items.length} 场。
              </p>
            ) : null}
          </>
        ) : (
          <p className="faint small mt-0">
            {interviews?.message ?? '加载中…'}
          </p>
        )}
      </Card>

      {resumeStats ? <ResumeSignal stats={resumeStats} /> : null}

      <Card title="推荐岗位 Top" sub="按 AI 总匹配分排序">
        {summary.top_jobs.length === 0 ? (
          <p className="faint small mt-0">还没有已分析的岗位，先去岗位库点一下「AI分析」。</p>
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
                  <th />
                </tr>
              </thead>
              <tbody>
                {summary.top_jobs.map((job) => (
                  <tr key={job.job_id}>
                    <td className="nowrap">
                      <ScoreBadge score={job.overall_score} />
                    </td>
                    <td>
                      <div className="cell-title">{job.company}</div>
                      <div className="cell-sub">{job.title}</div>
                    </td>
                    <td className="nowrap">{job.city ?? '—'}</td>
                    <td className="nowrap">{job.salary_text ?? '—'}</td>
                    <td className="nowrap">
                      <VerdictBadge verdict={job.verdict} />
                    </td>
                    <td className="nowrap">
                      <Link className="btn btn-sm" to={`/jobs/${job.job_id}`}>
                        查看
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {summary.active_resume_id === null ? (
        <Alert tone="warn">
          还没有设置当前简历。请先到 <Link to="/resume">简历</Link> 页面上传 PDF 或 DOCX，AI 分析需要它。
        </Alert>
      ) : null}
    </>
  )
}


/**
 * 简历表现 signal.
 *
 * Deliberately silent unless the evidence supports naming a leader: it needs
 * good attribution coverage *and* two adequately-sampled variants. Announcing
 * a "winner" off three applications would be worse than saying nothing.
 */
function ResumeSignal({ stats }: { stats: ResumeAnalyticsResult }) {
  const coverage = stats.attribution_coverage.ratio
  const usable = stats.by_resume.filter(
    (r) => r.mature_reply_rate.confidence !== 'insufficient',
  )
  const hasSignal = coverage !== null && coverage >= 0.6 && usable.length >= 2
  const best = usable[0]

  return (
    <Card
      title="简历表现"
      sub="按投递当时记录的简历归属；换掉当前分析简历不会改变历史"
      actions={
        <Link className="btn btn-sm" to="/resume-analytics">
          查看简历分析
        </Link>
      }
    >
      {hasSignal && best ? (
        <>
          <div className="grid grid-3">
            <div className="subscore">
              <div className="subscore-label">当前成熟回复率最高</div>
              <div className="subscore-value">{best.label}</div>
            </div>
            <div className="subscore">
              <div className="subscore-label">成熟回复率</div>
              <div className="subscore-value">
                {Math.round((best.mature_reply_rate.rate ?? 0) * 100)}%
              </div>
              <div className="subscore-label">
                {best.mature_reply_rate.numerator} / {best.mature_reply_rate.denominator} HR回复
              </div>
            </div>
            <div className="subscore">
              <div className="subscore-label">面试</div>
              <div className="subscore-value">{best.interviews}</div>
            </div>
          </div>
          <div className="field-hint mt-1">
            这是观察性数据，不同简历常被用于不同岗位，不能当作严格的 A/B 实验结论。
          </div>
        </>
      ) : (
        <p className="faint small mt-0">
          简历效果数据积累中
          {coverage !== null && coverage < 0.6
            ? `：只有 ${Math.round(coverage * 100)}% 的投递记录了实际使用的简历。`
            : '，样本还不足以比较各个版本。'}
        </p>
      )}
    </Card>
  )
}


/**
 * Offer待决定.
 *
 * Shows what needs a decision and when the soonest deadline is. Deliberately
 * plain: a deadline is information, not an alarm, and JobAgent sends no
 * notifications.
 */
function OfferSignal({ board }: { board: OfferBoardResponse }) {
  const open = [...board.pending, ...board.negotiating]
  const withDeadline = open
    .filter((offer) => offer.decision_deadline)
    .sort((a, b) => (a.days_to_deadline ?? 0) - (b.days_to_deadline ?? 0))
  const soonest = withDeadline[0]

  return (
    <Card
      title="Offer"
      sub="只显示你自己记录的 Offer"
      actions={
        <Link className="btn btn-sm" to="/offers">
          打开 Offer
        </Link>
      }
    >
      {open.length ? (
        <>
          <div className="grid grid-2">
            <div className="subscore">
              <div className="subscore-label">待决定</div>
              <div className="subscore-value">{open.length}</div>
            </div>
            <div className="subscore">
              <div className="subscore-label">最近截止</div>
              <div className="subscore-value">
                {soonest?.decision_deadline
                  ? formatDateTime(soonest.decision_deadline)
                  : '无'}
              </div>
              {soonest ? (
                <div className="subscore-label">{soonest.company}</div>
              ) : null}
            </div>
          </div>
          <div className="field-hint mt-1">
            JobAgent 不会替你答复或接受 Offer，也不会发送任何提醒。
          </div>
        </>
      ) : (
        <p className="faint small mt-0">
          {board.accepted.length || board.closed.length
            ? '当前没有待决定的 Offer。'
            : '还没有记录 Offer。收到后可在岗位详情页记录。'}
        </p>
      )}
    </Card>
  )
}
