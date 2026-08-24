// Shared presentation for recruiter conversations (v0.5).
//
// Internal enum values never reach the screen: everything is mapped to a
// Chinese label first, the same way ApplicationTimeline does it.

import type {
  ConversationStage,
  ConversationStatus,
  MessageDirection,
  RecruiterMessageAnalysisResult,
  RecruiterSourceName,
  Sentiment,
  Urgency,
} from '@/types'

export const SENTIMENT_LABEL: Record<Sentiment, string> = {
  positive: '积极',
  neutral: '中性',
  negative: '消极',
  unclear: '不明确',
}

const SENTIMENT_TONE: Record<Sentiment, string> = {
  positive: 'badge-good',
  neutral: 'badge-ok',
  negative: 'badge-bad',
  unclear: 'badge-neutral',
}

export const STAGE_LABEL: Record<ConversationStage, string> = {
  initial_contact: '初次联系',
  screening: '初步筛选',
  interview_scheduling: '面试安排',
  document_request: '材料索取',
  salary_discussion: '薪资沟通',
  offer_discussion: 'Offer 沟通',
  rejection: '未通过',
  follow_up: '跟进',
  other: '其他',
}

export const STATUS_LABEL: Record<ConversationStatus, string> = {
  needs_reply: '待回复',
  waiting_recruiter: '等待对方',
  follow_up_due: '待跟进',
  closed: '已结束',
  no_action: '无需处理',
}

const STATUS_TONE: Record<ConversationStatus, string> = {
  needs_reply: 'badge-warn',
  waiting_recruiter: 'badge-neutral',
  follow_up_due: 'badge-ok',
  closed: 'badge-neutral',
  no_action: 'badge-neutral',
}

export const SOURCE_LABEL: Record<RecruiterSourceName, string> = {
  boss: 'BOSS直聘',
  liepin: '猎聘',
  zhaopin: '智联',
  job51: '51job',
  linkedin: 'LinkedIn',
  wechat: '微信',
  email: '邮件',
  phone: '电话记录',
  other: '其他',
}

export const URGENCY_LABEL: Record<Urgency, string> = {
  low: '不急',
  normal: '正常',
  high: '较急',
}

export const DIRECTION_LABEL: Record<MessageDirection, string> = {
  recruiter: '招聘方',
  user: '我',
}

/** Request-type -> Chinese. Unknown types fall back to the raw value. */
export const REQUEST_LABEL: Record<string, string> = {
  interview_availability: '面试时间',
  expected_salary: '期望薪资',
  current_salary: '当前薪资',
  start_date: '到岗时间',
  notice_period: '离职周期',
  resume: '简历',
  resume_update: '简历更新',
  work_location: '工作地点',
  remote_preference: '远程意向',
  visa_status: '签证状态',
  visa_expiry: '签证到期',
  sponsorship: '签证支持',
  language_skill: '语言能力',
  technical_experience: '技术经验',
  years_of_experience: '工作年限',
  certification: '证书',
  motivation: '求职动机',
  reason_for_change: '离职原因',
  other: '其他',
}

export function SentimentBadge({ value }: { value: Sentiment | null }) {
  if (!value) return <span className="badge badge-neutral">未分析</span>
  return <span className={`badge ${SENTIMENT_TONE[value]}`}>{SENTIMENT_LABEL[value]}</span>
}

export function StatusBadge({ value }: { value: ConversationStatus }) {
  return <span className={`badge ${STATUS_TONE[value]}`}>{STATUS_LABEL[value]}</span>
}

export function StageBadge({ value }: { value: ConversationStage | null }) {
  if (!value) return null
  return <span className="chip">{STAGE_LABEL[value]}</span>
}

/** The "HR正在询问 / 待处理事项 / 时间信息" panel. */
export function AnalysisPanel({ analysis }: { analysis: RecruiterMessageAnalysisResult }) {
  return (
    <>
      <div className="row mb-1">
        <SentimentBadge value={analysis.sentiment} />
        <StageBadge value={analysis.conversation_stage} />
        <span className="chip">{URGENCY_LABEL[analysis.urgency]}</span>
        {analysis.needs_reply ? (
          <span className="badge badge-warn">需要回复 {analysis.recruiter_requests.length} 项</span>
        ) : (
          <span className="badge badge-neutral">暂无需回复</span>
        )}
      </div>

      {analysis.summary ? <p className="small muted mt-0">{analysis.summary}</p> : null}

      {analysis.recruiter_requests.length > 0 ? (
        <div className="mt-2">
          <h3 className="mb-1">HR正在询问</h3>
          <ul className="bullet-list">
            {analysis.recruiter_requests.map((request, index) => (
              <li key={`${request.type}-${index}`}>
                <strong>{REQUEST_LABEL[request.type] ?? request.type}</strong>
                {request.required ? null : <span className="faint small">（可选）</span>}
                <div className="small muted">{request.summary}</div>
                {request.suggested_answer ? (
                  <div className="small">可回答：{request.suggested_answer}</div>
                ) : (
                  <div className="small faint">资料中没有现成答案，需要你补充</div>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {analysis.action_items.length > 0 ? (
        <div className="mt-2">
          <h3 className="mb-1">待处理事项</h3>
          <ul className="bullet-list">
            {analysis.action_items.map((item, index) => (
              <li key={index}>
                {item.summary}
                {item.blocking ? <span className="badge badge-warn">必需</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {analysis.dates_times.length > 0 ? (
        <div className="mt-2">
          <h3 className="mb-1">时间信息</h3>
          <ul className="bullet-list">
            {analysis.dates_times.map((mention, index) => (
              <li key={index}>
                <strong>{mention.raw_text}</strong>
                {mention.context ? <span className="muted"> · {mention.context}</span> : null}
                {mention.is_ambiguous ? (
                  <span className="badge badge-warn">⚠ 需确认具体时间</span>
                ) : (
                  <span className="chip chip-good">
                    {mention.normalized_date ?? mention.normalized_at}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {analysis.missing_information.length > 0 ? (
        <div className="alert alert-warn mt-2">
          <span aria-hidden>⚠</span>
          <div className="alert-body">
            <strong>需要你补充的信息</strong>
            <ul className="bullet-list mt-1">
              {analysis.missing_information.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
            <div className="small">这些内容资料里没有，AI 不会替你编造。</div>
          </div>
        </div>
      ) : null}

      {analysis.risk_flags.length > 0 ? (
        <div className="mt-2">
          <h3 className="mb-1">风险提示</h3>
          <ul className="bullet-list">
            {analysis.risk_flags.map((flag, index) => (
              <li key={index}>{flag}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  )
}
