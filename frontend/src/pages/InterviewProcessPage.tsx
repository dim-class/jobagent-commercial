// One interview process, end to end (v0.8).
//
// The resume shown is the one recorded at application time for THIS cycle -
// switching the active analysis resume never changes it.

import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import {
  AddRoundDialog,
  CompleteRoundDialog,
  FAILURE_REASONS,
  FEEDBACK_TAGS,
  OUTCOME_LABEL,
  PROCESS_STATUS_LABEL,
  WITHDRAW_REASONS,
  WithdrawDialog,
} from '@/components/interview'
import { Alert, Card, Loading, formatDateTime } from '@/components/ui'
import type {
  InterviewProcessOut,
  InterviewRoundOut,
  RoundCompletePayload,
  RoundCreatePayload,
  WithdrawReason,
} from '@/types'

type Tone = 'success' | 'error' | 'info' | 'warn'
type Feedback = { tone: Tone; text: string } | null

function label(list: { value: string; label: string }[], value: string | null): string {
  if (!value) return '—'
  return list.find((item) => item.value === value)?.label ?? value
}

function RoundDetail({
  round,
  onComplete,
  onCancel,
  busy,
}: {
  round: InterviewRoundOut
  onComplete: (round: InterviewRoundOut) => void
  onCancel: (round: InterviewRoundOut) => void
  busy: boolean
}) {
  const state =
    round.status === 'completed'
      ? OUTCOME_LABEL[round.outcome]
      : round.status === 'cancelled'
        ? '已取消'
        : round.scheduled_at
          ? '已安排'
          : '待安排'

  return (
    <section className="job-card">
      <div className="job-card-head">
        <div>
          <div className="cell-title">
            {round.round_index}. {round.display_name}
          </div>
          <div className="cell-sub">
            {round.scheduled_at ? formatDateTime(round.scheduled_at) : '时间待定'}
            {round.duration_minutes ? ` · ${round.duration_minutes} 分钟` : ''}
          </div>
        </div>
        <span
          className={
            round.outcome === 'passed'
              ? 'chip chip-good'
              : round.outcome === 'failed'
                ? 'chip chip-bad'
                : 'chip'
          }
        >
          {state}
        </span>
      </div>

      <dl className="meta-list">
        {round.interviewer_name ? (
          <span>
            面试官：{round.interviewer_name}
            {round.interviewer_role ? `（${round.interviewer_role}）` : ''}
          </span>
        ) : null}
        {round.failure_reason ? (
          <span>未通过原因：{label(FAILURE_REASONS, round.failure_reason)}</span>
        ) : null}
        {round.completed_at ? <span>记录于 {formatDateTime(round.completed_at)}</span> : null}
      </dl>

      {round.feedback_tags.length ? (
        <div className="chip-list">
          {round.feedback_tags.map((tag) => (
            <span key={tag} className="chip">
              {label(FEEDBACK_TAGS, tag)}
            </span>
          ))}
        </div>
      ) : null}

      {round.feedback_text ? (
        <div className="field mt-1">
          <strong className="small">面试官/HR 的反馈</strong>
          <p className="mt-0">{round.feedback_text}</p>
        </div>
      ) : null}

      {round.notes ? (
        <div className="field">
          <strong className="small">我自己的记录</strong>
          <p className="mt-0">{round.notes}</p>
        </div>
      ) : null}

      <div className="btn-row">
        {round.meeting_url && round.status !== 'completed' ? (
          <a
            className="btn btn-sm"
            href={round.meeting_url}
            target="_blank"
            rel="noreferrer noopener"
          >
            会议链接
          </a>
        ) : null}
        {round.status !== 'cancelled' ? (
          <button
            type="button"
            className="btn-sm"
            disabled={busy}
            onClick={() => onComplete(round)}
          >
            {round.status === 'completed' ? '更正结果' : '记录结果'}
          </button>
        ) : null}
        {round.status !== 'completed' && round.status !== 'cancelled' ? (
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={busy}
            onClick={() => onCancel(round)}
          >
            取消该轮
          </button>
        ) : null}
      </div>
    </section>
  )
}

