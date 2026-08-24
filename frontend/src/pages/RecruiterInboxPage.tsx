import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import { Alert, Card, EmptyState, Loading, Modal, formatDateTime } from '@/components/ui'
import {
  DIRECTION_LABEL,
  SOURCE_LABEL,
  SentimentBadge,
  StageBadge,
  StatusBadge,
} from '@/components/recruiter'
import type {
  ConversationStatus,
  InboxResponse,
  JobListItem,
  RecruiterSourceName,
} from '@/types'

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

const SECTIONS: { key: ConversationStatus; label: string }[] = [
  { key: 'needs_reply', label: '待回复' },
  { key: 'follow_up_due', label: '待跟进' },
  { key: 'waiting_recruiter', label: '等待对方' },
  { key: 'closed', label: '已结束' },
]

const SOURCES: RecruiterSourceName[] = [
  'boss',
  'liepin',
  'zhaopin',
  'job51',
  'linkedin',
  'wechat',
  'email',
  'phone',
  'other',
]

export default function RecruiterInboxPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const [data, setData] = useState<InboxResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [keyword, setKeyword] = useState('')

  // 新建沟通 composer
  const [composing, setComposing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [text, setText] = useState('')
  const [source, setSource] = useState<RecruiterSourceName>('boss')
  const [recruiterName, setRecruiterName] = useState('')
  const [jobId, setJobId] = useState<number | ''>('')
  const [jobs, setJobs] = useState<JobListItem[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await api.recruiterInbox(keyword ? { keyword } : {}))
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载失败' })
    } finally {
      setLoading(false)
    }
  }, [keyword])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    api
      .listJobs({ limit: 200, sort: 'created_at' })
      .then((r) => setJobs(r.items))
      .catch(() => setJobs([]))
  }, [])

  // /recruiter?job=12 opens the composer with that job pre-linked.
  useEffect(() => {
    const preset = params.get('job')
    if (preset) {
      setJobId(Number(preset))
      setComposing(true)
    }
  }, [params])

  const grouped = useMemo(() => {
    const items = data?.items ?? []
    return SECTIONS.map((section) => ({
      ...section,
      items: items.filter((i) => i.status === section.key),
    }))
  }, [data])

  async function readClipboard() {
    // Explicit click only - never polled, never automatic.
    if (!navigator.clipboard?.readText) {
      setFeedback({ tone: 'warn', text: '无法读取剪贴板，请使用 Ctrl+V 手动粘贴。' })
      textareaRef.current?.focus()
      return
    }
    try {
      const clip = await navigator.clipboard.readText()
      if (!clip.trim()) {
        setFeedback({ tone: 'warn', text: '剪贴板是空的，请先复制 HR 消息。' })
        return
      }
      setText(clip)
      setFeedback({ tone: 'success', text: `已读取剪贴板（${clip.length} 字）。` })
    } catch {
      setFeedback({ tone: 'warn', text: '无法读取剪贴板，请使用 Ctrl+V 手动粘贴。' })
      textareaRef.current?.focus()
    }
  }

  async function submit() {
    if (!text.trim()) {
      setFeedback({ tone: 'warn', text: '请先粘贴 HR 消息。' })
      return
    }
    setSaving(true)
    setFeedback({ tone: 'info', text: '正在保存并分析…' })
    try {
      const conversation = await api.createConversation({
        job_id: jobId === '' ? null : Number(jobId),
        source,
        recruiter_name: recruiterName || null,
      })
      const result = await api.addRecruiterMessage(conversation.id, text)
      setComposing(false)
      setText('')
      setRecruiterName('')
      if (result.ai_error) {
        setFeedback({ tone: 'warn', text: result.ai_error })
      }
      navigate(`/recruiter/${conversation.id}`)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '保存失败' })
    } finally {
      setSaving(false)
    }
  }

  const summary = data?.summary
  const isEmpty = !loading && (data?.total ?? 0) === 0

  return (
    <>
      <header className="page-head">
        <div>
          <h1>HR沟通</h1>
          <p>
            把收到的招聘方消息复制到这里，JobAgent 帮你整理对方的问题并起草回复。
            <strong>它不会连接任何收件箱，也不会替你发送消息。</strong>
          </p>
        </div>
        <div className="page-actions">
          <button type="button" onClick={() => void load()} disabled={loading}>
            刷新
          </button>
          <button type="button" className="btn-primary" onClick={() => setComposing(true)}>
            新建沟通
          </button>
        </div>
      </header>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      <div className="grid grid-stats">
        <section className="card stat">
          <span className="stat-label">待回复</span>
          <span className="stat-value">{summary?.needs_reply ?? 0}</span>
        </section>
        <section className="card stat">
          <span className="stat-label">今天收到</span>
          <span className="stat-value">{summary?.received_today ?? 0}</span>
        </section>
        <section className="card stat">
          <span className="stat-label">今天已回复</span>
          <span className="stat-value">{summary?.replied_today ?? 0}</span>
          <span className="stat-hint">{summary?.timezone ?? 'Asia/Tokyo'}</span>
        </section>
        <section className="card stat">
          <span className="stat-label">面试安排</span>
          <span className="stat-value">{summary?.interview_scheduling ?? 0}</span>
        </section>
        <section className="card stat">
          <span className="stat-label">待跟进</span>
          <span className="stat-value">{summary?.follow_up_due ?? 0}</span>
        </section>
      </div>

      <Card title="搜索">
        <div className="row">
          <form
            style={{ flex: 1 }}
            onSubmit={(e) => {
              e.preventDefault()
              void load()
            }}
          >
            <input
              value={keyword}
              placeholder="公司 / 职位 / 招聘方 / 消息内容"
              onChange={(e) => setKeyword(e.target.value)}
            />
          </form>
          <button type="button" className="btn-sm" onClick={() => setKeyword('')}>
            清空
          </button>
        </div>
      </Card>

      {loading && !data ? <Loading text="正在加载沟通记录…" /> : null}

      {isEmpty ? (
        <Card>
          <EmptyState
            icon="💬"
            title="还没有记录招聘方消息。"
            text="收到招聘方消息后复制到这里，JobAgent 会帮你整理问题和生成回复建议。不会自动监控任何收件箱。"
            action={
              <button type="button" className="btn-primary" onClick={() => setComposing(true)}>
                粘贴HR消息
              </button>
            }
          />
        </Card>
      ) : null}

      {grouped.map((section) =>
        section.items.length === 0 ? null : (
          <Card key={section.key} title={section.label} sub={`${section.items.length} 条`}>
            <div className="stack">
              {section.items.map((item) => (
                <div key={item.id} className="card" style={{ boxShadow: 'none' }}>
                  <div className="row-between">
                    <div>
                      <div className="cell-title">
                        <Link to={`/recruiter/${item.id}`}>
                          {item.company || item.job_company || '未填写公司'}
                        </Link>
                      </div>
                      <div className="cell-sub">
                        {item.title || item.job_title || '未关联岗位'}
                        {item.recruiter_name ? ` · ${item.recruiter_name}` : ''}
                      </div>
                    </div>
                    <div className="row">
                      <SentimentBadge value={item.sentiment} />
                      <StageBadge value={item.stage} />
                      <StatusBadge value={item.status} />
                    </div>
                  </div>

                  <div className="job-card-meta mt-1">
                    <span className="chip">{SOURCE_LABEL[item.source]}</span>
                    <span className="chip">{item.message_count} 条消息</span>
                    {item.action_item_count > 0 ? (
                      <span className="chip chip-bad">{item.action_item_count} 项待办</span>
                    ) : null}
                    {item.last_message_at ? (
                      <span className="chip">{formatDateTime(item.last_message_at)}</span>
                    ) : null}
                    {item.next_action_at ? (
                      <span className="chip">跟进 {formatDateTime(item.next_action_at)}</span>
                    ) : null}
                  </div>

                  {item.latest_preview ? (
                    <p className="job-card-summary mt-1">
                      {item.latest_direction ? `${DIRECTION_LABEL[item.latest_direction]}：` : ''}
                      {item.latest_preview}
                    </p>
                  ) : null}

                  <div className="btn-row mt-1">
                    <Link className="btn btn-sm" to={`/recruiter/${item.id}`}>
                      查看
                    </Link>
                    {item.job_id ? (
                      <Link className="btn btn-sm" to={`/jobs/${item.job_id}`}>
                        查看岗位
                      </Link>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        ),
      )}

      {composing ? (
        <Modal
          title="新建沟通"
          onClose={() => setComposing(false)}
          footer={
            <>
              <button type="button" onClick={() => setComposing(false)}>
                取消
              </button>
              <button type="button" className="btn-primary" onClick={() => void submit()} disabled={saving}>
                {saving ? '分析中…' : '分析'}
              </button>
            </>
          }
        >
          <div className="field-row">
            <div className="field">
              <label htmlFor="rc-job">关联岗位（可选）</label>
              <select
                id="rc-job"
                value={jobId}
                onChange={(e) => setJobId(e.target.value === '' ? '' : Number(e.target.value))}
              >
                <option value="">不关联</option>
                {jobs.map((job) => (
                  <option key={job.id} value={job.id}>
                    {job.company} · {job.title}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="rc-source">来源</label>
              <select
                id="rc-source"
                value={source}
                onChange={(e) => setSource(e.target.value as RecruiterSourceName)}
              >
                {SOURCES.map((key) => (
                  <option key={key} value={key}>
                    {SOURCE_LABEL[key]}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="rc-name">招聘方姓名（可选）</label>
              <input
                id="rc-name"
                value={recruiterName}
                placeholder="张女士"
                onChange={(e) => setRecruiterName(e.target.value)}
              />
            </div>
          </div>

          <div className="field">
            <label htmlFor="rc-text">粘贴HR消息或对话</label>
            <textarea
              id="rc-text"
              ref={textareaRef}
              rows={10}
              value={text}
              placeholder={'把招聘方发来的消息粘贴到这里。\n整段对话也可以，用「HR：」「我：」标注发言人效果更好。'}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault()
                  void submit()
                }
              }}
            />
            <div className="field-hint">Ctrl+Enter 直接分析</div>
          </div>

          <div className="btn-row">
            <button type="button" className="btn-sm" onClick={() => void readClipboard()}>
              读取剪贴板
            </button>
          </div>
        </Modal>
      ) : null}
    </>
  )
}
