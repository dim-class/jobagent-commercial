// Offer decision support (v1.0)
//
// Everything here renders numbers the backend computed deterministically from
// weights and ratings the user entered themselves. Two rules shape the UI:
//
//   * a total score is never shown on its own - the weight, the dimension
//     score and the contribution that produced it are always beside it;
//   * "未知" is a visible third state, never a silent zero.

import { useState } from 'react'

import { Alert, Modal } from '@/components/ui'
import { money } from '@/components/offer'
import type {
  CompetingOfferOut,
  DealBreakerItem,
  DealBreakerKind,
  DealBreakerResult,
  DecisionDimension,
  DimensionScoreOut,
  NegotiationPositionOut,
  OfferScoreOut,
} from '@/types'

/** The dimensions, in the order they are presented. Labels mirror the backend. */
export const DIMENSIONS: {
  value: DecisionDimension
  label: string
  hint: string
  derived?: boolean
}[] = [
  {
    value: 'compensation',
    label: '薪酬',
    hint: '由已记录的保证现金自动计算，无需评分',
    derived: true,
  },
  { value: 'career_growth', label: '职业成长', hint: '晋升空间、能学到的东西' },
  { value: 'role_fit', label: '岗位匹配', hint: '和你想做的事情有多接近' },
  {
    value: 'remote_work',
    label: '远程办公',
    hint: '取自 Offer 的远程政策；未记录时用你的评分',
    derived: true,
  },
  { value: 'location', label: '地点', hint: '城市、通勤' },
  { value: 'work_life_balance', label: '工作生活平衡', hint: '加班强度、休假' },
  { value: 'company_stability', label: '公司稳定性', hint: '业务、融资、裁员风险' },
  { value: 'technology_fit', label: '技术契合', hint: '技术栈是否是你想深入的方向' },
  { value: 'language_environment', label: '语言环境', hint: '日常沟通语言' },
  {
    value: 'visa_support',
    label: '签证支持',
    hint: '取自 Offer 的福利记录；未记录时用你的评分',
    derived: true,
  },
  { value: 'brand_value', label: '品牌价值', hint: '简历上的分量' },
]

export const DIMENSION_LABEL: Record<DecisionDimension, string> = DIMENSIONS.reduce(
  (acc, item) => ({ ...acc, [item.value]: item.label }),
  {} as Record<DecisionDimension, string>,
)

export const DEAL_BREAKER_KINDS: {
  value: DealBreakerKind
  label: string
  input: 'number' | 'none' | 'text' | 'date'
  hint: string
}[] = [
  {
    value: 'minimum_guaranteed_cash',
    label: '最低保证现金',
    input: 'number',
    hint: '按比较的基准币种填写',
  },
  { value: 'requires_visa_support', label: '必须提供签证支持', input: 'none', hint: '' },
  { value: 'requires_remote_or_hybrid', label: '必须远程或混合办公', input: 'none', hint: '' },
  { value: 'required_location', label: '必须在指定城市', input: 'text', hint: '如：东京' },
  { value: 'latest_start_date', label: '最晚入职日期', input: 'date', hint: '' },
]

const RESULT_TONE: Record<DealBreakerResult, string> = {
  passed: 'chip chip-good',
  failed: 'chip chip-bad',
  unknown: 'chip',
}

const RESULT_LABEL: Record<DealBreakerResult, string> = {
  passed: '满足',
  failed: '不满足',
  unknown: '未知',
}

/** 0-1 as a percentage, or a dash. Never renders 0 for "unknown". */
export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

export function score100(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return String(Math.round(value * 100))
}

// ------------------------------------------------------------------ weights

