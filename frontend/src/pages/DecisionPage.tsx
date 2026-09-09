// Offer decision support (v1.0)
//
// The v0.9 comparison page shows the facts side by side and refuses to rank
// them. This one will rank them - but only against weights the user typed, only
// when enough of the inputs actually have data behind them, and only with the
// whole calculation on screen. It never decides anything.
//
// Zero OpenAI calls.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import {
  DealBreakerChips,
  DealBreakerEditor,
  DIMENSIONS,
  RatingEditor,
  SaveSnapshotDialog,
  ScoreBreakdown,
  WeightEditor,
  pct,
  score100,
} from '@/components/decision'
import { CURRENCIES, DeadlineChip, money } from '@/components/offer'
import { Alert, Card, ChipList, EmptyState, Loading, formatDateTime } from '@/components/ui'
import type {
  AssessmentOut,
  DealBreakerItem,
  DecisionComparisonOut,
  DecisionDimension,
  DecisionProfileOut,
  OfferCurrency,
  OfferScoreOut,
  SnapshotSummary,
} from '@/types'

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

/** Local edit buffer for one offer's targets, kept as text so blanks stay blank. */
type TargetState = { target: string; ideal: string; minimum: string; notes: string }

function targetsOf(assessment: AssessmentOut | undefined): TargetState {
  return {
    target: assessment?.target_total_cash?.toString() ?? '',
    ideal: assessment?.ideal_total_cash?.toString() ?? '',
    minimum: assessment?.minimum_total_cash?.toString() ?? '',
    notes: assessment?.notes ?? '',
  }
}

