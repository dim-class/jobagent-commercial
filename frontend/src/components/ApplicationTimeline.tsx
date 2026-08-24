// 求职进度 timeline, shared by the job detail page and the queue.
//
// Internal event names never reach the screen: everything is mapped to a
// Chinese label first.

import type { ApplicationEventOut } from '@/types'
import { formatDateTime } from '@/components/ui'

const EVENT_LABEL: Record<string, string> = {
  note: '备注',
  viewed: '查看岗位',
  analyzed: 'AI分析',
  saved: '收藏',
  skipped: '跳过',
  later: '稍后处理',
  status_changed: '状态变更',
  status_reset: '恢复待处理',
  greeting_copied: '复制招呼语',
  applied: '已投递',
  replied: 'HR回复',
  interview: '面试',
  offer: 'Offer',
  rejected: '未通过',
  // v0.5: the user confirmed they replied to a recruiter themselves.
  candidate_reply: '我已回复',
  // v0.7: the user filled in / corrected which resume an application used.
  application_resume_attributed: '补充投递简历',
  application_resume_changed: '修改投递简历',
  // v0.8: interview pipeline milestones. Round detail lives in the
  // interview process; these keep the timeline readable as a story.
  interview_scheduled: '面试已安排',
  interview_completed: '面试已完成',
  interview_passed: '面试通过',
  interview_failed: '面试未通过',
  interview_cancelled: '面试取消',
  interview_withdrawn: '主动终止流程',
  interview_round_corrected: '更正面试结果',
  // v0.9: offer milestones. Compensation itself lives on the Offer, never
  // in the event payload.
  offer_received: '收到 Offer',
  offer_countered: '我方谈薪诉求',
  offer_revised: '公司调整 Offer',
  offer_accepted: '接受 Offer',
  offer_declined: '拒绝 Offer',
  offer_revision_corrected: '更正 Offer 记录',
}

const EVENT_ICON: Record<string, string> = {
  note: '📝',
  viewed: '👀',
  analyzed: '🤖',
  saved: '⭐',
  skipped: '⏭️',
  later: '🕒',
  status_changed: '🔁',
  status_reset: '↩️',
  greeting_copied: '📋',
  applied: '📮',
  replied: '💬',
  interview: '🗓️',
  offer: '🎉',
  rejected: '❌',
  candidate_reply: '✉️',
  application_resume_attributed: '📎',
  application_resume_changed: '📎',
  interview_scheduled: '🗓️',
  interview_completed: '🗓️',
  interview_passed: '✅',
  interview_failed: '❌',
  interview_cancelled: '🚫',
  interview_withdrawn: '🔚',
  interview_round_corrected: '✏️',
  offer_received: '📨',
  offer_countered: '💬',
  offer_revised: '📝',
  offer_accepted: '🤝',
  offer_declined: '🙅',
  offer_revision_corrected: '✏️',
}

const RESPONSE_LABEL: Record<string, string> = {
  positive: '积极',
  neutral: '一般',
  negative: '消极',
}

/** Turn structured event metadata into a short human phrase. */
function describeMetadata(event: ApplicationEventOut): string | null {
  const meta = event.metadata_json ?? {}
  const parts: string[] = []

  const response = meta.response_type
  if (typeof response === 'string') parts.push(RESPONSE_LABEL[response] ?? response)

  const round = meta.interview_round
  if (typeof round === 'string') parts.push(round)

  const skip = meta.skip_reason
  if (typeof skip === 'string') parts.push(skip)

  const reject = meta.reject_reason
  if (typeof reject === 'string') parts.push(reject)

  const salary = meta.offer_salary
  if (typeof salary === 'string') parts.push(salary)

  const reviewAfter = meta.review_after
  if (typeof reviewAfter === 'string') parts.push(`至 ${formatDateTime(reviewAfter)}`)

  return parts.length > 0 ? parts.join(' · ') : null
}

export default function ApplicationTimeline({
  events,
  empty = '暂无进度记录。',
}: {
  events: ApplicationEventOut[]
  empty?: string
}) {
  if (events.length === 0) return <p className="faint small mt-0">{empty}</p>

  return (
    <ul className="timeline">
      {events.map((event) => {
        const label = EVENT_LABEL[event.event_type] ?? event.event_type
        const detail = describeMetadata(event)
        return (
          <li key={event.id}>
            <time>{formatDateTime(event.created_at)}</time>
            <span>
              <span aria-hidden style={{ marginRight: 6 }}>
                {EVENT_ICON[event.event_type] ?? '•'}
              </span>
              <strong>{label}</strong>
              {detail ? <span className="muted"> · {detail}</span> : null}
              {event.notes ? <div className="small faint">{event.notes}</div> : null}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export { EVENT_LABEL }
