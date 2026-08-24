// 策略分析 (v0.6)
//
// The feedback loop: what kinds of jobs are actually working?
//
// Three rules this page exists to enforce visually:
//   1. never show a bare percentage - "100%" and "1/1" always travel together;
//   2. never let a lucky small cohort look like a conclusion;
//   3. never change the career strategy without an explicit human click.
//
// Everything here is deterministic backend data. No AI call is involved, so
// refreshing costs nothing.

import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError, api } from '@/api/client'
import { Alert, Card, EmptyState, Loading, Modal, Stat } from '@/components/ui'
import type {
  CareerAnalyticsResult,
  CohortStat,
  SampleConfidence,
  CoverageStat,
  LatencyStat,
  ObservationKind,
  ProposalType,
  RateStat,
  RecommendationsResponse,
  ScoreBandStat,
  StrategyAdjustmentProposal,
  StrategyDiff,
  TimeWindow,
} from '@/types'

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

const WINDOWS: { value: TimeWindow; label: string }[] = [
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
  { value: '90d', label: '近 90 天' },
  { value: 'all', label: '全部' },
]

const CONFIDENCE_LABEL: Record<SampleConfidence, string> = {
  insufficient: '样本不足',
  low: '参考',
  moderate: '中等',
  strong: '较可靠',
}

const CONFIDENCE_CLASS: Record<SampleConfidence, string> = {
  insufficient: 'chip',
  low: 'chip',
  moderate: 'chip chip-good',
  strong: 'chip chip-good',
}

const OBSERVATION_ICON: Record<ObservationKind, string> = {
  outperforming: '📈',
  underperforming: '📉',
  insufficient_data: '🧪',
  descriptive: '📌',
}

const PROPOSAL_LABEL: Record<ProposalType, string> = {
  increase_city_priority: '提高城市优先级',
  decrease_city_priority: '降低城市优先级',
  increase_role_priority: '增加方向比重',
  decrease_role_priority: '减少方向比重',
  prioritize_source: '值得投入的来源',
  deprioritize_source: '转化偏低的来源',
  consider_score_floor_change: '匹配分参考线',
  skill_learning_candidate: '技能学习候选',
  collect_more_data: '继续积累数据',
}

