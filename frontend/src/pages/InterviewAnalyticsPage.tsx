// 面试分析 (v0.8)
//
// Stage funnel, round conversion, drop-off and latency - all deterministic,
// all free. Two distinctions the page keeps visible:
//
//   * stages come from recorded rounds, not from Job.status;
//   * a withdrawal is never counted as an employer rejection.
//
// Carries no meeting URLs, interviewer names or feedback text: the backend
// never puts them in this payload.

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import { Alert, Card, EmptyState, Loading, Stat } from '@/components/ui'
import type {
  InterviewAnalyticsResult,
  InterviewCohortStat,
  LatencyStat,
  RateStat,
  SampleConfidence,
  TimeWindow,
} from '@/types'

const WINDOWS: { value: TimeWindow; label: string }[] = [
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

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value * 100)}%`
}

function hours(value: number | null): string {
  if (value === null) return '—'
  if (value < 48) return `${Math.round(value)} 小时`
  return `${(value / 24).toFixed(1)} 天`
}

/** A rate never appears without its fraction. */
function RateCell({ stat }: { stat: RateStat }) {
  if (stat.denominator === 0) {
    return (
      <div>
        <div className="cell-title">—</div>
        <div className="cell-sub">暂无样本</div>
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

function Latency({ label, stat }: { label: string; stat: LatencyStat }) {
  return (
    <div className="subscore">
      <div className="subscore-label">{label}</div>
      <div className="subscore-value">{hours(stat.median_hours)}</div>
      <div className="subscore-label">
        {stat.sample
          ? `样本 ${stat.sample} · P25 ${hours(stat.p25_hours)} / P75 ${hours(stat.p75_hours)}`
          : '暂无样本'}
      </div>
    </div>
  )
}

function CohortTable({
  rows,
  firstColumn,
  empty,
}: {
  rows: InterviewCohortStat[]
  firstColumn: string
  empty: string
}) {
  if (!rows.length) return <p className="faint small mt-0">{empty}</p>
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{firstColumn}</th>
            <th>投递</th>
            <th>进入面试</th>
            <th>进面率</th>
            <th>终面</th>
            <th>Offer</th>
            <th>置信度</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td>
                <div className="cell-title">{row.label}</div>
                {row.resume_archived ? <div className="cell-sub">已归档</div> : null}
              </td>
              <td>{row.applications}</td>
              <td>{row.reached_any_interview}</td>
              <td>
                <RateCell stat={row.interview_reach_rate} />
              </td>
              <td>{row.reached_final}</td>
              <td>{row.interview_offers}</td>
              <td>
                <span
                  className={
                    row.interview_reach_rate.confidence === 'insufficient'
                      ? 'chip'
                      : 'chip chip-good'
                  }
                >
                  {CONFIDENCE_LABEL[row.interview_reach_rate.confidence]}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function InterviewAnalyticsPage() {
  const [data, setData] = useState<InterviewAnalyticsResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [timeWindow, setTimeWindow] = useState<TimeWindow>('90d')
  const [city, setCity] = useState('')
  const [roleFamily, setRoleFamily] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await api.interviewAnalytics({ window: timeWindow, city, roleFamily }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '加载面试分析失败')
    } finally {
      setLoading(false)
    }
  }, [timeWindow, city, roleFamily])

  useEffect(() => {
    void load()
  }, [load])

  const funnel = data?.funnel

  return (
    <>
      <div className="page-head">
        <div>
          <h1>面试分析</h1>
          <p>
            阶段转化、轮次通过率、淘汰节点与耗时。全部为本地统计，
            <strong>不消耗任何 AI 额度</strong>。阶段来自你记录的面试轮次，而不是岗位状态。
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to="/interviews">
            返回面试
          </Link>
          <button type="button" className="btn-ghost" onClick={() => void load()}>
            刷新
          </button>
        </div>
      </div>

      {error ? <Alert tone="error">{error}</Alert> : null}

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
            <input
              id="city"
              value={city}
              placeholder="全部"
              onChange={(event) => setCity(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="role">方向</label>
            <input
              id="role"
              value={roleFamily}
              placeholder="全部，如 DevOps"
              onChange={(event) => setRoleFamily(event.target.value)}
            />
          </div>
        </div>
        <p className="field-hint mt-1">
          面试流程通常跨越数周，因此默认时间窗是 90 天。
        </p>
      </Card>

      {loading ? <Loading text="正在统计…" /> : null}

      {!loading && funnel && funnel.applications === 0 ? (
        <EmptyState
          icon="📉"
          title="当前时间窗内还没有投递记录"
          text="先记录投递与面试轮次，这里就会显示阶段转化。"
        />
      ) : null}

      {!loading && data && funnel && funnel.applications > 0 ? (
        <>
          {data.notes.length ? (
            <Alert tone="info">
              <ul className="bullet-list">
                {data.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </Alert>
          ) : null}

          <div className="grid grid-stats">
            <Stat label="投递" value={funnel.applications} />
            <Stat
              label="进入面试"
              value={funnel.reached_any_interview}
              hint={pct(funnel.application_to_interview.rate)}
            />
            <Stat label="技术面" value={funnel.reached_technical} />
            <Stat label="终面" value={funnel.reached_final} />
            <Stat label="Offer" value={funnel.offers} />
            <Stat
              label="主动终止"
              value={funnel.withdrawn}
              hint="不计入「被拒」"
            />
          </div>

          <Card title="阶段转化" sub="每一级都带自己的分母">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>阶段</th>
                    <th>到达</th>
                    <th>上一级人数</th>
                    <th>转化率</th>
                  </tr>
                </thead>
                <tbody>
                  {funnel.stages.map((stage) => (
                    <tr key={stage.key}>
                      <td className="cell-title">{stage.label}</td>
                      <td>{stage.reached}</td>
                      <td>{stage.eligible}</td>
                      <td>
                        <RateCell stat={stage.rate} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="各轮次通过率" sub="结果待定的轮次不计入分母 —— 还没出结果不等于没通过">
            {data.round_conversion.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>轮次</th>
                      <th>进入</th>
                      <th>通过</th>
                      <th>未通过</th>
                      <th>待定</th>
                      <th>通过率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.round_conversion.map((row) => (
                      <tr key={row.round_type}>
                        <td className="cell-title">{row.label}</td>
                        <td>{row.entered}</td>
                        <td>{row.passed}</td>
                        <td>{row.failed}</td>
                        <td>{row.pending}</td>
                        <td>
                          <RateCell stat={row.pass_rate} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="faint small mt-0">还没有记录任何面试轮次。</p>
            )}
          </Card>

          <Card
            title="流程在哪一步结束"
            sub="「被拒」与「主动终止」分开统计 —— 你自己的决定不是失败"
          >
            {data.drop_off.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>结束于</th>
                      <th>被拒</th>
                      <th>主动终止</th>
                      <th>占已结束流程</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.drop_off.map((row) => (
                      <tr key={row.round_type}>
                        <td className="cell-title">{row.label}</td>
                        <td>{row.rejected_after}</td>
                        <td>{row.withdrawn_after}</td>
                        <td>
                          <RateCell stat={row.share} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="faint small mt-0">还没有已结束的面试流程。</p>
            )}
          </Card>

          <Card title="耗时" sub="用中位数，避免一次特别慢的流程拉高「典型值」">
            <div className="grid grid-2">
              <Latency
                label="投递 → 首次面试"
                stat={data.latency.application_to_first_interview}
              />
              <Latency label="第一轮 → 第二轮" stat={data.latency.first_to_second_round} />
              <Latency label="最后一轮 → 结果" stat={data.latency.last_round_to_decision} />
              <Latency label="安排 → 完成" stat={data.latency.scheduled_to_completed} />
            </div>
          </Card>

          <Card
            title="按简历版本"
            sub="归属以投递当时记录的简历为准，切换当前分析简历不会改变历史"
          >
            <CohortTable
              rows={data.by_resume}
              firstColumn="简历版本"
              empty="还没有投递记录了所用简历"
            />
            {data.resume_attribution_coverage.total ? (
              <p className="field-hint mt-1">
                简历归属覆盖率 {pct(data.resume_attribution_coverage.ratio)}（
                {data.resume_attribution_coverage.covered}/
                {data.resume_attribution_coverage.total}）。
              </p>
            ) : null}
          </Card>

          <Card title="按城市">
            <CohortTable rows={data.by_city} firstColumn="城市" empty="暂无城市数据" />
          </Card>

          <Card title="按方向">
            <CohortTable rows={data.by_role_family} firstColumn="方向" empty="暂无方向数据" />
          </Card>

          <Card title="按来源">
            <CohortTable rows={data.by_source} firstColumn="来源" empty="暂无来源数据" />
          </Card>

          <Card
            title="反馈标签与原因"
            sub="只统计你自己选择的标签，不会去解读反馈原文"
          >
            <div className="grid grid-3">
              <div>
                <strong className="small">反馈标签</strong>
                {data.feedback_tags.length ? (
                  <div className="chip-list mt-1">
                    {data.feedback_tags.map((item) => (
                      <span key={item.key} className="chip">
                        {item.label} × {item.count}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="faint small">暂无</p>
                )}
              </div>
              <div>
                <strong className="small">未通过原因</strong>
                {data.failure_reasons.length ? (
                  <div className="chip-list mt-1">
                    {data.failure_reasons.map((item) => (
                      <span key={item.key} className="chip chip-bad">
                        {item.label} × {item.count}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="faint small">暂无</p>
                )}
              </div>
              <div>
                <strong className="small">主动终止原因</strong>
                {data.withdraw_reasons.length ? (
                  <div className="chip-list mt-1">
                    {data.withdraw_reasons.map((item) => (
                      <span key={item.key} className="chip">
                        {item.label} × {item.count}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="faint small">暂无</p>
                )}
              </div>
            </div>
          </Card>

          {data.observations.length ? (
            <Card title="观察到的结果" sub="模板生成，可复算；描述现象，不解释原因">
              <ul className="bullet-list">
                {data.observations.map((observation, index) => (
                  <li key={`${observation.dimension}-${observation.target}-${index}`}>
                    {observation.text}
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </>
      ) : null}
    </>
  )
}
