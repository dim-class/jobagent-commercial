// Offer comparison (v0.9)
//
// Side by side, with no overall winner. Non-cash factors are shown as
// themselves rather than folded into a score, and offers in different
// currencies keep their native values - there is no exchange-rate model.

import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import { DeadlineChip, EQUITY_TYPES, REMOTE_POLICIES, money } from '@/components/offer'
import { Alert, Card, EmptyState, Loading, formatDateTime } from '@/components/ui'
import type { OfferComparisonResponse, OfferComparisonRow } from '@/types'

/** One row of the table: a label and how to read it out of each offer. */
const ROWS: { label: string; hint?: string; render: (row: OfferComparisonRow) => string }[] = [
  { label: '公司', render: (r) => r.company },
  { label: '岗位', render: (r) => r.title },
  { label: '城市', render: (r) => r.city ?? '—' },
  { label: '币种', hint: '不同币种不做换算', render: (r) => r.currency },
  { label: '基础年薪', render: (r) => money(r.base_annual, r.currency) },
  {
    label: '首年保证现金',
    hint: '可以确定拿到的钱',
    render: (r) => money(r.first_year_guaranteed_cash, r.currency),
  },
  {
    label: '首年目标现金',
    hint: '含目标奖金，非保证',
    render: (r) => money(r.first_year_target_cash, r.currency),
  },
  {
    label: '估算总包',
    render: (r) =>
      r.equity_excluded && r.estimated_first_year_total_comp === null
        ? '—（股权未计入）'
        : money(r.estimated_first_year_total_comp, r.currency),
  },
  { label: '目标奖金', render: (r) => money(r.bonus_target, r.currency) },
  { label: '签字费', render: (r) => money(r.signing_bonus, r.currency) },
  {
    label: '股权',
    render: (r) => {
      if (r.stock_value === null) return '—'
      const type = EQUITY_TYPES.find((t) => t.value === r.stock_type)?.label ?? ''
      return `${money(r.stock_value, r.currency)}${type ? ` · ${type}` : ''}${
        r.equity_excluded ? '（未计入总包）' : ''
      }`
    },
  },
  {
    label: '远程政策',
    render: (r) => REMOTE_POLICIES.find((p) => p.value === r.remote_policy)?.label ?? '—',
  },
  { label: '工作地点', render: (r) => r.work_location ?? '—' },
  { label: '入职日期', render: (r) => r.proposed_start_date ?? '—' },
  {
    label: '决定截止',
    render: (r) => (r.decision_deadline ? formatDateTime(r.decision_deadline) : '—'),
  },
  { label: '本次投递简历', render: (r) => r.resume_label ?? '未记录' },
]

/** Benefits are shown as entered - never converted into money. */
function benefitRows(rows: OfferComparisonRow[]): string[] {
  const keys = new Set<string>()
  for (const row of rows) {
    for (const key of Object.keys(row.benefits ?? {})) keys.add(key)
  }
  return [...keys].sort()
}

const BENEFIT_LABEL: Record<string, string> = {
  paid_leave: '带薪年假',
  remote_policy: '远程政策',
  flex_time: '弹性工作',
  housing_support: '住房支持',
  relocation_support: '搬迁支持',
  transport_support: '交通支持',
  visa_support: '签证支持',
  education_budget: '学习预算',
  language_allowance: '语言补贴',
  healthcare: '医疗',
  other: '其他',
}

function benefitValue(value: unknown): string {
  if (value === true) return '有'
  if (value === false || value === null || value === undefined) return '—'
  return String(value)
}

export default function OfferComparisonPage() {
  const [params] = useSearchParams()
  const ids = params.getAll('id').map(Number).filter(Number.isFinite)

  const [data, setData] = useState<OfferComparisonResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (ids.length < 2) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      setData(await api.compareOffers(ids))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '加载比较失败')
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.toString()])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Offer 比较</h1>
          <p>
            并列显示各项条件。系统<strong>不会</strong>给出综合评分或推荐结论 ——
            哪个更合适只有你自己知道。
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to="/offers">
            返回 Offer
          </Link>
        </div>
      </div>

      {error ? <Alert tone="error">{error}</Alert> : null}
      {loading ? <Loading text="正在加载…" /> : null}

      {!loading && ids.length < 2 ? (
        <EmptyState
          icon="⚖️"
          title="请选择至少两个 Offer"
          text="在 Offer 列表里勾选 2–4 个，然后点「比较所选」。"
        />
      ) : null}

      {!loading && data ? (
        <>
          {data.mixed_currency ? (
            <Alert tone="warn">
              所选 Offer 涉及多种币种（{data.currencies.join('、')}），
              金额按<strong>原币种</strong>显示。系统不会换算，也不会跨币种排名。
            </Alert>
          ) : null}
          {data.notes.map((note) => (
            <Alert key={note} tone="info">
              {note}
            </Alert>
          ))}

          <Card title="条件对比" sub={data.message}>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>项目</th>
                    {data.rows.map((row) => (
                      <th key={row.offer_id}>
                        <Link to={`/offers/${row.offer_id}`}>{row.company}</Link>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {ROWS.map((definition) => (
                    <tr key={definition.label}>
                      <td>
                        <div className="cell-title">{definition.label}</div>
                        {definition.hint ? (
                          <div className="cell-sub">{definition.hint}</div>
                        ) : null}
                      </td>
                      {data.rows.map((row) => (
                        <td key={row.offer_id}>{definition.render(row)}</td>
                      ))}
                    </tr>
                  ))}
                  <tr>
                    <td className="cell-title">截止状态</td>
                    {data.rows.map((row) => (
                      <td key={row.offer_id}>
                        <DeadlineChip state={row.deadline_state} />
                      </td>
                    ))}
                  </tr>
                  {benefitRows(data.rows).map((key) => (
                    <tr key={key}>
                      <td>
                        <div className="cell-title">{BENEFIT_LABEL[key] ?? key}</div>
                        <div className="cell-sub">非现金条件，不折算成金额</div>
                      </td>
                      {data.rows.map((row) => (
                        <td key={row.offer_id}>
                          {benefitValue((row.benefits ?? {})[key])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      ) : null}
    </>
  )
}
