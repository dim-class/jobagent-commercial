// Offer UI pieces, shared by the board, the detail page and the job card.
//
// Two things this file is careful about:
//   * the company's offer and your counter are labelled differently and never
//     rendered as the same thing;
//   * money is always shown with its currency, and never converted.

import { useEffect, useState } from 'react'

import { api } from '@/api/client'
import { Alert, Modal } from '@/components/ui'
import type {
  CompetingOfferOut,
  CompensationFields,
  CompensationSummary,
  CounterPayload,
  DeadlineState,
  DeclineReason,
  EquityType,
  OfferCreatePayload,
  OfferCurrency,
  OfferOut,
  OfferRemotePolicy,
  OfferRevisionOut,
  OfferStatus,
  RevisionCreatePayload,
} from '@/types'

export const CURRENCIES: OfferCurrency[] = ['CNY', 'JPY', 'USD', 'EUR', 'HKD', 'SGD', 'GBP']

export const REMOTE_POLICIES: { value: OfferRemotePolicy; label: string }[] = [
  { value: 'onsite', label: '现场办公' },
  { value: 'hybrid', label: '混合办公' },
  { value: 'remote', label: '远程' },
  { value: 'unknown', label: '未说明' },
]

export const EQUITY_TYPES: { value: EquityType; label: string }[] = [
  { value: 'rsu', label: 'RSU' },
  { value: 'options', label: '期权' },
  { value: 'restricted_stock', label: '限制性股票' },
  { value: 'unknown', label: '未说明' },
]

export const DECLINE_REASONS: { value: DeclineReason; label: string }[] = [
  { value: 'salary', label: '薪资' },
  { value: 'role_content', label: '岗位内容' },
  { value: 'location', label: '地点' },
  { value: 'remote_policy', label: '远程政策' },
  { value: 'company', label: '公司' },
  { value: 'growth', label: '发展空间' },
  { value: 'accepted_other_offer', label: '接受其他Offer' },
  { value: 'visa', label: '签证' },
  { value: 'start_date', label: '入职时间' },
  { value: 'personal', label: '个人原因' },
  { value: 'other', label: '其他' },
]

export const OFFER_STATUS_TONE: Record<OfferStatus, string> = {
  draft: 'chip',
  received: 'chip chip-good',
  negotiating: 'chip chip-good',
  accepted: 'chip chip-good',
  declined: 'chip',
  withdrawn: 'chip',
  expired: 'chip',
}

const DEADLINE_LABEL: Record<DeadlineState, string> = {
  none: '无截止日期',
  today: '今天截止',
  tomorrow: '明天截止',
  soon: '3天内截止',
  later: '稍后截止',
  past: '已过截止日期',
}

/** Deliberately calm: a deadline is information, not an alarm. */
const DEADLINE_TONE: Record<DeadlineState, string> = {
  none: 'chip',
  today: 'chip chip-bad',
  tomorrow: 'chip chip-bad',
  soon: 'chip',
  later: 'chip',
  past: 'chip',
}

