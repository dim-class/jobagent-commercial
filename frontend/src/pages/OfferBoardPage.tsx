// Offer (v0.9)
//
// A local record of offers you received. JobAgent does not negotiate, does not
// reply to recruiters, and does not accept or decline anything on your behalf.
//
// Deadlines are bucketed server-side in Asia/Tokyo so "今天截止" means the same
// thing here and in every statistic.

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import {
  AcceptDialog,
  CounterDialog,
  DeadlineChip,
  DeclineDialog,
  OFFER_STATUS_TONE,
  money,
} from '@/components/offer'
import { Alert, Card, EmptyState, Loading, Stat, formatDateTime } from '@/components/ui'
import type { CounterPayload, DeclineReason, OfferBoardResponse, OfferOut } from '@/types'

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

function OfferCard({
  offer,
  selected,
  onToggle,
  onCounter,
  onAccept,
  onDecline,
  busy,
}: {
  offer: OfferOut
  selected?: boolean
  onToggle?: (offer: OfferOut) => void
  onCounter?: (offer: OfferOut) => void
  onAccept?: (offer: OfferOut) => void
  onDecline?: (offer: OfferOut) => void
  busy?: boolean
}) {
  // The company's offer - never the latest revision, which may be your counter.
  const summary = offer.accepted_summary ?? offer.current_company_summary

  return (
    <section className="job-card">
      <div className="job-card-head">
        <div>
          <Link className="cell-title" to={`/offers/${offer.id}`}>
            {offer.company}
          </Link>
          <div className="cell-sub">
            {offer.title}
            {offer.city ? ` · ${offer.city}` : ''}
          </div>
        </div>
        <div>
          <span className={OFFER_STATUS_TONE[offer.status]}>{offer.status_label}</span>
          <DeadlineChip state={offer.deadline_state} />
        </div>
      </div>

      <div className="meta-list">
        <span>本次投递简历：{offer.resume_label ?? '未记录'}</span>
        {offer.received_at ? <span>收到 {formatDateTime(offer.received_at)}</span> : null}
        {offer.decision_deadline ? (
          <span>截止 {formatDateTime(offer.decision_deadline)}</span>
        ) : null}
      </div>

      <div className="grid grid-2 mt-1">
        <div className="subscore">
          <div className="subscore-label">基础年薪</div>
          <div className="subscore-value">
            {money(summary?.base_annual ?? null, offer.currency)}
          </div>
        </div>
        <div className="subscore">
          <div className="subscore-label">首年保证现金</div>
          <div className="subscore-value">
            {money(summary?.first_year_guaranteed_cash ?? null, offer.currency)}
          </div>
        </div>
        <div className="subscore">
          <div className="subscore-label">首年目标现金</div>
          <div className="subscore-value">
            {money(summary?.first_year_target_cash ?? null, offer.currency)}
          </div>
        </div>
        <div className="subscore">
          <div className="subscore-label">估算总包</div>
          <div className="subscore-value">
            {money(summary?.estimated_first_year_total_comp ?? null, offer.currency)}
          </div>
          {summary?.equity_excluded ? (
            <div className="subscore-label">股权未计入</div>
          ) : null}
        </div>
      </div>

      {offer.latest_counter_summary ? (
        <p className="field-hint mt-1">
          我方最近诉求：{money(offer.latest_counter_summary.base_annual, offer.currency)}
          （这是你的要求，不是公司的 Offer）
        </p>
      ) : null}

      <div className="btn-row mt-1">
        {onToggle ? (
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={Boolean(selected)}
              onChange={() => onToggle(offer)}
            />
            <span className="small">比较</span>
          </label>
        ) : null}
        <Link className="btn btn-sm" to={`/offers/${offer.id}`}>
          查看 Offer
        </Link>
        {onCounter ? (
          <button
            type="button"
            className="btn-sm"
            disabled={busy}
            onClick={() => onCounter(offer)}
          >
            添加谈薪记录
          </button>
        ) : null}
        {onAccept ? (
          <button
            type="button"
            className="btn-primary btn-sm"
            disabled={busy}
            onClick={() => onAccept(offer)}
          >
            接受
          </button>
        ) : null}
        {onDecline ? (
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={busy}
            onClick={() => onDecline(offer)}
          >
            拒绝
          </button>
        ) : null}
      </div>
    </section>
  )
}