export function WeightEditor({
  weights,
  onChange,
  disabled,
}: {
  weights: Partial<Record<DecisionDimension, number>>
  onChange: (next: Partial<Record<DecisionDimension, number>>) => void
  disabled?: boolean
}) {
  const total = Object.values(weights).reduce((sum, value) => sum + (value ?? 0), 0)

  return (
    <>
      <p className="field-hint mt-0">
        用 0–10 表示每个维度对你的重要程度。系统只看<strong>比例</strong>，
        计算时会自动归一化 —— 5/3/2 和 50/30/20 完全一样。设成 0 表示不在意，
        该维度不会参与评分。
      </p>

      <div className="grid grid-3">
        {DIMENSIONS.map((dimension) => {
          const raw = weights[dimension.value] ?? 0
          const share = total > 0 ? raw / total : null
          return (
            <div key={dimension.value} className="field">
              <label htmlFor={`weight-${dimension.value}`}>
                {dimension.label}
                {dimension.derived ? <span className="faint small"> · 自动</span> : null}
              </label>
              <input
                id={`weight-${dimension.value}`}
                type="range"
                min={0}
                max={10}
                step={1}
                value={raw}
                disabled={disabled}
                onChange={(event) =>
                  onChange({ ...weights, [dimension.value]: Number(event.target.value) })
                }
              />
              <div className="field-hint">
                {raw === 0 ? '不在意' : `${raw} / 10`}
                {share !== null && raw > 0 ? ` · 占比 ${pct(share, 1)}` : ''}
              </div>
            </div>
          )
        })}
      </div>

      {total === 0 ? (
        <Alert tone="warn">
          所有权重都是 0，系统<strong>不会</strong>替你假设一个平均分配 ——
          没有权重就没有综合评分。请先设定你在意什么。
        </Alert>
      ) : null}
    </>
  )
}

// ------------------------------------------------------------------ ratings

export function RatingRow({
  dimension,
  value,
  onChange,
  disabled,
}: {
  dimension: (typeof DIMENSIONS)[number]
  value: number | null
  onChange: (next: number | null) => void
  disabled?: boolean
}) {
  return (
    <div className="btn-row" style={{ alignItems: 'center', marginBottom: 6 }}>
      <span style={{ minWidth: 120 }}>
        <span className="cell-title">{dimension.label}</span>
        <div className="cell-sub">{dimension.hint}</div>
      </span>
      {[1, 2, 3, 4, 5].map((rating) => (
        <button
          key={rating}
          type="button"
          className={value === rating ? 'btn-primary btn-sm' : 'btn-sm'}
          disabled={disabled}
          onClick={() => onChange(value === rating ? null : rating)}
        >
          {rating}
        </button>
      ))}
      <button
        type="button"
        className={value === null ? 'btn-sm' : 'btn-ghost btn-sm'}
        disabled={disabled}
        onClick={() => onChange(null)}
      >
        不确定
      </button>
    </div>
  )
}

export function RatingEditor({
  ratings,
  onChange,
  disabled,
}: {
  ratings: Partial<Record<DecisionDimension, number | null>>
  onChange: (next: Partial<Record<DecisionDimension, number | null>>) => void
  disabled?: boolean
}) {
  return (
    <>
      <p className="field-hint mt-0">
        1 = 很差，5 = 很好。<strong>只有你能给公司打分</strong> ——
        系统不会替你评价任何一家公司。留空的维度会被排除在评分之外，
        而不是当成 0 分。
      </p>
      {DIMENSIONS.map((dimension) => (
        <RatingRow
          key={dimension.value}
          dimension={dimension}
          value={ratings[dimension.value] ?? null}
          disabled={disabled}
          onChange={(next) => onChange({ ...ratings, [dimension.value]: next })}
        />
      ))}
    </>
  )
}

// ------------------------------------------------------- score presentation

export function CoverageBar({ coverage, floor }: { coverage: number; floor: number }) {
  const thin = coverage < floor
  return (
    <div>
      <div className="subscore-label">
        信息覆盖率 {pct(coverage)}
        {thin ? <span className="chip chip-bad" style={{ marginLeft: 6 }}>数据不足</span> : null}
      </div>
      <div className="bar">
        <span style={{ width: `${Math.max(0, Math.min(100, coverage * 100))}%` }} />
      </div>
    </div>
  )
}

