// 面试 (v0.8)
//
// A local view of interviews you recorded. JobAgent reads no calendar and no
// inbox, never accepts an interview, and never replies to a recruiter.
//
// Days are grouped server-side in Asia/Tokyo so "今天" means the same thing
// here and in every statistic.

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import {
  CompleteRoundDialog,
  PROCESS_STATUS_LABEL,
  RoundLine,
  WithdrawDialog,
} from '@/components/interview'
import { Alert, Card, EmptyState, Loading, Stat, formatDateTime } from '@/components/ui'
import type {
  InterviewBoardResponse,
  InterviewProcessOut,
  InterviewRoundOut,
  RoundCompletePayload,
  WithdrawReason,
} from '@/types'

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

function ProcessCard({
  process,
  onComplete,
  onWithdraw,
  busy,
}: {
  process: InterviewProcessOut
  onComplete?: (round: InterviewRoundOut) => void
  onWithdraw?: (process: InterviewProcessOut) => void
  busy?: boolean
}) {
  return (
    <section className="job-card">
      <div className="job-card-head">
        <div>
          <Link className="cell-title" to={`/jobs/${process.job_id}`}>
            {process.company}
          </Link>
          <div className="cell-sub">
            {process.title}
            {process.city ? ` · ${process.city}` : ''}
          </div>
        </div>
        <span className={process.status === 'ongoing' ? 'chip chip-good' : 'chip'}>
          {PROCESS_STATUS_LABEL[process.status]}
        </span>
      </div>

      <div className="meta-list">
        {process.applied_at ? <span>投递 {formatDateTime(process.applied_at)}</span> : null}
        <span>
          本次投递简历：{process.resume_label ?? '未记录'}
          {process.resume_archived ? '（已归档）' : ''}
        </span>
        <span>已通过 {process.rounds_passed} 轮</span>
      </div>

      {process.rounds.map((round) => (
        <RoundLine key={round.id} round={round} onComplete={onComplete} busy={busy} />
      ))}

      <div className="btn-row mt-1">
        <Link className="btn btn-sm" to={`/interviews/${process.id}`}>
          查看面试
        </Link>
        <Link className="btn btn-sm" to={`/jobs/${process.job_id}`}>
          查看岗位
        </Link>
        {onWithdraw && process.status === 'ongoing' ? (
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={busy}
            onClick={() => onWithdraw(process)}
          >
            主动终止
          </button>
        ) : null}
      </div>
    </section>
  )
}

export default function InterviewPipelinePage() {
  const [board, setBoard] = useState<InterviewBoardResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [busy, setBusy] = useState(false)
  const [completing, setCompleting] = useState<InterviewRoundOut | null>(null)
  const [withdrawing, setWithdrawing] = useState<InterviewProcessOut | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setBoard(await api.interviewBoard())
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载面试失败' })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function submitComplete(payload: RoundCompletePayload) {
    if (!completing) return
    setBusy(true)
    try {
      const result = await api.completeInterviewRound(completing.id, payload)
      setFeedback({ tone: 'success', text: result.message })
      setCompleting(null)
      await load()
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '记录失败' })
    } finally {
      setBusy(false)
    }
  }

  async function submitWithdraw(reason: WithdrawReason, notes: string) {
    if (!withdrawing) return
    setBusy(true)
    try {
      const result = await api.withdrawInterviewProcess(withdrawing.id, reason, notes)
      setFeedback({ tone: 'info', text: result.message })
      setWithdrawing(null)
      await load()
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '操作失败' })
    } finally {
      setBusy(false)
    }
  }

  const upcomingCount = (board?.upcoming ?? []).reduce((n, g) => n + g.items.length, 0)
  const nothingYet =
    board &&
    !upcomingCount &&
    !board.awaiting_result.length &&
    !board.completed.length &&
    !board.ongoing_without_schedule.length

  return (
    <>
      <div className="page-head">
        <div>
          <h1>面试</h1>
          <p>
            你自己记录的面试安排与结果。JobAgent 不读取日历、不读取邮箱，
            也不会替你接受面试或回复招聘方。日期按 {board?.timezone ?? 'Asia/Tokyo'} 计算。
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to="/analytics-interviews">
            面试分析
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

      {loading && !board ? <Loading text="正在加载面试…" /> : null}

      {nothingYet ? (
        <EmptyState
          icon="🗓️"
          title="还没有记录任何面试"
          text="在岗位详情页的「面试流程」里添加第一轮面试，这里就会显示接下来的安排。"
        />
      ) : null}

      {board && !nothingYet ? (
        <>
          <div className="grid grid-stats">
            <Stat label="即将进行" value={upcomingCount} hint="已安排时间的轮次" />
            <Stat label="结果待定" value={board.awaiting_result.length} hint="面试已过，等待结果" />
            <Stat
              label="待安排"
              value={board.ongoing_without_schedule.length}
              hint="流程进行中，还没有下一轮时间"
            />
            <Stat label="已结束" value={board.completed.length} />
          </div>

          {board.upcoming.map((group) => (
            <Card key={group.key} title={`即将进行 · ${group.label}`}>
              <div className="grid grid-2">
                {group.items.map((process) => (
                  <ProcessCard
                    key={process.id}
                    process={process}
                    onComplete={setCompleting}
                    onWithdraw={setWithdrawing}
                    busy={busy}
                  />
                ))}
              </div>
            </Card>
          ))}

          {board.awaiting_result.length ? (
            <Card title="结果待定" sub="面试时间已过，还没有记录结果">
              <div className="grid grid-2">
                {board.awaiting_result.map((process) => (
                  <ProcessCard
                    key={process.id}
                    process={process}
                    onComplete={setCompleting}
                    onWithdraw={setWithdrawing}
                    busy={busy}
                  />
                ))}
              </div>
            </Card>
          ) : null}

          {board.ongoing_without_schedule.length ? (
            <Card title="进行中 · 待安排" sub="流程还在，但下一轮还没有时间">
              <div className="grid grid-2">
                {board.ongoing_without_schedule.map((process) => (
                  <ProcessCard
                    key={process.id}
                    process={process}
                    onComplete={setCompleting}
                    onWithdraw={setWithdrawing}
                    busy={busy}
                  />
                ))}
              </div>
            </Card>
          ) : null}

          {board.completed.length ? (
            <Card title="已完成" sub="已通过的轮次会完整保留">
              <div className="grid grid-2">
                {board.completed.map((process) => (
                  <ProcessCard key={process.id} process={process} />
                ))}
              </div>
            </Card>
          ) : null}
        </>
      ) : null}

      {board && board.legacy_milestones.length ? (
        <Card
          title="旧版面试记录"
          sub="v0.8 之前记录的面试没有轮次详情，系统不会替你猜是哪一轮"
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
                {board.legacy_milestones.map((item) => (
                  <tr key={item.event_id}>
                    <td>
                      <div className="cell-title">{item.company}</div>
                      <div className="cell-sub">{item.title}</div>
                    </td>
                    <td className="nowrap">{formatDateTime(item.occurred_at)}</td>
                    <td>{item.legacy_round_label ?? '未记录轮次'}</td>
                    <td className="nowrap">
                      <Link className="btn btn-sm" to={`/jobs/${item.job_id}`}>
                        补充面试轮次
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="field-hint mt-1">
            这些记录不会计入任何面试阶段统计。你可以到对应岗位手动补充轮次。
          </p>
        </Card>
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
          process={withdrawing}
          busy={busy}
          onClose={() => setWithdrawing(null)}
          onSubmit={(reason, notes) => void submitWithdraw(reason, notes)}
        />
      ) : null}
    </>
  )
}