export default function InterviewProcessPage() {
  const params = useParams()
  const processId = Number(params.processId)

  const [process, setProcess] = useState<InterviewProcessOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(false)
  const [completing, setCompleting] = useState<InterviewRoundOut | null>(null)
  const [withdrawing, setWithdrawing] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setProcess(await api.getInterviewProcess(processId))
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载失败' })
    } finally {
      setLoading(false)
    }
  }, [processId])

  useEffect(() => {
    if (Number.isFinite(processId)) void load()
  }, [processId, load])

  async function run(
    action: () => Promise<{ message: string }>,
    tone: Tone = 'success',
  ) {
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

  async function submitAdd(payload: RoundCreatePayload) {
    if (await run(() => api.addInterviewRound(processId, payload))) setAdding(false)
  }

  async function submitComplete(payload: RoundCompletePayload) {
    if (!completing) return
    if (await run(() => api.completeInterviewRound(completing.id, payload))) {
      setCompleting(null)
    }
  }

  async function submitWithdraw(reason: WithdrawReason, notes: string) {
    if (await run(() => api.withdrawInterviewProcess(processId, reason, notes), 'info')) {
      setWithdrawing(false)
    }
  }

  if (loading && !process) return <Loading text="正在加载面试流程…" />
  if (!process) return feedback ? <Alert tone="error">{feedback.text}</Alert> : null

  const closed = process.status !== 'ongoing'

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{process.company}</h1>
          <p>
            {process.title}
            {process.city ? ` · ${process.city}` : ''}
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to={`/jobs/${process.job_id}`}>
            查看岗位
          </Link>
          <Link className="btn" to="/interviews">
            返回面试列表
          </Link>
        </div>
      </div>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      <Card
        title="流程概览"
        actions={
          <span className={process.status === 'ongoing' ? 'chip chip-good' : 'chip'}>
            {PROCESS_STATUS_LABEL[process.status]}
          </span>
        }
      >
        <dl className="meta-list">
          <span>投递时间：{process.applied_at ? formatDateTime(process.applied_at) : '—'}</span>
          <span>
            本次投递简历：{process.resume_label ?? '未记录'}
            {process.resume_archived ? '（已归档）' : ''}
          </span>
          <span>已通过 {process.rounds_passed} 轮</span>
          {process.ended_after_round_type ? (
            <span>结束于：{process.ended_after_round_type}</span>
          ) : null}
          {process.withdraw_reason ? (
            <span>终止原因：{label(WITHDRAW_REASONS, process.withdraw_reason)}</span>
          ) : null}
          {process.failure_reason ? (
            <span>未通过原因：{label(FAILURE_REASONS, process.failure_reason)}</span>
          ) : null}
        </dl>
        <p className="field-hint">
          简历归属以投递当时的记录为准，切换「当前AI分析简历」不会改变这里。
        </p>
      </Card>

      <Card
        title="面试流程"
        sub="投递 → 各轮面试 → 结果。只显示真实存在的轮次"
        actions={
          !closed ? (
            <button
              type="button"
              className="btn-primary btn-sm"
              disabled={busy}
              onClick={() => setAdding(true)}
            >
              添加下一轮
            </button>
          ) : null
        }
      >
        {!process.rounds.length ? (
          <p className="faint small mt-0">还没有添加任何轮次。</p>
        ) : (
          <div className="grid grid-2">
            {process.rounds.map((round) => (
              <RoundDetail
                key={round.id}
                round={round}
                busy={busy}
                onComplete={setCompleting}
                onCancel={(target) =>
                  void run(() => api.cancelInterviewRound(target.id), 'info')
                }
              />
            ))}
          </div>
        )}

        {!closed ? (
          <div className="btn-row mt-2">
            <button
              type="button"
              className="btn-ghost btn-sm"
              disabled={busy}
              onClick={() => setWithdrawing(true)}
            >
              主动终止流程
            </button>
          </div>
        ) : null}
      </Card>

      {process.notes ? (
        <Card title="流程备注">
          <p className="mt-0">{process.notes}</p>
        </Card>
      ) : null}

      {adding ? (
        <AddRoundDialog
          busy={busy}
          onClose={() => setAdding(false)}
          onSubmit={(payload) => void submitAdd(payload)}
        />
      ) : null}

      {completing ? (
        <CompleteRoundDialog
          round={completing}
          busy={busy}
          onClose={() => setCompleting(null)}
          onSubmit={(payload) => void submitComplete(payload)}
        />
      ) : null}

      {withdrawing ? (
        <WithdrawDialog
          process={process}
          busy={busy}
          onClose={() => setWithdrawing(false)}
          onSubmit={(reason, notes) => void submitWithdraw(reason, notes)}
        />
      ) : null}
    </>
  )
}
