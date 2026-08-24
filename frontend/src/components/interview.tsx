// Interview pipeline UI pieces, shared by the 面试 board, the process page and
// the job detail card so a round looks the same everywhere.
//
// Every action here records something the human decided. Nothing schedules,
// accepts or replies on its own.

import { useState } from 'react'

import { Alert, Modal, formatDateTime } from '@/components/ui'
import type {
  FeedbackTag,
  InterviewFailureReason,
  InterviewLocationType,
  InterviewOutcome,
  InterviewProcessOut,
  InterviewProcessStatus,
  InterviewRoundOut,
  InterviewRoundType,
  RoundCompletePayload,
  RoundCreatePayload,
  WithdrawReason,
} from '@/types'

export const ROUND_TYPES: { value: InterviewRoundType; label: string }[] = [
  { value: 'hr', label: 'HR面' },
  { value: 'screening', label: '初筛' },
  { value: 'technical', label: '技术面' },
  { value: 'coding', label: '编程面' },
  { value: 'system_design', label: '系统设计' },
  { value: 'manager', label: '主管面' },
  { value: 'culture', label: '文化面' },
  { value: 'final', label: '终面' },
  { value: 'other', label: '其他' },
]

export const LOCATION_TYPES: { value: InterviewLocationType; label: string }[] = [
  { value: 'online', label: '线上' },
  { value: 'onsite', label: '线下' },
  { value: 'phone', label: '电话' },
  { value: 'unknown', label: '待定' },
]

export const FAILURE_REASONS: { value: InterviewFailureReason; label: string }[] = [
  { value: 'technical_depth', label: '技术深度不足' },
  { value: 'experience_years', label: '经验年限' },
  { value: 'language', label: '语言' },
  { value: 'role_fit', label: '岗位匹配' },
  { value: 'salary', label: '薪资' },
  { value: 'visa', label: '签证' },
  { value: 'culture_fit', label: '文化匹配' },
  { value: 'position_cancelled', label: '职位取消' },
  { value: 'unknown', label: '未知' },
  { value: 'other', label: '其他' },
]

export const WITHDRAW_REASONS: { value: WithdrawReason; label: string }[] = [
  { value: 'accepted_other_offer', label: '接受其他Offer' },
  { value: 'salary', label: '薪资不合适' },
  { value: 'company', label: '公司不合适' },
  { value: 'location', label: '地点' },
  { value: 'role_content', label: '岗位内容' },
  { value: 'personal', label: '个人原因' },
  { value: 'other', label: '其他' },
]

export const FEEDBACK_TAGS: { value: FeedbackTag; label: string }[] = [
  { value: 'technical_depth', label: '技术深度' },
  { value: 'communication', label: '沟通表达' },
  { value: 'language', label: '语言' },
  { value: 'experience', label: '工作经验' },
  { value: 'cloud', label: '云平台' },
  { value: 'coding', label: '编程' },
  { value: 'system_design', label: '系统设计' },
  { value: 'culture', label: '文化匹配' },
  { value: 'salary', label: '薪资' },
  { value: 'other', label: '其他' },
]

export const OUTCOME_LABEL: Record<InterviewOutcome, string> = {
  pending: '结果待定',
  passed: '已通过',
  failed: '未通过',
  unknown: '未知',
}

export const PROCESS_STATUS_LABEL: Record<InterviewProcessStatus, string> = {
  ongoing: '进行中',
  completed: '已结束',
  rejected: '未通过',
  withdrawn: '主动终止',
  offer: 'Offer',
}

const LOCATION_LABEL: Record<InterviewLocationType, string> = {
  online: '线上',
  onsite: '线下',
  phone: '电话',
  unknown: '待定',
}

/** One line in the pipeline: HR ✓ 已通过 / 技术二面 ● 8/28 19:00 / 终面 — */
export function RoundLine({
  round,
  onComplete,
  onCancel,
  busy,
}: {
  round: InterviewRoundOut
  onComplete?: (round: InterviewRoundOut) => void
  onCancel?: (round: InterviewRoundOut) => void
  busy?: boolean
}) {
  const marker =
    round.status === 'cancelled'
      ? '🚫'
      : round.status === 'completed'
        ? round.outcome === 'passed'
          ? '✓'
          : round.outcome === 'failed'
            ? '✗'
            : '•'
        : round.scheduled_at
          ? '●'
          : '—'

  const detail =
    round.status === 'completed'
      ? OUTCOME_LABEL[round.outcome]
      : round.status === 'cancelled'
        ? '已取消'
        : round.scheduled_at
          ? formatDateTime(round.scheduled_at)
          : '待安排'

  return (
    <div className="row-between mt-1">
      <div>
        <span aria-hidden>{marker}</span>{' '}
        <strong>{round.display_name}</strong>{' '}
        <span className="faint small">{detail}</span>
        {round.interviewer_name ? (
          <span className="faint small"> · {round.interviewer_name}</span>
        ) : null}
        {round.location_type !== 'unknown' ? (
          <span className="chip">{LOCATION_LABEL[round.location_type]}</span>
        ) : null}
      </div>
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
        {onComplete && round.status !== 'cancelled' ? (
          <button
            type="button"
            className="btn-sm"
            disabled={busy}
            onClick={() => onComplete(round)}
          >
            {round.status === 'completed' ? '更正结果' : '记录结果'}
          </button>
        ) : null}
        {onCancel && round.status !== 'completed' && round.status !== 'cancelled' ? (
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={busy}
            onClick={() => onCancel(round)}
          >
            取消
          </button>
        ) : null}
      </div>
    </div>
  )
}

