// One offer, end to end (v0.9).
//
// The compensation cards show the COMPANY's current offer. Your counters
// appear in the timeline as your requests - never as what is on the table.
//
// Once accepted, the figures come from the frozen accepted revision, so later
// records cannot change what you decided on.

import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import {
  AcceptDialog,
  CompanyRevisionDialog,
  CompensationCards,
  CounterDialog,
  DeadlineChip,
  DeclineDialog,
  OFFER_STATUS_TONE,
  REMOTE_POLICIES,
  RevisionLine,
  money,
} from '@/components/offer'
import { NegotiationPanel } from '@/components/decision'
import { Alert, Card, Loading, formatDateTime } from '@/components/ui'
import type {
  CounterPayload,
  DeclineReason,
  NegotiationPositionOut,
  OfferOut,
  RevisionCreatePayload,
} from '@/types'

type Tone = 'success' | 'error' | 'info' | 'warn'
type Feedback = { tone: Tone; text: string } | null

function pct(value: number | null): string {
  if (value === null) return '—'
  return `${(value * 100).toFixed(1)}%`
}

export default function OfferDetailPage() {
  const params = useParams()
  const offerId = Number(params.offerId)

  const [offer, setOffer] = useState<OfferOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [busy, setBusy] = useState(false)
  const [countering, setCountering] = useState(false)
  const [revising, setRevising] = useState(false)
  const [accepting, setAccepting] = useState(false)
  const [declining, setDeclining] = useState(false)
  // Deterministic; reads the company revision and your own targets only.
  const [position, setPosition] = useState<NegotiationPositionOut | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [loaded, negotiationPosition] = await Promise.all([
        api.getOffer(offerId),
        api.negotiationPosition(offerId),
      ])
      setOffer(loaded)
      setPosition(negotiationPosition)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载失败' })
    } finally {
      setLoading(false)
    }
  }, [offerId])

  useEffect(() => {
    if (Number.isFinite(offerId)) void load()
  }, [offerId, load])

  async function run(action: () => Promise<{ message: string }>, tone: Tone = 'success') {
    setBusy(true)
    try {
      const result = await action()
      setFeedback({ tone, text: result.message })
      await load()
      return true
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '操作失败' })
      return false
    } finally {
      setBusy(false)
    }
  }

  if (loading && !offer) return <Loading text="正在加载 Offer…" />
  if (!offer) return feedback ? <Alert tone="error">{feedback.text}</Alert> : null

  const open = offer.status === 'received' || offer.status === 'negotiating'
  const accepted = offer.status === 'accepted'
  const summary = accepted ? offer.accepted_summary : offer.current_company_summary
  const negotiation = offer.negotiation
  const remoteLabel =
    REMOTE_POLICIES.find((r) => r.value === offer.remote_policy)?.label ?? '未说明'

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{offer.company}</h1>
          <p>
            {offer.title}
            {offer.city ? ` · ${offer.city}` : ''}
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to={`/jobs/${offer.job_id}`}>
            查看岗位
          </Link>
          <Link className="btn" to="/offers">
            返回 Offer 列表
          </Link>
        </div>
      </div>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      <Card
        title={accepted ? '已接受的薪酬' : '公司当前 Offer'}
        sub={
          accepted
            ? '接受时的记录，之后新增的记录不会改变它'
            : '这是公司给出的条件，不包含你提出的诉求'
        }
        actions={
          <>
            <span className={OFFER_STATUS_TONE[offer.status]}>{offer.status_label}</span>
            <DeadlineChip state={offer.deadline_state} />
          </>
        }
      >
        <CompensationCards summary={summary} currency={offer.currency} />

        <dl className="meta-list mt-2">
          <span>本次投递简历：{offer.resume_label ?? '未记录'}</span>
          {offer.applied_at ? <span>投递于 {formatDateTime(offer.applied_at)}</span> : null}
          {offer.received_at ? (
            <span>收到于 {formatDateTime(offer.received_at)}</span>
          ) : null}
          {offer.decision_deadline ? (
            <span>决定截止 {formatDateTime(offer.decision_deadline)}</span>
          ) : null}
          <span>入职日期：{offer.proposed_start_date ?? '未确定'}</span>
          <span>远程政策：{remoteLabel}</span>
          {offer.work_location ? <span>地点：{offer.work_location}</span> : null}
          {offer.decline_reason_label ? (
            <span>拒绝原因：{offer.decline_reason_label}</span>
          ) : null}
        </dl>

        {offer.deadline_state === 'past' && open ? (
          <Alert tone="warn">
            截止日期已过。系统不会自动把它标记为过期 —— 如果这个 Offer 确实作废了，
            请手动标记。
          </Alert>
        ) : null}

        {open ? (
          <div className="btn-row mt-1">
            <button
              type="button"
              className="btn-primary btn-sm"
              disabled={busy}
              onClick={() => setAccepting(true)}
            >
              接受 Offer
            </button>
            <button
              type="button"
              className="btn-sm"
              disabled={busy}
              onClick={() => setCountering(true)}
            >
              记录我的诉求
            </button>
            <button
              type="button"
              className="btn-sm"
              disabled={busy}
              onClick={() => setRevising(true)}
            >
              记录公司调整
            </button>
            <button
              type="button"
              className="btn-ghost btn-sm"
              disabled={busy}
              onClick={() => setDeclining(true)}
            >
              拒绝 Offer
            </button>
            <button
              type="button"
              className="btn-ghost btn-sm"
              disabled={busy}
              onClick={() => void run(() => api.expireOffer(offer.id), 'info')}
            >
              标记为过期
            </button>
          </div>
        ) : null}
      </Card>

      {offer.latest_counter_summary && open ? (
        <Card
          title="我方最近的诉求"
          sub="这是你打算要求的条件，不是公司的 Offer；JobAgent 不会替你发送"
        >
          <div className="grid grid-3">
            <div className="subscore">
              <div className="subscore-label">要求的基础年薪</div>
              <div className="subscore-value">
                {money(offer.latest_counter_summary.base_annual, offer.currency)}
              </div>
            </div>
            <div className="subscore">
              <div className="subscore-label">要求的首年现金</div>
              <div className="subscore-value">
                {money(
                  offer.latest_counter_summary.first_year_target_cash,
                  offer.currency,
                )}
              </div>
            </div>
          </div>
        </Card>
      ) : null}

      {position ? (
        <Card
          title="谈薪定位"
          sub="公司当前 Offer 与你自己设定的目标线之间的距离"
          actions={
            <Link className="btn btn-sm" to="/offers">
              设定目标线
            </Link>
          }
        >
          <NegotiationPanel position={position} />
          <p className="field-hint mt-1">
            目标线在「决策分析」页按 Offer 填写。低于最低线只会提示，
            系统<strong>不会</strong>替你拒绝任何 Offer。
          </p>
        </Card>
      ) : null}

      <Card title="谈薪记录" sub="按时间顺序，历史记录不会被覆盖">
        {!offer.revisions.length ? (
          <p className="faint small mt-0">还没有填写任何薪酬记录。</p>
        ) : (
          offer.revisions.map((revision) => (
            <RevisionLine key={revision.id} revision={revision} />
          ))
        )}
      </Card>

      {negotiation.company_revisions >= 2 ? (
        <Card
          title="谈薪变化"
          sub="只比较公司先后给出的条件；你的诉求不参与计算"
        >
          <div className="grid grid-3">
            <div className="subscore">
              <div className="subscore-label">基础年薪变化</div>
              <div className="subscore-value">
                {money(negotiation.base.absolute, offer.currency)}
              </div>
              <div className="subscore-label">{pct(negotiation.base.percentage)}</div>
            </div>
            <div className="subscore">
              <div className="subscore-label">首年保证现金变化</div>
              <div className="subscore-value">
                {money(negotiation.first_year_guaranteed.absolute, offer.currency)}
              </div>
            </div>
            <div className="subscore">
              <div className="subscore-label">新增签字费</div>
              <div className="subscore-value">
                {money(negotiation.signing_bonus_gained, offer.currency)}
              </div>
            </div>
          </div>
          <p className="field-hint mt-1">
            {negotiation.has_negotiation_sequence
              ? '记录中存在「我方诉求 → 公司调整」的完整过程。'
              : '记录里没有完整的「我方诉求 → 公司调整」过程，因此这只是条件的变化，' +
                '不能说明是谈判带来的。'}
          </p>
        </Card>
      ) : null}

      {offer.notes ? (
        <Card title="备注">
          <p className="mt-0">{offer.notes}</p>
        </Card>
      ) : null}

      {countering ? (
        <CounterDialog
          offer={offer}
          busy={busy}
          onClose={() => setCountering(false)}
          onSubmit={(payload: CounterPayload) => {
            void run(() => api.recordCounter(offer.id, payload)).then((ok) => {
              if (ok) setCountering(false)
            })
          }}
        />
      ) : null}

      {revising ? (
        <CompanyRevisionDialog
          offer={offer}
          busy={busy}
          onClose={() => setRevising(false)}
          onSubmit={(payload: RevisionCreatePayload) => {
            void run(() => api.addOfferRevision(offer.id, payload)).then((ok) => {
              if (ok) setRevising(false)
            })
          }}
        />
      ) : null}

      {accepting ? (
        <AcceptDialog
          offer={offer}
          busy={busy}
          onClose={() => setAccepting(false)}
          onSubmit={(notes) => {
            void run(() => api.acceptOffer(offer.id, null, notes)).then((ok) => {
              if (ok) setAccepting(false)
            })
          }}
        />
      ) : null}

      {declining ? (
        <DeclineDialog
          offer={offer}
          busy={busy}
          onClose={() => setDeclining(false)}
          onSubmit={(reason: DeclineReason, notes) => {
            void run(() => api.declineOffer(offer.id, reason, notes)).then((ok) => {
              if (ok) setDeclining(false)
            })
          }}
        />
      ) : null}
    </>
  )
}