// ------------------------------------------------------------------ format

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value * 100)}%`
}

function hours(value: number | null): string {
  if (value === null) return '—'
  if (value < 48) return `${Math.round(value)} 小时`
  return `${(value / 24).toFixed(1)} 天`
}

/** A rate is never rendered alone: the fraction and the interval come with it. */
function RateCell({ stat }: { stat: RateStat }) {
  if (stat.denominator === 0) {
    return (
      <div>
        <div className="cell-title">—</div>
        <div className="cell-sub">暂无成熟样本</div>
      </div>
    )
  }
  return (
    <div>
      <div className="cell-title">
        {pct(stat.rate)}{' '}
        <span className="faint small">
          （{stat.numerator}/{stat.denominator}）
        </span>
      </div>
      <div className="cell-sub">
        95% 区间 {pct(stat.ci_low)}–{pct(stat.ci_high)}
      </div>
    </div>
  )
}

function ConfidenceChip({ value }: { value: SampleConfidence }) {
  return <span className={CONFIDENCE_CLASS[value]}>{CONFIDENCE_LABEL[value]}</span>
}

function Coverage({ label, stat }: { label: string; stat: CoverageStat }) {
  return (
    <div className="field">
      <strong className="small">{label}</strong>
      <div className="faint small">
        {stat.total === 0 ? '暂无数据' : `${pct(stat.ratio)}（${stat.covered}/${stat.total}）`}
      </div>
    </div>
  )
}

function Latency({ stat }: { stat: LatencyStat }) {
  if (!stat.sample) return <span className="faint">—</span>
  return (
    <span>
      {hours(stat.median_hours)}
      <span className="faint small">
        {' '}
        （P25 {hours(stat.p25_hours)} · P75 {hours(stat.p75_hours)}）
      </span>
    </span>
  )
}

// ------------------------------------------------------------------- table

function CohortTable({
  rows,
  firstColumn,
  empty,
  extraHeader,
  extraCell,
}: {
  rows: CohortStat[]
  firstColumn: string
  empty: string
  extraHeader?: string
  extraCell?: (row: CohortStat) => React.ReactNode
}) {
  if (!rows.length) return <p className="faint small mt-0">{empty}</p>
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{firstColumn}</th>
            {extraHeader ? <th>{extraHeader}</th> : null}
            <th>投递</th>
            <th>成熟样本</th>
            <th>回复</th>
            <th>成熟回复率</th>
            <th>面试</th>
            <th>面试率</th>
            <th>置信度</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td>
                <div className="cell-title">{row.label}</div>
              </td>
              {extraCell ? <td>{extraCell(row)}</td> : null}
              <td>{row.applications}</td>
              <td>{row.mature_applications}</td>
              <td>{row.replies}</td>
              <td>
                <RateCell stat={row.mature_reply_rate} />
              </td>
              <td>{row.interviews}</td>
              <td>
                <RateCell stat={row.interview_rate} />
              </td>
              <td>
                <ConfidenceChip value={row.mature_reply_rate.confidence} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// -------------------------------------------------------------------- page

export default function CareerAnalyticsPage() {
  const [data, setData] = useState<CareerAnalyticsResult | null>(null)
  // Facet options come from an unfiltered load, so narrowing the filters never
  // makes the other options disappear.
  const [facets, setFacets] = useState<CareerAnalyticsResult | null>(null)
  const [recommendations, setRecommendations] = useState<RecommendationsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [feedback, setFeedback] = useState<Feedback>(null)

  const [timeWindow, setTimeWindow] = useState<TimeWindow>('30d')
  const [city, setCity] = useState('')
  const [roleFamily, setRoleFamily] = useState('')
  const [source, setSource] = useState('')

  const [preview, setPreview] = useState<{
    proposal: StrategyAdjustmentProposal
    diff: StrategyDiff[]
  } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [result, proposals] = await Promise.all([
        api.careerAnalytics({ window: timeWindow, city, roleFamily, source }),
        api.strategyRecommendations(timeWindow),
      ])
      setData(result)
      setRecommendations(proposals)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载分析失败' })
    } finally {
      setLoading(false)
    }
  }, [timeWindow, city, roleFamily, source])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    api
      .careerAnalytics({ window: 'all' })
      .then(setFacets)
      .catch(() => setFacets(null))
  }, [])

  const cityOptions = useMemo(() => (facets?.by_city ?? []).map((c) => c.key), [facets])
  const roleOptions = useMemo(() => (facets?.by_role_family ?? []).map((c) => c.key), [facets])
  const sourceOptions = useMemo(
    () => (facets?.by_source ?? []).map((c) => ({ key: c.key, label: c.label })),
    [facets],
  )

  async function openPreview(proposal: StrategyAdjustmentProposal) {
    setBusy(proposal.signature)
    try {
      const result = await api.previewProposal(proposal.signature, timeWindow)
      setPreview({ proposal, diff: result.diff })
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '无法预览该建议' })
    } finally {
      setBusy(null)
    }
  }

  async function confirmApply() {
    if (!preview) return
    setBusy(preview.proposal.signature)
    try {
      const result = await api.applyProposal(preview.proposal.signature, undefined, timeWindow)
      setFeedback({ tone: 'success', text: result.message })
      setPreview(null)
      await load()
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '应用失败' })
    } finally {
      setBusy(null)
    }
  }

  async function dismiss(proposal: StrategyAdjustmentProposal) {
    setBusy(proposal.signature)
    try {
      const result = await api.dismissProposal(proposal.signature)
      setFeedback({ tone: 'info', text: result.message })
      await load()
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '操作失败' })
    } finally {
      setBusy(null)
    }
  }

  const summary = data?.summary
  const proposals = recommendations?.proposals ?? []

  return (
    <>
      <div className="page-head">
        <div>
          <h1>策略分析</h1>
          <p>
            用你自己的投递结果回答「哪类岗位真的有效」。全部为本地统计，
            <strong>不消耗任何 AI 额度</strong>；所有结论都会附上样本量。
          </p>
        </div>
        <div className="page-actions">
          <button type="button" className="btn-ghost" onClick={() => void load()}>
            刷新
          </button>
        </div>
      </div>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      <Card title="筛选">
        <div className="filters">
          <div className="field">
            <label htmlFor="window">时间窗</label>
            <select
              id="window"
              value={timeWindow}
              onChange={(event) => setTimeWindow(event.target.value as TimeWindow)}
            >
              {WINDOWS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="city">城市</label>
            <select id="city" value={city} onChange={(event) => setCity(event.target.value)}>
              <option value="">全部</option>
              {cityOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="role">方向</label>
            <select
              id="role"
              value={roleFamily}
              onChange={(event) => setRoleFamily(event.target.value)}
            >
              <option value="">全部</option>
              {roleOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="source">来源</label>
            <select id="source" value={source} onChange={(event) => setSource(event.target.value)}>
              <option value="">全部</option>
              {sourceOptions.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        {data ? (
          <p className="field-hint mt-1">
            回复成熟期 {data.response_maturity_days} 天 · 面试成熟期{' '}
            {data.interview_maturity_days} 天 · 报告时区 {data.timezone}。
            刚投递的岗位不算「没回复」，只是还没到时间。
          </p>
        ) : null}
      </Card>

      {loading ? <Loading text="正在统计…" /> : null}

      {!loading && summary && summary.applications === 0 ? (
        <EmptyState
          icon="📊"
          title="当前时间窗内还没有投递记录"
          text="先在投递队列里处理几个岗位，并在真正投递后点击「已投递」，这里就会开始有数据。"
        />
      ) : null}

      {!loading && summary && summary.applications > 0 ? (
        <>
          <div className="grid grid-stats">
            <Stat label="投递" value={summary.applications} hint="窗口内的真实投递" />
            <Stat
              label="成熟样本"
              value={summary.mature_applications}
              hint={`已回复或超过 ${data?.response_maturity_days ?? 7} 天`}
            />
            <Stat
              label="成熟回复率"
              value={pct(summary.mature_reply_rate.rate)}
              hint={`${summary.mature_reply_rate.numerator}/${summary.mature_reply_rate.denominator}`}
            />
            <Stat
              label="面试率"
              value={pct(summary.interview_rate.rate)}
              hint={`${summary.interview_rate.numerator}/${summary.interview_rate.denominator}`}
            />
            <Stat label="Offer" value={summary.offers} hint={`拒绝 ${summary.rejections}`} />
            <Stat
              label="回复中位时长"
              value={hours(summary.reply_latency.median_hours)}
              hint={`样本 ${summary.reply_latency.sample}`}
            />
          </div>

          {data && data.observations.length ? (
            <Card title="观察到的结果" sub="模板生成，可复算；只描述现象，不解释原因">
              <ul className="bullet-list">
                {data.observations.map((observation, index) => (
                  <li key={`${observation.dimension}-${observation.target}-${index}`}>
                    <span aria-hidden>{OBSERVATION_ICON[observation.kind]}</span>{' '}
                    {observation.text}
                  </li>
                ))}
              </ul>
              <p className="field-hint">
                这些是相关性，不是因果。样本量小的时候，差异很可能只是运气。
              </p>
            </Card>
          ) : null}

          <Card
            title="策略建议"
            sub="AI 不参与；系统只提出建议，任何修改都需要你确认"
            actions={
              recommendations && recommendations.suppressed > 0 ? (
                <span className="chip">已忽略 {recommendations.suppressed} 条</span>
              ) : null
            }
          >
            {!proposals.length ? (
              <p className="faint small mt-0">{recommendations?.message}</p>
            ) : (
              <>
                <p className="faint small mt-0">{recommendations?.message}</p>
                <div className="grid grid-2">
                  {proposals.map((proposal) => (
                    <section key={proposal.signature} className="job-card">
                      <div className="job-card-head">
                        <div>
                          <div className="cell-title">{PROPOSAL_LABEL[proposal.type]}</div>
                          <div className="cell-sub">{proposal.target}</div>
                        </div>
                        <ConfidenceChip value={proposal.confidence} />
                      </div>
                      <p className="job-card-summary">{proposal.reason}</p>
                      <p className="faint small">{proposal.impact_description}</p>
                      <div className="meta-list">
                        <span>
                          样本 {proposal.evidence.numerator}/{proposal.evidence.denominator}
                        </span>
                        <span>整体 {pct(proposal.evidence.comparison_rate)}</span>
                        {proposal.decision ? (
                          <span className="chip">
                            {proposal.decision === 'accepted' ? '已应用' : '已忽略'}
                          </span>
                        ) : null}
                      </div>
                      <div className="btn-row">
                        {proposal.applicable ? (
                          <button
                            type="button"
                            className="btn-primary btn-sm"
                            disabled={busy === proposal.signature}
                            onClick={() => void openPreview(proposal)}
                          >
                            查看修改内容
                          </button>
                        ) : (
                          <span className="chip">参考建议，无需修改配置</span>
                        )}
                        <button
                          type="button"
                          className="btn-ghost btn-sm"
                          disabled={busy === proposal.signature}
                          onClick={() => void dismiss(proposal)}
                        >
                          暂不调整
                        </button>
                      </div>
                    </section>
                  ))}
                </div>
              </>
            )}
          </Card>

          {data ? (
            <>
              <Card title="按城市" sub="按 95% 置信下界排序：小样本不会因为运气好而排到前面">
                <CohortTable rows={data.by_city} firstColumn="城市" empty="暂无城市数据" />
              </Card>

              <Card title="按方向">
                <CohortTable rows={data.by_role_family} firstColumn="方向" empty="暂无方向数据" />
              </Card>

              <Card title="按城市 × 方向" sub="至少 2 个样本才会成行">
                <CohortTable
                  rows={data.by_city_role}
                  firstColumn="城市 · 方向"
                  empty="还没有任何组合达到 2 个样本"
                />
              </Card>

              <Card title="按来源" sub="来源只说明机会从哪里来，JobAgent 不会去操作任何平台">
                <CohortTable rows={data.by_source} firstColumn="来源" empty="暂无来源数据" />
              </Card>

              <Card title="按匹配分" sub="回答「匹配分能不能预测结果」">
                <CohortTable
                  rows={data.by_score_band}
                  firstColumn="分数段"
                  empty="暂无已分析岗位"
                  extraHeader="已分析岗位"
                  extraCell={(row) => (row as ScoreBandStat).analyzed_jobs}
                />
              </Card>

              <Card title="按 AI 建议" sub="AI 的建议只是推荐；这里统计的是你实际投递后的结果">
                <CohortTable rows={data.by_verdict} firstColumn="AI 建议" empty="暂无分析记录" />
              </Card>

              <Card title="按薪资区间" sub="只统计能解析出数字的岗位，面议不会被猜成数字">
                <CohortTable
                  rows={data.by_salary_band}
                  firstColumn="薪资区间"
                  empty="已投岗位中没有可解析的薪资"
                />
              </Card>

              <Card title="回复速度" sub="用中位数，避免一次三周后的回复拉高「典型值」">
                {data.latency_by_source.length ? (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>来源</th>
                          <th>回复样本</th>
                          <th>回复中位时长</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.latency_by_source.map((row) => (
                          <tr key={row.key}>
                            <td>{row.label}</td>
                            <td>{row.reply_latency.sample}</td>
                            <td>
                              <Latency stat={row.reply_latency} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="faint small mt-0">暂无回复记录</p>
                )}
              </Card>

              <Card
                title="HR 常问什么"
                sub="来自你手动录入并分析过的消息；JobAgent 不读取任何收件箱"
              >
                {data.recruiter.requests.length ? (
                  <>
                    <div className="chip-list">
                      {data.recruiter.requests.map((item) => (
                        <span key={item.key} className="chip">
                          {item.label} × {item.count}
                        </span>
                      ))}
                    </div>
                    <p className="field-hint">
                      已分析 {data.recruiter.analysis_coverage.covered}/
                      {data.recruiter.analysis_coverage.total} 条 HR 消息。
                      出现最多的问题值得提前准备好答案。
                    </p>
                  </>
                ) : (
                  <p className="faint small mt-0">
                    还没有分析过的 HR 消息。在「HR沟通」里录入并分析后，这里会出现统计。
                  </p>
                )}
              </Card>

              <Card
                title="技能与结果"
                sub="只是出现频率的统计，不代表补上某项技能就会带来面试"
              >
                <div className="grid grid-2">
                  <div>
                    <strong className="small">进入面试的岗位中出现的技能</strong>
                    {data.skills.matched_in_interviews.length ? (
                      <div className="chip-list mt-1">
                        {data.skills.matched_in_interviews.map((item) => (
                          <span key={item.skill} className="chip chip-good">
                            {item.skill} {item.interviewed}/{item.applied}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="faint small">暂无面试记录</p>
                    )}
                  </div>
                  <div>
                    <strong className="small">高分岗位中反复缺失的技能</strong>
                    {data.skills.missing_in_high_score_jobs.length ? (
                      <div className="chip-list mt-1">
                        {data.skills.missing_in_high_score_jobs.map((item) => (
                          <span key={item.key} className="chip chip-bad">
                            {item.label} × {item.count}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="faint small">暂无数据</p>
                    )}
                  </div>
                </div>
              </Card>

              <Card title="数据质量" sub="先知道能信多少，再看结论">
                <div className="grid grid-3">
                  <Coverage label="城市信息" stat={data.data_quality.city_coverage} />
                  <Coverage label="方向识别" stat={data.data_quality.role_family_coverage} />
                  <Coverage label="薪资可解析" stat={data.data_quality.salary_coverage} />
                  <Coverage label="关联 HR 沟通" stat={data.data_quality.conversation_coverage} />
                  <Coverage
                    label="HR 消息已分析"
                    stat={data.data_quality.recruiter_analysis_coverage}
                  />
                </div>
                {data.data_quality.notes.length ? (
                  <Alert tone="warn">
                    <ul className="bullet-list">
                      {data.data_quality.notes.map((note) => (
                        <li key={note}>{note}</li>
                      ))}
                    </ul>
                  </Alert>
                ) : null}
              </Card>
            </>
          ) : null}
        </>
      ) : null}

      {preview ? (
        <Modal
          title="确认修改求职策略"
          onClose={() => setPreview(null)}
          footer={
            <>
              <button type="button" className="btn-ghost" onClick={() => setPreview(null)}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={busy === preview.proposal.signature}
                onClick={() => void confirmApply()}
              >
                应用修改
              </button>
            </>
          }
        >
          <p>{preview.proposal.reason}</p>
          {preview.diff.map((diff) => (
            <div key={diff.field} className="field">
              <strong className="small">{diff.description}</strong>
              <div className="mono small mt-1">
                修改前：{JSON.stringify(diff.before)}
              </div>
              <div className="mono small">修改后：{JSON.stringify(diff.after)}</div>
            </div>
          ))}
          <Alert tone="info">
            这只会调整 <code className="mono">career_strategy.yaml</code> 里的优先级顺序，
            并记录一条变更审计。不会改变任何岗位的状态，也不会自动投递。
          </Alert>
        </Modal>
      ) : null}
    </>
  )
}