/** 添加下一轮. Only the round type is genuinely required. */
export function AddRoundDialog({
  onClose,
  onSubmit,
  busy,
  defaultScheduledAt,
  defaultRoundType,
}: {
  onClose: () => void
  onSubmit: (payload: RoundCreatePayload) => void
  busy?: boolean
  defaultScheduledAt?: string | null
  defaultRoundType?: InterviewRoundType | null
}) {
  const [roundType, setRoundType] = useState<InterviewRoundType>(
    defaultRoundType ?? 'technical',
  )
  const [name, setName] = useState('')
  const [scheduledAt, setScheduledAt] = useState(
    defaultScheduledAt ? defaultScheduledAt.slice(0, 16) : '',
  )
  const [duration, setDuration] = useState('')
  const [location, setLocation] = useState<InterviewLocationType>('unknown')
  const [meetingUrl, setMeetingUrl] = useState('')
  const [interviewer, setInterviewer] = useState('')
  const [interviewerRole, setInterviewerRole] = useState('')
  const [notes, setNotes] = useState('')

  return (
    <Modal
      title="添加面试轮次"
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
                round_type: roundType,
                custom_round_name: name.trim() || null,
                scheduled_at: scheduledAt ? new Date(scheduledAt).toISOString() : null,
                duration_minutes: duration ? Number(duration) : null,
                location_type: location,
                meeting_url: meetingUrl.trim() || null,
                interviewer_name: interviewer.trim() || null,
                interviewer_role: interviewerRole.trim() || null,
                notes: notes.trim() || null,
              })
            }
          >
            添加
          </button>
        </>
      }
    >
      <div className="field">
        <label htmlFor="round-type">轮次类型</label>
        <select
          id="round-type"
          value={roundType}
          onChange={(event) => setRoundType(event.target.value as InterviewRoundType)}
        >
          {ROUND_TYPES.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="round-name">名称（可选）</label>
        <input
          id="round-name"
          value={name}
          placeholder="例如：技术二面"
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="round-time">日期时间（可选）</label>
          <input
            id="round-time"
            type="datetime-local"
            value={scheduledAt}
            onChange={(event) => setScheduledAt(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="round-duration">预计时长（分钟）</label>
          <input
            id="round-duration"
            type="number"
            min={1}
            value={duration}
            onChange={(event) => setDuration(event.target.value)}
          />
        </div>
      </div>
      <div className="field">
        <label htmlFor="round-location">形式</label>
        <select
          id="round-location"
          value={location}
          onChange={(event) => setLocation(event.target.value as InterviewLocationType)}
        >
          {LOCATION_TYPES.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="round-url">会议链接（可选）</label>
        <input
          id="round-url"
          value={meetingUrl}
          placeholder="https://…"
          onChange={(event) => setMeetingUrl(event.target.value)}
        />
        <div className="field-hint">
          会议链接可能包含访问令牌，只会在面试详情里显示，不会进入任何统计。
        </div>
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="round-interviewer">面试官</label>
          <input
            id="round-interviewer"
            value={interviewer}
            onChange={(event) => setInterviewer(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="round-interviewer-role">面试官职位</label>
          <input
            id="round-interviewer-role"
            value={interviewerRole}
            onChange={(event) => setInterviewerRole(event.target.value)}
          />
        </div>
      </div>
      <div className="field">
        <label htmlFor="round-notes">备注（可选）</label>
        <textarea
          id="round-notes"
          rows={2}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
        />
      </div>
    </Modal>
  )
}

/** 记录结果. Explicit confirmation; rejection is never implied by a failure. */
export function CompleteRoundDialog({
  round,
  onClose,
  onSubmit,
  busy,
}: {
  round: InterviewRoundOut
  onClose: () => void
  onSubmit: (payload: RoundCompletePayload) => void
  busy?: boolean
}) {
  const isCorrection = round.status === 'completed'
  const [outcome, setOutcome] = useState<InterviewOutcome>(
    isCorrection ? round.outcome : 'passed',
  )
  const [feedback, setFeedback] = useState(round.feedback_text ?? '')
  const [notes, setNotes] = useState(round.notes ?? '')
  const [tags, setTags] = useState<Set<FeedbackTag>>(
    new Set((round.feedback_tags ?? []) as FeedbackTag[]),
  )
  const [reason, setReason] = useState<InterviewFailureReason | ''>(
    round.failure_reason ?? '',
  )
  // Deliberately unchecked by default: a failed round is not automatically the
  // employer closing the door.
  const [alsoReject, setAlsoReject] = useState(false)

  function toggleTag(tag: FeedbackTag) {
    setTags((prev) => {
      const next = new Set(prev)
      if (next.has(tag)) next.delete(tag)
      else next.add(tag)
      return next
    })
  }

  return (
    <Modal
      title={isCorrection ? `更正结果：${round.display_name}` : `记录结果：${round.display_name}`}
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
                outcome,
                feedback_text: feedback.trim() || null,
                notes: notes.trim() || null,
                feedback_tags: [...tags],
                failure_reason: outcome === 'failed' && reason ? reason : null,
                also_record_rejection: outcome === 'failed' && alsoReject,
                correction: isCorrection,
              })
            }
          >
            {isCorrection ? '确认更正' : '确认记录'}
          </button>
        </>
      }
    >
      {isCorrection ? (
        <Alert tone="warn">
          这一轮已经记录过「{OUTCOME_LABEL[round.outcome]}」。更正会追加一条更正记录，
          原始记录不会被删除。
        </Alert>
      ) : null}

      <div className="field">
        <label>结果</label>
        <div className="stack">
          {(['passed', 'failed', 'pending'] as InterviewOutcome[]).map((value) => (
            <label key={value} className="checkbox-row">
              <input
                type="radio"
                name="outcome"
                checked={outcome === value}
                onChange={() => setOutcome(value)}
              />
              <span>{OUTCOME_LABEL[value]}</span>
            </label>
          ))}
        </div>
      </div>

      {outcome === 'failed' ? (
        <>
          <div className="field">
            <label htmlFor="failure-reason">未通过原因（可选）</label>
            <select
              id="failure-reason"
              value={reason}
              onChange={(event) =>
                setReason(event.target.value as InterviewFailureReason | '')
              }
            >
              <option value="">不填</option>
              {FAILURE_REASONS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={alsoReject}
              onChange={(event) => setAlsoReject(event.target.checked)}
            />
            <span>同时记录职位拒绝（流程就此结束）</span>
          </label>
          <div className="field-hint">
            只有确认对方明确拒绝时才勾选。一轮未通过不一定代表流程结束。
          </div>
        </>
      ) : null}

      <div className="field mt-2">
        <label htmlFor="feedback">面试官/HR 的反馈</label>
        <textarea
          id="feedback"
          rows={3}
          value={feedback}
          placeholder="对方实际说了什么"
          onChange={(event) => setFeedback(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="own-notes">我自己的记录</label>
        <textarea
          id="own-notes"
          rows={3}
          value={notes}
          placeholder="问到的问题、没答好的地方、需要准备的内容"
          onChange={(event) => setNotes(event.target.value)}
        />
        <div className="field-hint">
          两者分开存储：统计只使用你选择的标签，不会去解读任何自由文本。
        </div>
      </div>

      <div className="field">
        <label>反馈标签（可选，可多选）</label>
        <div className="chip-list">
          {FEEDBACK_TAGS.map((item) => (
            <button
              key={item.value}
              type="button"
              className={tags.has(item.value) ? 'chip chip-good' : 'chip'}
              onClick={() => toggleTag(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
    </Modal>
  )
}

/** 主动终止流程. Recorded as a withdrawal, never as a rejection. */
export function WithdrawDialog({
  process,
  onClose,
  onSubmit,
  busy,
}: {
  process: InterviewProcessOut
  onClose: () => void
  onSubmit: (reason: WithdrawReason, notes: string) => void
  busy?: boolean
}) {
  const [reason, setReason] = useState<WithdrawReason>('accepted_other_offer')
  const [notes, setNotes] = useState('')

  return (
    <Modal
      title="终止这个面试流程？"
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
            确认终止
          </button>
        </>
      }
    >
      <p className="mt-0">
        <strong>{process.company}</strong>
        <br />
        {process.title}
      </p>
      <div className="field">
        <label htmlFor="withdraw-reason">原因</label>
        <select
          id="withdraw-reason"
          value={reason}
          onChange={(event) => setReason(event.target.value as WithdrawReason)}
        >
          {WITHDRAW_REASONS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="withdraw-notes">备注（可选）</label>
        <textarea
          id="withdraw-notes"
          rows={2}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
        />
      </div>
      <Alert tone="info">
        这会记录为「主动终止」，<strong>不会</strong>被统计成对方拒绝你。
        已经通过的轮次会完整保留。
      </Alert>
    </Modal>
  )
}