export default function OfferBoardPage() {
  const [board, setBoard] = useState<OfferBoardResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState<number[]>([])
  const [countering, setCountering] = useState<OfferOut | null>(null)
  const [accepting, setAccepting] = useState<OfferOut | null>(null)
  const [declining, setDeclining] = useState<OfferOut | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setBoard(await api.offerBoard())
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载失败' })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function run(action: () => Promise<{ message: string }>) {
    setBusy(true)
    try {
      const result = await action()
      setFeedback({ tone: 'success', text: result.message })
      await load()
      return true
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '操作失败' })
      return false
    } finally {
      setBusy(false)
    }
  }

  function toggle(offer: OfferOut) {
    setSelected((prev) =>
      prev.includes(offer.id) ? prev.filter((id) => id !== offer.id) : [...prev, offer.id],
    )
  }

  const active = board ? [...board.pending, ...board.negotiating] : []
  const nothingYet =
    board &&
    !board.pending.length &&
    !board.negotiating.length &&
    !board.accepted.length &&
    !board.closed.length

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Offer</h1>
          <p>
            你自己记录的 Offer 与谈薪过程。JobAgent 不会替你谈判、答复或接受 —
            所有决定都由你做出。截止时间按 {board?.timezone ?? 'Asia/Tokyo'} 计算。
          </p>
        </div>
        <div className="page-actions">
          {selected.length >= 2 ? (
            <>
              <Link
                className="btn btn-primary"
                to={`/offers/decision?${selected.map((id) => `id=${id}`).join('&')}`}
              >
                决策分析（{selected.length}）
              </Link>
              <Link
                className="btn"
                to={`/offers/compare?${selected.map((id) => `id=${id}`).join('&')}`}
              >
                条件对照
              </Link>
            </>
          ) : null}
          <Link className="btn" to="/analytics-offers">
            Offer 分析
          </Link>
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

      {loading && !board ? <Loading text="正在加载 Offer…" /> : null}

      {nothingYet ? (
        <EmptyState
          icon="📨"
          title="还没有记录任何 Offer"
          text="收到 Offer 后，在岗位详情页点「记录 Offer」，填入薪酬与截止时间，这里就会出现。"
        />
      ) : null}

      {board && !nothingYet ? (
        <>
          <div className="grid grid-stats">
            <Stat label="待决定" value={board.pending.length} />
            <Stat label="谈判中" value={board.negotiating.length} />
            <Stat label="已接受" value={board.accepted.length} />
            <Stat label="已结束" value={board.closed.length} />
          </div>

          {active.length >= 2 ? (
            <Alert tone="info">
              勾选 2–4 个 Offer 后可以并列比较。「条件对照」按原币种并列显示，不做任何换算；
              「决策分析」按<strong>你自己设定的权重</strong>计算评分，并把每一步算式都展示出来。
            </Alert>
          ) : null}

          {board.pending.length ? (
            <Card title="待决定" sub="按截止时间排序，最近的在前">
              <div className="grid grid-2">
                {board.pending.map((offer) => (
                  <OfferCard
                    key={offer.id}
                    offer={offer}
                    selected={selected.includes(offer.id)}
                    onToggle={toggle}
                    onCounter={setCountering}
                    onAccept={setAccepting}
                    onDecline={setDeclining}
                    busy={busy}
                  />
                ))}
              </div>
            </Card>
          ) : null}

          {board.negotiating.length ? (
            <Card title="谈判中">
              <div className="grid grid-2">
                {board.negotiating.map((offer) => (
                  <OfferCard
                    key={offer.id}
                    offer={offer}
                    selected={selected.includes(offer.id)}
                    onToggle={toggle}
                    onCounter={setCountering}
                    onAccept={setAccepting}
                    onDecline={setDeclining}
                    busy={busy}
                  />
                ))}
              </div>
            </Card>
          ) : null}

          {board.accepted.length ? (
            <Card title="已接受" sub="接受时的薪酬已固定保存">
              <div className="grid grid-2">
                {board.accepted.map((offer) => (
                  <OfferCard key={offer.id} offer={offer} />
                ))}
              </div>
            </Card>
          ) : null}

          {board.closed.length ? (
            <Card title="已拒绝 / 结束">
              <div className="grid grid-2">
                {board.closed.map((offer) => (
                  <OfferCard key={offer.id} offer={offer} />
                ))}
              </div>
            </Card>
          ) : null}
        </>
      ) : null}

      {board && board.legacy_offer_events.length ? (
        <Card
          title="旧版 Offer 记录"
          sub="v0.9 之前记录的 Offer 没有薪酬明细，系统不会替你编造数字"
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>公司 / 岗位</th>
                  <th>时间</th>
                  <th>原记录</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {board.legacy_offer_events.map((item) => (
                  <tr key={item.event_id}>
                    <td>
                      <div className="cell-title">{item.company}</div>
                      <div className="cell-sub">{item.title}</div>
                    </td>
                    <td className="nowrap">{formatDateTime(item.occurred_at)}</td>
                    <td>{item.legacy_salary_text ?? '未记录金额'}</td>
                    <td className="nowrap">
                      <Link className="btn btn-sm" to={`/jobs/${item.job_id}`}>
                        补充明细
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="field-hint mt-1">这些记录不计入任何薪酬统计。</p>
        </Card>
      ) : null}

      {countering ? (
        <CounterDialog
          offer={countering}
          busy={busy}
          onClose={() => setCountering(null)}
          onSubmit={(payload: CounterPayload) => {
            void run(() => api.recordCounter(countering.id, payload)).then((ok) => {
              if (ok) setCountering(null)
            })
          }}
        />
      ) : null}

      {accepting ? (
        <AcceptDialog
          offer={accepting}
          busy={busy}
          onClose={() => setAccepting(null)}
          onSubmit={(notes) => {
            void run(() => api.acceptOffer(accepting.id, null, notes)).then((ok) => {
              if (ok) setAccepting(null)
            })
          }}
        />
      ) : null}

      {declining ? (
        <DeclineDialog
          offer={declining}
          busy={busy}
          onClose={() => setDeclining(null)}
          onSubmit={(reason: DeclineReason, notes) => {
            void run(() => api.declineOffer(declining.id, reason, notes)).then((ok) => {
              if (ok) setDeclining(null)
            })
          }}
        />
      ) : null}
    </>
  )
}