export function DimensionRow({ row }: { row: DimensionScoreOut }) {
  return (
    <tr>
      <td>
        <div className="cell-title">{row.label}</div>
        <div className="cell-sub">{row.detail || (row.known ? '' : '未记录')}</div>
      </td>
      <td className="nowrap">{pct(row.weight, 1)}</td>
      <td className="nowrap">
        {row.known ? (
          score100(row.score)
        ) : (
          <span className="chip">未知</span>
        )}
      </td>
      <td className="nowrap">
        {row.contribution === null ? '不计入' : row.contribution.toFixed(3)}
      </td>
    </tr>
  )
}

/** The whole breakdown for one offer: total, coverage and every contribution. */
export function ScoreBreakdown({
  score,
  minCoverage,
}: {
  score: OfferScoreOut
  minCoverage: number
}) {
  const sum = Object.values(score.weighted_contributions).reduce(
    (acc, value) => acc + (value ?? 0),
    0,
  )

  return (
    <div>
      <div className="grid grid-2">
        <div className="subscore">
          <div className="subscore-label">综合评分（满分 100）</div>
          <div className="subscore-value">{score100(score.total_score)}</div>
          <div className="field-hint">
            {score.total_score === null
              ? '数据不足，没有评分'
              : '只是你自己的权重乘以你自己的评分'}
          </div>
        </div>
        <CoverageBar coverage={score.coverage} floor={minCoverage} />
      </div>

      <div className="table-wrap mt-1">
        <table>
          <thead>
            <tr>
              <th>维度</th>
              <th>权重</th>
              <th>得分</th>
              <th>贡献</th>
            </tr>
          </thead>
          <tbody>
            {score.dimension_scores.map((row) => (
              <DimensionRow key={row.dimension} row={row} />
            ))}
            <tr>
              <td className="cell-title">合计（已知维度）</td>
              <td className="nowrap">{pct(score.coverage, 1)}</td>
              <td />
              <td className="nowrap">{sum.toFixed(3)}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="field-hint mt-1">
        综合评分 = 贡献合计 ÷ 已知维度权重合计。缺失的维度<strong>不计入</strong>，
        既不加分也不扣分，只会拉低覆盖率。
      </p>
    </div>
  )
}

export function DealBreakerChips({ checks }: { checks: OfferScoreOut['deal_breakers'] }) {
  if (!checks.length) return <p className="faint small mt-0">未设置硬性条件</p>
  return (
    <div className="chip-list">
      {checks.map((check) => (
        <span key={check.kind} className={RESULT_TONE[check.result]}>
          {check.label}：{RESULT_LABEL[check.result]}
          {check.detail ? ` · ${check.detail}` : ''}
        </span>
      ))}
    </div>
  )
}

// ----------------------------------------------------------- deal breakers

export function DealBreakerEditor({
  items,
  onChange,
  disabled,
}: {
  items: DealBreakerItem[]
  onChange: (next: DealBreakerItem[]) => void
  disabled?: boolean
}) {
  function valueOf(kind: DealBreakerKind): DealBreakerItem | undefined {
    return items.find((item) => item.kind === kind)
  }

  function toggle(kind: DealBreakerKind, on: boolean) {
    onChange(
      on
        ? [...items, { kind, value: kind === 'requires_visa_support' ? true : null }]
        : items.filter((item) => item.kind !== kind),
    )
  }

  function setValue(kind: DealBreakerKind, value: DealBreakerItem['value']) {
    onChange(items.map((item) => (item.kind === kind ? { ...item, value } : item)))
  }

  return (
    <>
      <p className="field-hint mt-0">
        硬性条件只会<strong>标记</strong>是否满足，不会自动排除任何 Offer。
        没有记录相关信息时显示「未知」—— 那不等于不满足。
      </p>
      {DEAL_BREAKER_KINDS.map((kind) => {
        const active = valueOf(kind.value)
        return (
          <div key={kind.value} className="btn-row" style={{ alignItems: 'center' }}>
            <label className="checkbox-row" style={{ minWidth: 190 }}>
              <input
                type="checkbox"
                checked={Boolean(active)}
                disabled={disabled}
                onChange={(event) => toggle(kind.value, event.target.checked)}
              />
              <span>{kind.label}</span>
            </label>
            {active && kind.input !== 'none' ? (
              <input
                type={kind.input}
                value={active.value === null ? '' : String(active.value)}
                placeholder={kind.hint}
                disabled={disabled}
                style={{ maxWidth: 220 }}
                onChange={(event) =>
                  setValue(
                    kind.value,
                    kind.input === 'number'
                      ? event.target.value === ''
                        ? null
                        : Number(event.target.value)
                      : event.target.value || null,
                  )
                }
              />
            ) : null}
          </div>
        )
      })}
    </>
  )
}