export function money(value: number | null | undefined, currency: string): string {
  if (value === null || value === undefined) return '—'
  return `${currency} ${value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
}

export function DeadlineChip({ state }: { state: DeadlineState }) {
  if (state === 'none') return null
  return <span className={DEADLINE_TONE[state]}>{DEADLINE_LABEL[state]}</span>
}

/**
 * The four headline figures.
 *
 * Guaranteed and target are shown side by side on purpose - collapsing them
 * into one "package" is the most common way an offer comparison misleads.
 */
export function CompensationCards({
  summary,
  currency,
}: {
  summary: CompensationSummary | null
  currency: OfferCurrency
}) {
  if (!summary) {
    return <p className="faint small mt-0">还没有填写薪酬明细。</p>
  }
  return (
    <>
      <div className="grid grid-3">
        <div className="subscore">
          <div className="subscore-label">基础年薪</div>
          <div className="subscore-value">{money(summary.base_annual, currency)}</div>
        </div>
        <div className="subscore">
          <div className="subscore-label">首年保证现金</div>
          <div className="subscore-value">
            {money(summary.first_year_guaranteed_cash, currency)}
          </div>
          <div className="subscore-label">含签字费等一次性收入</div>
        </div>
        <div className="subscore">
          <div className="subscore-label">首年目标现金</div>
          <div className="subscore-value">
            {money(summary.first_year_target_cash, currency)}
          </div>
          <div className="subscore-label">含目标奖金，非保证到手</div>
        </div>
        <div className="subscore">
          <div className="subscore-label">估算总包（首年）</div>
          <div className="subscore-value">
            {summary.equity_excluded && summary.estimated_first_year_total_comp === null
              ? '—'
              : money(summary.estimated_first_year_total_comp, currency)}
          </div>
          {summary.equity_excluded ? (
            <div className="subscore-label">股权未计入可比较总包</div>
          ) : null}
        </div>
        <div className="subscore">
          <div className="subscore-label">常态保证现金</div>
          <div className="subscore-value">
            {money(summary.steady_state_guaranteed_cash, currency)}
          </div>
          <div className="subscore-label">第二年起，不含签字费</div>
        </div>
        <div className="subscore">
          <div className="subscore-label">股权折算（每年）</div>
          <div className="subscore-value">{money(summary.equity_annualized, currency)}</div>
        </div>
      </div>
      {summary.notes.length ? (
        <ul className="bullet-list mt-1">
          {summary.notes.map((note) => (
            <li key={note} className="small faint">
              {note}
            </li>
          ))}
        </ul>
      ) : null}
    </>
  )
}

/** One row of the negotiation timeline. Internal enum names never shown. */
export function RevisionLine({ revision }: { revision: OfferRevisionOut }) {
  const summary = revision.summary
  return (
    <div className="mt-1">
      <div className="row-between">
        <div>
          <strong>{revision.revision_type_label}</strong>{' '}
          <span className={revision.is_company_offer ? 'chip chip-good' : 'chip'}>
            {revision.is_company_offer ? '公司方' : '我方诉求'}
          </span>
          {revision.corrects_revision_id ? <span className="chip">更正记录</span> : null}
        </div>
        <span className="faint small">
          {money(summary.base_annual, revision.currency)}
        </span>
      </div>
      <div className="cell-sub">
        {summary.first_year_guaranteed_cash !== null ? (
          <>保证现金 {money(summary.first_year_guaranteed_cash, revision.currency)} · </>
        ) : null}
        {summary.first_year_target_cash !== null ? (
          <>目标现金 {money(summary.first_year_target_cash, revision.currency)}</>
        ) : null}
      </div>
      {revision.other_request ? (
        <div className="cell-sub">其他诉求：{revision.other_request}</div>
      ) : null}
      {revision.salary_text_original ? (
        <div className="cell-sub faint">原文：{revision.salary_text_original}</div>
      ) : null}
      {revision.notes ? <div className="cell-sub">{revision.notes}</div> : null}
    </div>
  )
}

// --------------------------------------------------------------------------
// compensation form, shared by every dialog that collects figures
// --------------------------------------------------------------------------

export interface CompState {
  base_salary_annual: string
  base_salary_monthly: string
  months_per_year: string
  bonus_guaranteed: string
  bonus_target: string
  signing_bonus: string
  stock_value: string
  stock_vesting_years: string
  allowances_annual: string
  salary_text_original: string
}

export const EMPTY_COMP: CompState = {
  base_salary_annual: '',
  base_salary_monthly: '',
  months_per_year: '',
  bonus_guaranteed: '',
  bonus_target: '',
  signing_bonus: '',
  stock_value: '',
  stock_vesting_years: '',
  allowances_annual: '',
  salary_text_original: '',
}

/** Empty stays empty - a blank field is "unknown", never 0. */
function num(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

export function toCompensation(state: CompState): CompensationFields {
  return {
    base_salary_annual: num(state.base_salary_annual),
    base_salary_monthly: num(state.base_salary_monthly),
    months_per_year: num(state.months_per_year),
    bonus_guaranteed: num(state.bonus_guaranteed),
    bonus_target: num(state.bonus_target),
    signing_bonus: num(state.signing_bonus),
    stock_value: num(state.stock_value),
    stock_vesting_years: num(state.stock_vesting_years),
    allowances_annual: num(state.allowances_annual),
    salary_text_original: state.salary_text_original.trim() || null,
  }
}

export function CompensationForm({
  state,
  onChange,
  currency,
  showOriginalText = true,
}: {
  state: CompState
  onChange: (next: CompState) => void
  currency: OfferCurrency
  showOriginalText?: boolean
}) {
  function set<K extends keyof CompState>(key: K, value: string) {
    onChange({ ...state, [key]: value })
  }

  return (
    <>
      <div className="field-row">
        <div className="field">
          <label htmlFor="base-annual">基础年薪（{currency}）</label>
          <input
            id="base-annual"
            type="number"
            min={0}
            value={state.base_salary_annual}
            onChange={(e) => set('base_salary_annual', e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="base-monthly">月薪</label>
          <input
            id="base-monthly"
            type="number"
            min={0}
            value={state.base_salary_monthly}
            onChange={(e) => set('base_salary_monthly', e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="months">几薪</label>
          <input
            id="months"
            type="number"
            min={1}
            max={24}
            placeholder="如 14"
            value={state.months_per_year}
            onChange={(e) => set('months_per_year', e.target.value)}
          />
        </div>
      </div>
      <div className="field-hint">
        填了年薪就以年薪为准。留空表示「未说明」，不会被当成 0。
      </div>

      <div className="field-row mt-1">
        <div className="field">
          <label htmlFor="bonus-guaranteed">保证奖金</label>
          <input
            id="bonus-guaranteed"
            type="number"
            min={0}
            value={state.bonus_guaranteed}
            onChange={(e) => set('bonus_guaranteed', e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="bonus-target">目标奖金</label>
          <input
            id="bonus-target"
            type="number"
            min={0}
            value={state.bonus_target}
            onChange={(e) => set('bonus_target', e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="signing">签字费（一次性）</label>
          <input
            id="signing"
            type="number"
            min={0}
            value={state.signing_bonus}
            onChange={(e) => set('signing_bonus', e.target.value)}
          />
        </div>
      </div>
      <div className="field-hint">
        目标奖金不会被算进「保证现金」；签字费只计入首年。
      </div>

      <div className="field-row mt-1">
        <div className="field">
          <label htmlFor="stock">股权总价值</label>
          <input
            id="stock"
            type="number"
            min={0}
            value={state.stock_value}
            onChange={(e) => set('stock_value', e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="vesting">归属年限</label>
          <input
            id="vesting"
            type="number"
            min={1}
            max={20}
            value={state.stock_vesting_years}
            onChange={(e) => set('stock_vesting_years', e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="allowances">其他年度补贴</label>
          <input
            id="allowances"
            type="number"
            min={0}
            value={state.allowances_annual}
            onChange={(e) => set('allowances_annual', e.target.value)}
          />
        </div>
      </div>
      <div className="field-hint">
        只有同时填写股权价值和归属年限才会折算；否则会标注「未计入可比较总包」。
      </div>

      {showOriginalText ? (
        <div className="field mt-1">
          <label htmlFor="original">Offer 原文（可选）</label>
          <textarea
            id="original"
            rows={2}
            placeholder="例如：年薪600万日元，其中基本工资…"
            value={state.salary_text_original}
            onChange={(e) => set('salary_text_original', e.target.value)}
          />
          <div className="field-hint">原文会完整保留；你填写的数值始终优先。</div>
        </div>
      ) : null}
    </>
  )
}

// --------------------------------------------------------------------------
// dialogs
// --------------------------------------------------------------------------

export function RecordOfferDialog({
  onClose,
  onSubmit,
  onParse,
  busy,
}: {
  onClose: () => void
  onSubmit: (payload: OfferCreatePayload) => void
  onParse?: (text: string) => Promise<void>
  busy?: boolean
  }) {
  const [currency, setCurrency] = useState<OfferCurrency>('CNY')
  const [comp, setComp] = useState<CompState>(EMPTY_COMP)
  const [deadline, setDeadline] = useState('')
  const [startDate, setStartDate] = useState('')
  const [remote, setRemote] = useState<OfferRemotePolicy>('unknown')
  const [location, setLocation] = useState('')
  const [notes, setNotes] = useState('')

  return (
    <Modal
      title="记录 Offer"
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
            onClick={() =>
              onSubmit({
                confirmed: true,
                currency,
                decision_deadline: deadline ? new Date(deadline).toISOString() : null,
                proposed_start_date: startDate || null,
                remote_policy: remote,
                work_location: location.trim() || null,
                notes: notes.trim() || null,
                initial: toCompensation(comp),
              })
            }
          >
            确认记录
          </button>
        </>
      }
    >
      <div className="field-row">
        <div className="field">
          <label htmlFor="currency">币种</label>
          <select
            id="currency"
            value={currency}
            onChange={(e) => setCurrency(e.target.value as OfferCurrency)}
          >
            {CURRENCIES.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="deadline">决定截止时间</label>
          <input
            id="deadline"
            type="datetime-local"
            value={deadline}
            onChange={(e) => setDeadline(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="start">建议入职日期</label>
          <input
            id="start"
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </div>
      </div>

      <CompensationForm state={comp} onChange={setComp} currency={currency} />

      {onParse ? (
        <div className="btn-row">
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={busy || !comp.salary_text_original.trim()}
            onClick={() => void onParse(comp.salary_text_original)}
          >
            从原文识别金额
          </button>
          <span className="faint small">识别结果只是建议，需要你核对。</span>
        </div>
      ) : null}

      <div className="field-row mt-1">
        <div className="field">
          <label htmlFor="remote">远程政策</label>
          <select
            id="remote"
            value={remote}
            onChange={(e) => setRemote(e.target.value as OfferRemotePolicy)}
          >
            {REMOTE_POLICIES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="location">工作地点</label>
          <input
            id="location"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
          />
        </div>
      </div>

      <div className="field">
        <label htmlFor="offer-notes">备注（可选）</label>
        <textarea
          id="offer-notes"
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
      </div>

      <Alert tone="info">
        JobAgent 只记录你收到的 Offer，不会替你回复、谈判或接受。
      </Alert>
    </Modal>
  )
}

/** 我的谈薪诉求 - stored, never sent, and never shown as a company offer. */
export function CounterDialog({
  offer,
  onClose,
  onSubmit,
  busy,
}: {
  offer: OfferOut
  onClose: () => void
  onSubmit: (payload: CounterPayload) => void
  busy?: boolean
}) {
  const [comp, setComp] = useState<CompState>(EMPTY_COMP)
  const [startDate, setStartDate] = useState('')
  const [remote, setRemote] = useState<OfferRemotePolicy | ''>('')
  const [other, setOther] = useState('')
  const [notes, setNotes] = useState('')

  return (
    <Modal
      title="我的谈薪诉求"
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
            onClick={() =>
              onSubmit({
                ...toCompensation(comp),
                requested_start_date: startDate || null,
                requested_remote_policy: (remote || null) as OfferRemotePolicy | null,
                other_request: other.trim() || null,
                notes: notes.trim() || null,
              })
            }
          >
            记录诉求
          </button>
        </>
      }
    >
      <p className="mt-0">
        <strong>{offer.company}</strong>
        <br />
        {offer.title}
      </p>
      <Alert tone="info">
        这里记录的是<strong>你打算要求的条件</strong>，不是公司的 Offer。
        JobAgent 不会替你发送任何消息。
      </Alert>

      <CompensationForm
        state={comp}
        onChange={setComp}
        currency={offer.currency}
        showOriginalText={false}
      />

      <div className="field-row mt-1">
        <div className="field">
          <label htmlFor="want-start">希望入职日期</label>
          <input
            id="want-start"
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="want-remote">希望的远程政策</label>
          <select
            id="want-remote"
            value={remote}
            onChange={(e) => setRemote(e.target.value as OfferRemotePolicy | '')}
          >
            <option value="">不提</option>
            {REMOTE_POLICIES.filter((r) => r.value !== 'unknown').map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="field">
        <label htmlFor="other-request">其他诉求</label>
        <input id="other-request" value={other} onChange={(e) => setOther(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="counter-notes">备注</label>
        <textarea
          id="counter-notes"
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
      </div>
    </Modal>
  )
}

/** A company revision: what they came back with. */
export function CompanyRevisionDialog({
  offer,
  onClose,
  onSubmit,
  busy,
}: {
  offer: OfferOut
  onClose: () => void
  onSubmit: (payload: RevisionCreatePayload) => void
  busy?: boolean
}) {
  const [comp, setComp] = useState<CompState>(EMPTY_COMP)
  const [isFinal, setIsFinal] = useState(false)
  const [notes, setNotes] = useState('')

  return (
    <Modal
      title="记录公司调整后的 Offer"
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
            onClick={() =>
              onSubmit({
                revision_type: isFinal ? 'final' : 'company_revision',
                source: 'company',
                ...toCompensation(comp),
                notes: notes.trim() || null,
              })
            }
          >
            记录
          </button>
        </>
      }
    >
      <p className="mt-0 muted">
        填写<strong>公司实际给出</strong>的条件。之前的记录不会被覆盖。
      </p>
      <CompensationForm state={comp} onChange={setComp} currency={offer.currency} />
      <label className="checkbox-row mt-1">
        <input
          type="checkbox"
          checked={isFinal}
          onChange={(e) => setIsFinal(e.target.checked)}
        />
        <span>这是最终 Offer</span>
      </label>
      <div className="field">
        <label htmlFor="rev-notes">备注</label>
        <textarea
          id="rev-notes"
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
      </div>
    </Modal>
  )
}

export function AcceptDialog({
  offer,
  onClose,
  onSubmit,
  busy,
}: {
  offer: OfferOut
  onClose: () => void
  onSubmit: (notes: string) => void
  busy?: boolean
}) {
  const [notes, setNotes] = useState('')
  // Other offers still awaiting a decision. Shown so nothing is forgotten -
  // accepting is never blocked, and none of them is declined automatically.
  const [competing, setCompeting] = useState<CompetingOfferOut[]>([])
  const summary = offer.current_company_summary

  useEffect(() => {
    let cancelled = false
    api
      .competingOffers(offer.id)
      .then((data) => {
        if (!cancelled) setCompeting(data.items)
      })
      .catch(() => {
        // A warning that cannot load must not stand between you and a decision.
      })
    return () => {
      cancelled = true
    }
  }, [offer.id])

  return (
    <Modal
      title="确认接受该 Offer？"
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
            onClick={() => onSubmit(notes)}
          >
            确认接受
          </button>
        </>
      }
    >
      <p className="mt-0">
        <strong>{offer.company}</strong>
        <br />
        {offer.title}
        {offer.city ? ` · ${offer.city}` : ''}
      </p>
      <dl className="meta-list">
        <span>基础年薪：{money(summary?.base_annual ?? null, offer.currency)}</span>
        <span>
          首年保证现金：{money(summary?.first_year_guaranteed_cash ?? null, offer.currency)}
        </span>
        <span>入职日期：{offer.proposed_start_date ?? '未确定'}</span>
      </dl>
      <div className="field">
        <label htmlFor="accept-notes">备注（可选）</label>
        <textarea
          id="accept-notes"
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
      </div>
      {competing.length ? (
        <Alert tone="warn">
          <div>
            你还有 {competing.length} 个 Offer 尚未决定。接受这一个
            <strong>不会</strong>自动拒绝它们 —— 需要你自己逐个答复。
          </div>
          <ul className="bullet-list">
            {competing.map((item) => (
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
      ) : null}

      <Alert tone="info">
        这里记录的是<strong>你已经做出的决定</strong>。JobAgent 不会替你向公司答复。
        接受时的薪酬会被固定保存，之后再补充记录也不会改变它。
      </Alert>
    </Modal>
  )
}

export function DeclineDialog({
  offer,
  onClose,
  onSubmit,
  busy,
}: {
  offer: OfferOut
  onClose: () => void
  onSubmit: (reason: DeclineReason, notes: string) => void
  busy?: boolean
}) {
  const [reason, setReason] = useState<DeclineReason>('salary')
  const [notes, setNotes] = useState('')

  return (
    <Modal
      title="确认拒绝该 Offer？"
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className="btn-danger"
            disabled={busy}
            onClick={() => onSubmit(reason, notes)}
          >
            确认拒绝
          </button>
        </>
      }
    >
      <p className="mt-0">
        <strong>{offer.company}</strong>
        <br />
        {offer.title}
      </p>
      <div className="field">
        <label htmlFor="decline-reason">原因</label>
        <select
          id="decline-reason"
          value={reason}
          onChange={(e) => setReason(e.target.value as DeclineReason)}
        >
          {DECLINE_REASONS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="decline-notes">备注（可选）</label>
        <textarea
          id="decline-notes"
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
      </div>
      <Alert tone="info">
        这会记录为<strong>你主动拒绝</strong>，不会被统计成对方拒绝你。
      </Alert>
    </Modal>
  )
}