function numberOrNull(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

export default function DecisionPage() {
  const [params] = useSearchParams()
  const ids = useMemo(
    () => params.getAll('id').map(Number).filter(Number.isFinite),
    [params],
  )

  const [profile, setProfile] = useState<DecisionProfileOut | null>(null)
  const [result, setResult] = useState<DecisionComparisonOut | null>(null)
  const [assessments, setAssessments] = useState<Record<number, AssessmentOut>>({})
  const [snapshots, setSnapshots] = useState<SnapshotSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [saving, setSaving] = useState(false)
  const [openOffer, setOpenOffer] = useState<number | null>(null)
  const [targets, setTargets] = useState<Record<number, TargetState>>({})

  // Local, unsaved edits to the profile. Nothing is written until 保存偏好.
  const [weights, setWeights] = useState<Partial<Record<DecisionDimension, number>>>({})
  const [breakers, setBreakers] = useState<DealBreakerItem[]>([])
  const [rates, setRates] = useState<Record<string, string>>({})
  const [baseCurrency, setBaseCurrency] = useState<OfferCurrency>('CNY')

  const adopt = useCallback((next: DecisionProfileOut) => {
    setProfile(next)
    setWeights(next.weights)
    setBreakers(next.deal_breakers)
    setBaseCurrency(next.base_currency)
    setRates(
      Object.fromEntries(
        Object.entries(next.fx_rates).map(([code, rate]) => [code, String(rate)]),
      ),
    )
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [loadedProfile, loadedSnapshots] = await Promise.all([
        api.decisionProfile(),
        api.decisionSnapshots(),
      ])
      adopt(loadedProfile)
      setSnapshots(loadedSnapshots.items)

      if (ids.length >= 2) {
        const [comparison, ...rows] = await Promise.all([
          api.decisionCompare(ids),
          ...ids.map((id) => api.offerAssessment(id)),
        ])
        setResult(comparison)
        const byOffer = Object.fromEntries(rows.map((row) => [row.offer_id, row]))
        setAssessments(byOffer)
        setTargets(
          Object.fromEntries(ids.map((id) => [id, targetsOf(byOffer[id])])) as Record<
            number,
            TargetState
          >,
        )
      }
    } catch (err) {
      setFeedback({
        tone: 'error',
        text: err instanceof ApiError ? err.message : '加载决策数据失败',
      })
    } finally {
      setLoading(false)
    }
  }, [adopt, ids])

  useEffect(() => {
    void load()
  }, [load])

  async function refreshComparison() {
    if (ids.length < 2) return
    try {
      setResult(await api.decisionCompare(ids))
    } catch (err) {
      setFeedback({
        tone: 'error',
        text: err instanceof ApiError ? err.message : '重新计算失败',
      })
    }
  }

  async function saveProfile() {
    setBusy(true)
    try {
      const fx: Record<string, number> = {}
      for (const [code, value] of Object.entries(rates)) {
        const parsed = numberOrNull(value)
        if (parsed !== null) fx[code] = parsed
      }
      adopt(
        await api.updateDecisionProfile({
          weights,
          deal_breakers: breakers,
          fx_rates: fx,
          base_currency: baseCurrency,
        }),
      )
      await refreshComparison()
      setFeedback({ tone: 'success', text: '偏好已保存，评分已重新计算。' })
    } catch (err) {
      setFeedback({
        tone: 'error',
        text: err instanceof ApiError ? err.message : '保存偏好失败',
      })
    } finally {
      setBusy(false)
    }
  }

  async function saveAssessment(offerId: number, ratings: AssessmentOut['ratings']) {
    setBusy(true)
    try {
      const state = targets[offerId] ?? { target: '', ideal: '', minimum: '', notes: '' }
      const saved = await api.saveOfferAssessment(offerId, {
        ratings,
        notes: state.notes || null,
        target_total_cash: numberOrNull(state.target),
        ideal_total_cash: numberOrNull(state.ideal),
        minimum_total_cash: numberOrNull(state.minimum),
        clear_targets:
          !state.target.trim() && !state.ideal.trim() && !state.minimum.trim(),
      })
      setAssessments((prev) => ({ ...prev, [offerId]: saved }))
      await refreshComparison()
      setFeedback({ tone: 'success', text: '你的评分已保存。' })
    } catch (err) {
      setFeedback({
        tone: 'error',
        text: err instanceof ApiError ? err.message : '保存评分失败',
      })
    } finally {
      setBusy(false)
    }
  }

  async function saveSnapshot(name: string, notes: string) {
    setBusy(true)
    try {
      await api.createDecisionSnapshot(ids, name, notes)
      setSnapshots((await api.decisionSnapshots()).items)
      setSaving(false)
      setFeedback({ tone: 'success', text: '决策记录已冻结保存。' })
    } catch (err) {
      setFeedback({
        tone: 'error',
        text: err instanceof ApiError ? err.message : '保存决策记录失败',
      })
    } finally {
      setBusy(false)
    }
  }

  async function removeSnapshot(snapshotId: number) {
    setBusy(true)
    try {
      await api.deleteDecisionSnapshot(snapshotId)
      setSnapshots((prev) => prev.filter((item) => item.id !== snapshotId))
    } catch (err) {
      setFeedback({
        tone: 'error',
        text: err instanceof ApiError ? err.message : '删除失败',
      })
    } finally {
      setBusy(false)
    }
  }

  const foreignCurrencies = useMemo(() => {
    const set = new Set<string>()
    for (const offer of result?.offers ?? []) {
      if (offer.currency !== baseCurrency) set.add(offer.currency)
    }
    return [...set].sort()
  }, [result, baseCurrency])

  const winner = result?.offers.find((o) => o.offer_id === result.winner_offer_id) ?? null
  const defaultName = result?.offers.map((o) => o.company).join(' vs ') ?? '决策记录'

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Offer 决策分析</h1>
          <p>
            按<strong>你自己的权重</strong>计算，全部算式都摆在页面上。
            系统不会替你决定，也不会替任何一家公司打分 —— 主观评分只有你能填。
            本页<strong>不调用 AI</strong>。
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to={`/offers/compare?${ids.map((id) => `id=${id}`).join('&')}`}>
            条件对照表
          </Link>
          <Link className="btn" to="/offers">
            返回 Offer
          </Link>
        </div>
      </div>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      {loading ? <Loading text="正在计算…" /> : null}

      {!loading && ids.length < 2 ? (
        <EmptyState
          icon="⚖️"
          title="请选择至少两个 Offer"
          text="在 Offer 列表里勾选 2–4 个，然后点「决策分析」。"
        />
      ) : null}

      {!loading ? (
        <Card title="① 你在意什么" sub="权重只反映比例，计算时会自动归一化">
          <WeightEditor weights={weights} onChange={setWeights} disabled={busy} />

          <h3>硬性条件</h3>
          <DealBreakerEditor items={breakers} onChange={setBreakers} disabled={busy} />

          <h3>币种与汇率</h3>
          <div className="field" style={{ maxWidth: 220 }}>
            <label htmlFor="base-currency">比较基准币种</label>
            <select
              id="base-currency"
              value={baseCurrency}
              disabled={busy}
              onChange={(event) => setBaseCurrency(event.target.value as OfferCurrency)}
            >
              {CURRENCIES.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </div>

          {foreignCurrencies.length ? (
            <>
              <p className="field-hint">
                填写 1 单位外币等于多少 {baseCurrency}。汇率<strong>由你填写</strong> ——
                系统不联网、不查任何行情。不填就不换算，薪酬维度会标为不可比较。
              </p>
              <div className="field-row">
                {foreignCurrencies.map((code) => (
                  <div key={code} className="field">
                    <label htmlFor={`rate-${code}`}>
                      1 {code} = ? {baseCurrency}
                    </label>
                    <input
                      id={`rate-${code}`}
                      type="number"
                      step="0.0001"
                      min="0"
                      value={rates[code] ?? ''}
                      disabled={busy}
                      onChange={(event) =>
                        setRates((prev) => ({ ...prev, [code]: event.target.value }))
                      }
                    />
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="field-hint">所选 Offer 都是同一币种，无需汇率。</p>
          )}

          <div className="btn-row mt-1">
            <button
              type="button"
              className="btn-primary"
              disabled={busy}
              onClick={() => void saveProfile()}
            >
              保存偏好并重新计算
            </button>
            {profile ? (
              <span className="faint small">
                上次更新：{formatDateTime(profile.updated_at)}
              </span>
            ) : null}
          </div>
        </Card>
      ) : null}

      {result ? (
        <>
          <Card title="② 你怎么看这几个 Offer" sub="主观评分，只有人能填">
            {result.offers.map((offer) => (
              <details
                className="details-panel"
                key={offer.offer_id}
                open={openOffer === offer.offer_id}
                onToggle={(event) =>
                  setOpenOffer(
                    (event.target as HTMLDetailsElement).open ? offer.offer_id : null,
                  )
                }
              >
                <summary>
                  <span className="cell-title">{offer.company}</span>
                  <span className="cell-sub"> · {offer.title}</span>
                  <span className="faint small">
                    {' '}
                    已评 {Object.keys(assessments[offer.offer_id]?.ratings ?? {}).length} /{' '}
                    {DIMENSIONS.length}
                  </span>
                </summary>

                <div className="mt-1">
                  <RatingEditor
                    ratings={assessments[offer.offer_id]?.ratings ?? {}}
                    disabled={busy}
                    onChange={(next) =>
                      setAssessments((prev) => ({
                        ...prev,
                        [offer.offer_id]: {
                          ...(prev[offer.offer_id] ?? {
                            offer_id: offer.offer_id,
                            notes: null,
                            target_total_cash: null,
                            ideal_total_cash: null,
                            minimum_total_cash: null,
                            updated_at: null,
                          }),
                          ratings: Object.fromEntries(
                            Object.entries(next).filter(([, v]) => v !== null),
                          ) as AssessmentOut['ratings'],
                        },
                      }))
                    }
                  />

                  <h4>谈薪目标（{offer.currency}）</h4>
                  <p className="field-hint mt-0">
                    低于最低线只会<strong>提示</strong>，不会自动拒绝任何 Offer。
                  </p>
                  <div className="field-row">
                    {(
                      [
                        ['ideal', '理想线'],
                        ['target', '目标线'],
                        ['minimum', '最低接受线'],
                      ] as const
                    ).map(([key, label]) => (
                      <div key={key} className="field">
                        <label htmlFor={`${key}-${offer.offer_id}`}>{label}</label>
                        <input
                          id={`${key}-${offer.offer_id}`}
                          type="number"
                          min="0"
                          value={targets[offer.offer_id]?.[key] ?? ''}
                          disabled={busy}
                          onChange={(event) =>
                            setTargets((prev) => ({
                              ...prev,
                              [offer.offer_id]: {
                                ...(prev[offer.offer_id] ?? targetsOf(undefined)),
                                [key]: event.target.value,
                              },
                            }))
                          }
                        />
                      </div>
                    ))}
                  </div>

                  <div className="btn-row mt-1">
                    <button
                      type="button"
                      className="btn-primary btn-sm"
                      disabled={busy}
                      onClick={() =>
                        void saveAssessment(
                          offer.offer_id,
                          assessments[offer.offer_id]?.ratings ?? {},
                        )
                      }
                    >
                      保存我的评分
                    </button>
                    <Link className="btn btn-sm" to={`/offers/${offer.offer_id}`}>
                      查看 Offer 详情
                    </Link>
                  </div>
                </div>
              </details>
            ))}
          </Card>

          <Card
            title="③ 计算结果"
            sub={result.message}
            actions={
              <button
                type="button"
                className="btn-primary btn-sm"
                disabled={busy}
                onClick={() => setSaving(true)}
              >
                保存决策记录
              </button>
            }
          >
            {result.mixed_currency ? (
              <Alert tone={Object.keys(result.fx_rates_used).length ? 'info' : 'warn'}>
                所选 Offer 涉及多种币种。
                {Object.keys(result.fx_rates_used).length
                  ? '已按你填写的汇率换算 —— 结论完全取决于该汇率是否合适。'
                  : '未填写汇率，薪酬维度无法比较。系统不会自行使用任何汇率。'}
              </Alert>
            ) : null}

            {result.notes.map((note) => (
              <Alert key={note} tone="info">
                {note}
              </Alert>
            ))}

            {winner ? (
              <Alert tone="success">
                按你当前的权重，<strong>{winner.company}</strong> 得分最高（
                {score100(winner.total_score)} 分，覆盖率 {pct(winner.coverage)}）。
                这只是把你自己的偏好算了一遍 —— 最终决定仍然由你做出。
              </Alert>
            ) : (
              <Alert tone="warn">{result.winner_blocked_reason || result.message}</Alert>
            )}

            <div className="grid grid-2">
              {result.offers.map((offer) => (
                <OfferScoreCard
                  key={offer.offer_id}
                  offer={offer}
                  minCoverage={result.min_coverage}
                  baseCurrency={result.base_currency}
                  isWinner={offer.offer_id === result.winner_offer_id}
                />
              ))}
            </div>
          </Card>
        </>
      ) : null}

      {snapshots.length ? (
        <Card title="历史决策记录" sub="每条记录都被冻结，之后修改任何数据都不会改变它">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>名称</th>
                  <th>Offer</th>
                  <th>当时结论</th>
                  <th>时间</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {snapshots.map((snapshot) => (
                  <tr key={snapshot.id}>
                    <td>
                      <div className="cell-title">{snapshot.name}</div>
                      {snapshot.notes ? (
                        <div className="cell-sub">{snapshot.notes}</div>
                      ) : null}
                    </td>
                    <td>{snapshot.companies.join('、')}</td>
                    <td>
                      {snapshot.winner_offer_id === null
                        ? '当时未给出结论'
                        : snapshot.companies[
                            snapshot.offer_ids.indexOf(snapshot.winner_offer_id)
                          ] ?? '—'}
                    </td>
                    <td className="nowrap">{formatDateTime(snapshot.created_at)}</td>
                    <td className="nowrap">
                      <Link
                        className="btn btn-sm"
                        to={`/offers/decision?${snapshot.offer_ids
                          .map((id) => `id=${id}`)
                          .join('&')}`}
                      >
                        重新比较
                      </Link>
                      <button
                        type="button"
                        className="btn-ghost btn-sm"
                        disabled={busy}
                        onClick={() => void removeSnapshot(snapshot.id)}
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      {saving ? (
        <SaveSnapshotDialog
          defaultName={defaultName}
          busy={busy}
          onClose={() => setSaving(false)}
          onSubmit={(name, notes) => void saveSnapshot(name, notes)}
        />
      ) : null}
    </>
  )
}

function OfferScoreCard({
  offer,
  minCoverage,
  baseCurrency,
  isWinner,
}: {
  offer: OfferScoreOut
  minCoverage: number
  baseCurrency: OfferCurrency
  isWinner: boolean
}) {
  return (
    <section className="job-card">
      <div className="job-card-head">
        <div>
          <Link className="cell-title" to={`/offers/${offer.offer_id}`}>
            {offer.company}
          </Link>
          <div className="cell-sub">{offer.title}</div>
        </div>
        <div>
          {isWinner ? <span className="chip chip-good">当前权重下得分最高</span> : null}
          <DeadlineChip state={offer.deadline_state} />
        </div>
      </div>

      <div className="meta-list">
        <span>
          首年保证现金：{money(offer.guaranteed_cash, offer.currency)}
          {offer.fx_rate_used
            ? `（按 1 ${offer.currency} = ${offer.fx_rate_used} ${baseCurrency} 换算）`
            : ''}
        </span>
        <span>首年目标现金：{money(offer.target_cash, offer.currency)}</span>
        {offer.revision_id ? (
          <span className="faint small">数据来自薪酬版本 #{offer.revision_id}</span>
        ) : null}
      </div>

      {!offer.compensation_comparable ? (
        <Alert tone="warn">缺少汇率，该 Offer 的薪酬无法与其他币种比较。</Alert>
      ) : null}

      <ScoreBreakdown score={offer} minCoverage={minCoverage} />

      <h4>硬性条件</h4>
      <DealBreakerChips checks={offer.deal_breakers} />

      <div className="grid grid-2 mt-1">
        <div>
          <div className="subscore-label">相对强项</div>
          <ChipList items={offer.strengths} tone="good" />
        </div>
        <div>
          <div className="subscore-label">需要取舍</div>
          <ChipList items={offer.trade_offs} tone="bad" />
        </div>
      </div>

      {offer.warnings.length ? (
        <ul className="bullet-list mt-1">
          {offer.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}

      {offer.days_to_deadline !== null ? (
        <p className="field-hint mt-1">
          距离截止 {offer.days_to_deadline} 天。
          <strong>截止时间不会影响评分</strong> —— 紧急不等于更好。
        </p>
      ) : null}
    </section>
  )
}