// ------------------------------------------------------------- negotiation

export function NegotiationPanel({ position }: { position: NegotiationPositionOut }) {
  const { currency } = position
  return (
    <>
      <div className="grid grid-3">
        <div className="subscore">
          <div className="subscore-label">公司当前 Offer</div>
          <div className="subscore-value">
            {money(position.current_company_cash, currency)}
          </div>
        </div>
        <div className="subscore">
          <div className="subscore-label">我方最近诉求</div>
          <div className="subscore-value">
            {money(position.latest_candidate_ask, currency)}
          </div>
          <div className="field-hint">这是你的要求，不是公司的 Offer</div>
        </div>
        <div className="subscore">
          <div className="subscore-label">差距</div>
          <div className="subscore-value">{money(position.gap, currency)}</div>
        </div>
      </div>

      <div className="grid grid-3 mt-1">
        <div className="subscore">
          <div className="subscore-label">理想线</div>
          <div className="subscore-value">{money(position.ideal_total_cash, currency)}</div>
        </div>
        <div className="subscore">
          <div className="subscore-label">目标线</div>
          <div className="subscore-value">{money(position.target_total_cash, currency)}</div>
        </div>
        <div className="subscore">
          <div className="subscore-label">最低接受线</div>
          <div className="subscore-value">
            {money(position.minimum_total_cash, currency)}
          </div>
        </div>
      </div>

      {position.warnings.map((warning) => (
        <Alert key={warning} tone="warn">
          {warning}
        </Alert>
      ))}
    </>
  )
}

// -------------------------------------------------------- competing offers

/** Shown before accepting. It informs; it never blocks and never declines. */
export function CompetingOffersNotice({
  items,
  message,
}: {
  items: CompetingOfferOut[]
  message: string
}) {
  if (!items.length) return null
  return (
    <Alert tone="warn">
      <div>{message}</div>
      <ul className="bullet-list">
        {items.map((item) => (
          <li key={item.offer_id}>
            {item.company} · {item.title} · {item.status}
            {item.days_to_deadline !== null
              ? item.days_to_deadline < 0
                ? '（截止已过）'
                : `（还有 ${item.days_to_deadline} 天）`
              : ''}
          </li>
        ))}
      </ul>
    </Alert>
  )
}

// ------------------------------------------------------------- save dialog

export function SaveSnapshotDialog({
  defaultName,
  onClose,
  onSubmit,
  busy,
}: {
  defaultName: string
  onClose: () => void
  onSubmit: (name: string, notes: string) => void
  busy?: boolean
}) {
  const [name, setName] = useState(defaultName)
  const [notes, setNotes] = useState('')

  return (
    <Modal
      title="保存这次决策记录"
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => onSubmit(name, notes)}
          >
            保存
          </button>
        </>
      }
    >
      <div className="field">
        <label htmlFor="snapshot-name">名称</label>
        <input
          id="snapshot-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="snapshot-notes">当时的想法（可选）</label>
        <textarea
          id="snapshot-notes"
          rows={3}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
        />
      </div>
      <Alert tone="info">
        保存会把当前的 Offer、所用的薪酬版本、权重、评分、汇率与硬性条件
        <strong>整体冻结</strong>。之后修改任何一项都不会改变这条记录 ——
        它记录的是你当时看到的东西。
      </Alert>
    </Modal>
  )
}
