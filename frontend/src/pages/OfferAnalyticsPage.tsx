// Offer 分析 (v0.9)
//
// Deterministic and free. Compensation is grouped strictly within each
// currency - there is no exchange-rate model, so amounts are never converted
// or ranked across currencies.

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import { money } from '@/components/offer'
import { Alert, Card, EmptyState, Loading, Stat } from '@/components/ui'
import type {
  CurrencyCompensation,
  MoneyStat,
  OfferAnalyticsResult,
  OfferCohortStat,
  RateStat,
  SampleConfidence,
  TimeWindow,
} from '@/types'

const WINDOWS: { value: TimeWindow; label: string }[] = [
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

function MoneyRow({ label, stat }: { label: string; stat: MoneyStat | null }) {
  if (!stat) {
    return (
      <tr>
        <td className="cell-title">{label}</td>
        <td colSpan={4} className="faint">
          未填写
        </td>
      </tr>
    )
  }
  return (
    <tr>
      <td className="cell-title">{label}</td>
      <td>{stat.sample}</td>
      <td>{money(stat.median, stat.currency)}</td>
      <td>{money(stat.p25, stat.currency)}</td>
      <td>{money(stat.p75, stat.currency)}</td>
    </tr>
  )
}

function CurrencyCard({ entry, title }: { entry: CurrencyCompensation; title: string }) {
  return (
    <Card
      title={`${title} · ${entry.currency}`}
      sub={`${entry.offers} 个 Offer，仅在本币种内统计`}
    >
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>项目</th>
              <th>样本</th>
              <th>中位数</th>
              <th>P25</th>
              <th>P75</th>
            </tr>
          </thead>
          <tbody>
            <MoneyRow label="基础年薪" stat={entry.base_annual} />
            <MoneyRow label="首年保证现金" stat={entry.first_year_guaranteed} />
            <MoneyRow label="首年目标现金" stat={entry.first_year_target} />
            <MoneyRow label="估算总包" stat={entry.estimated_total_comp} />
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function CohortTable({
  rows,
  firstColumn,
  empty,
}: {
  rows: OfferCohortStat[]
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
            <th>Offer</th>
            <th>Offer 率</th>
            <th>接受</th>
            <th>拒绝</th>
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
              <td>{row.offers_recorded}</td>
              <td>
                <RateCell stat={row.offer_reach_rate} />
              </td>
              <td>{row.accepted}</td>
              <td>{row.declined}</td>
              <td>
                <span
                  className={
                    row.offer_reach_rate.confidence === 'insufficient'
                      ? 'chip'
                      : 'chip chip-good'
                  }
                >
                  {CONFIDENCE_LABEL[row.offer_reach_rate.confidence]}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function OfferAnalyticsPage() {
  const [data, setData] = useState<OfferAnalyticsResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [timeWindow, setTimeWindow] = useState<TimeWindow>('all')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await api.offerAnalytics({ window: timeWindow }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '加载 Offer 分析失败')
    } finally {
      setLoading(false)
    }
  }, [timeWindow])

  useEffect(() => {
    void load()
  }, [load])

  const funnel = data?.funnel

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Offer 分析</h1>
          <p>
            Offer 转化、薪酬中位数、谈薪变化与拒绝原因。全部为本地统计，
            <strong>不消耗任何 AI 额度</strong>。不同币种分开统计，系统不会使用任何汇率。
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to="/offers">
            返回 Offer
          </Link>
          <select
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
      </div>

      {error ? <Alert tone="error">{error}</Alert> : null}
      {loading ? <Loading text="正在统计…" /> : null}

      {!loading && funnel && funnel.applications === 0 ? (
        <EmptyState
          icon="📊"
          title="当前时间窗内还没有投递记录"
          text="记录投递与 Offer 后，这里会显示转化与薪酬分布。"
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
              label="Offer"
              value={funnel.offers}
              hint={pct(funnel.application_to_offer.rate)}
            />
            <Stat label="已接受" value={funnel.accepted} />
            <Stat label="已拒绝" value={funnel.declined} hint="我方主动拒绝" />
            <Stat label="待决定" value={funnel.pending} />
          </div>

          <Card title="转化率" sub="每个比例都带自己的分子分母">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>指标</th>
                    <th>比例</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="cell-title">投递 → Offer</td>
                    <td>
                      <RateCell stat={funnel.application_to_offer} />
                    </td>
                  </tr>
                  <tr>
                    <td className="cell-title">进入面试 → Offer</td>
                    <td>
                      <RateCell stat={funnel.interview_to_offer} />
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <div className="cell-title">Offer 接受率</div>
                      <div className="cell-sub">分母只含已做决定的 Offer</div>
                    </td>
                    <td>
                      <RateCell stat={funnel.offer_acceptance_rate} />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Card>

          {data.compensation.map((entry) => (
            <CurrencyCard key={entry.currency} entry={entry} title="薪酬分布" />
          ))}

          {data.accepted_compensation.map((entry) => (
            <CurrencyCard
              key={`accepted-${entry.currency}`}
              entry={entry}
              title="已接受 Offer 的薪酬"
            />
          ))}

          {data.negotiation.length ? (
            <Card
              title="谈薪变化"
              sub="只比较公司先后给出的条件；我方诉求不参与计算"
            >
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>币种</th>
                      <th>样本</th>
                      <th>基础年薪中位变化</th>
                      <th>幅度</th>
                      <th>完整谈薪过程</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.negotiation.map((entry) => (
                      <tr key={entry.currency}>
                        <td className="cell-title">{entry.currency}</td>
                        <td>{entry.sample}</td>
                        <td>{money(entry.median_base_uplift, entry.currency)}</td>
                        <td>{pct(entry.median_base_uplift_pct)}</td>
                        <td>{entry.with_full_sequence}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="field-hint mt-1">
                「完整谈薪过程」指记录里存在「我方诉求 → 公司调整」的先后顺序。
                没有这个顺序时，条件变化不能说明是谈判带来的。
              </p>
            </Card>
          ) : null}

          <Card title="按简历版本" sub="归属以投递当时记录的简历为准">
            <CohortTable rows={data.by_resume} firstColumn="简历版本" empty="暂无数据" />
            {data.resume_attribution_coverage.total ? (
              <p className="field-hint mt-1">
                简历归属覆盖率 {pct(data.resume_attribution_coverage.ratio)}（
                {data.resume_attribution_coverage.covered}/
                {data.resume_attribution_coverage.total}）。
              </p>
            ) : null}
          </Card>

          <Card title="按城市">
            <CohortTable rows={data.by_city} firstColumn="城市" empty="暂无数据" />
          </Card>

          <Card title="按方向">
            <CohortTable rows={data.by_role_family} firstColumn="方向" empty="暂无数据" />
          </Card>

          <Card title="按来源">
            <CohortTable rows={data.by_source} firstColumn="来源" empty="暂无数据" />
          </Card>

          {data.decline_reasons.length ? (
            <Card title="拒绝 Offer 的原因" sub="仅为描述性统计">
              <div className="chip-list">
                {data.decline_reasons.map((item) => (
                  <span key={item.key} className="chip">
                    {item.label} × {item.count}
                  </span>
                ))}
              </div>
            </Card>
          ) : null}

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
