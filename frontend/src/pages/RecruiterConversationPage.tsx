import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import ApplicationTimeline from '@/components/ApplicationTimeline'
import { Alert, Card, Loading, Modal, formatDateTime } from '@/components/ui'
import {
  AnalysisPanel,
  DIRECTION_LABEL,
  SOURCE_LABEL,
  StatusBadge,
} from '@/components/recruiter'
import type {
  ConversationDetailOut,
  FollowUpPreset,
  LanguagePreference,
  RecruiterMessageOut,
} from '@/types'

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

const LANGUAGES: { key: LanguagePreference; label: string }[] = [
  { key: 'auto', label: '自动' },
  { key: 'zh', label: '中文' },
  { key: 'ja', label: '日本語' },
  { key: 'en', label: 'English' },
]

const FOLLOW_UPS: { key: FollowUpPreset; label: string }[] = [
  { key: 'tomorrow', label: '明天' },
  { key: 'in_3_days', label: '3天后' },
  { key: 'in_1_week', label: '1周后' },
]

const CLOSE_REASONS = ['已进入面试', '已拒绝', '职位关闭', '无后续', '其他']

export default function RecruiterConversationPage() {
  const { conversationId } = useParams<{ conversationId: string }>()
  const id = Number(conversationId)
  const appendRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const [conversation, setConversation] = useState<ConversationDetailOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>(null)

  const [reply, setReply] = useState('')
  const [replyDirty, setReplyDirty] = useState(false)
  const [copied, setCopied] = useState(false)
  const [language, setLanguage] = useState<LanguagePreference>('auto')

  const [appendText, setAppendText] = useState('')
  const [appending, setAppending] = useState(false)
  const [confirmSend, setConfirmSend] = useState(false)
  const [closing, setClosing] = useState(false)
  const [closeReason, setCloseReason] = useState('无后续')
  const [alsoReject, setAlsoReject] = useState(false)

  const load = useCallback(
    async (adoptDraft: boolean) => {
      setLoading(true)
      try {
        const detail = await api.getConversation(id)
        setConversation(detail)
        // Never clobber text the user has started editing.
        if (adoptDraft && !replyDirty) {
          setReply(detail.latest_analysis?.suggested_reply ?? '')
        }
      } catch (err) {
        setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载失败' })
      } finally {
        setLoading(false)
      }
    },
    [id, replyDirty],
  )

  useEffect(() => {
    if (Number.isFinite(id)) void load(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  async function run(action: () => Promise<unknown>, successText?: string) {
    setBusy(true)
    try {
      await action()
      if (successText) setFeedback({ tone: 'success', text: successText })
      await load(false)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '操作失败' })
    } finally {
      setBusy(false)
    }
  }

  async function appendMessage() {
    if (!appendText.trim()) return
    setBusy(true)
    setFeedback({ tone: 'info', text: '正在保存并分析…' })
    try {
      const result = await api.addRecruiterMessage(id, appendText, { language })
      setAppendText('')
      setAppending(false)
      setReplyDirty(false)
      setConversation(result.conversation)
      setReply(result.conversation.latest_analysis?.suggested_reply ?? '')
      const notes = [...result.warnings]
      if (result.duplicate) notes.push('这条消息之前已经记录过')
      if (result.ai_error) notes.push(result.ai_error)
      setFeedback({
        tone: result.ai_error ? 'warn' : 'success',
        text: notes.length ? notes.join('；') : '已追加消息',
      })
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '追加失败' })
    } finally {
      setBusy(false)
    }
  }

  async function uploadScreenshot(file: File) {
    setBusy(true)
    setFeedback({ tone: 'info', text: `正在识别截图「${file.name}」…` })
    try {
      const result = await api.addRecruiterImage(id, file, language)
      setConversation(result.conversation)
      setReplyDirty(false)
      setReply(result.conversation.latest_analysis?.suggested_reply ?? '')
      setFeedback({
        tone: result.warnings.length ? 'warn' : 'success',
        text: result.warnings.length ? result.warnings.join('；') : '已从截图识别对话',
      })
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '截图识别失败' })
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function reanalyze(smart: boolean) {
    const latest = [...(conversation?.messages ?? [])]
      .reverse()
      .find((m) => m.direction === 'recruiter')
    if (!latest) return
    setBusy(true)
    setFeedback({ tone: 'info', text: smart ? '正在用高质量模型重新分析…' : '正在重新分析…' })
    try {
      const result = smart
        ? await api.reanalyzeRecruiterMessageSmart(id, latest.id)
        : await api.analyzeRecruiterMessage(id, latest.id, { force: true, language })
      setReplyDirty(false)
      setReply(result.result.suggested_reply ?? '')
      setFeedback({
        tone: 'success',
        text: result.cached ? '已读取缓存结果（未消耗额度）' : `分析完成，模型 ${result.model}`,
      })
      await load(false)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '分析失败' })
    } finally {
      setBusy(false)
    }
  }

  async function copyReply() {
    // Copying is not sending and must never change any state.
    try {
      await navigator.clipboard.writeText(reply)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setFeedback({ tone: 'warn', text: '浏览器拒绝了剪贴板访问，请手动选中回复复制。' })
    }
  }

  if (loading && !conversation) return <Loading text="正在加载沟通记录…" />
  if (!conversation)
    return (
      <Alert tone="error">
        沟通记录不存在。<Link to="/recruiter">返回 HR沟通</Link>
      </Alert>
    )

  const analysis = conversation.latest_analysis
  const closed = conversation.status === 'closed'

  return (
    <>
      <header className="page-head">
        <div>
          <div className="row small faint mb-1">
            <Link to="/recruiter">← 返回 HR沟通</Link>
            <span>·</span>
            <span>{SOURCE_LABEL[conversation.source]}</span>
            {conversation.recruiter_name ? <span>· {conversation.recruiter_name}</span> : null}
          </div>
          <h1>{conversation.company || conversation.job_company || '未填写公司'}</h1>
          <p>{conversation.title || conversation.job_title || '未关联岗位'}</p>
        </div>
        <div className="page-actions">
          <StatusBadge value={conversation.status} />
          {conversation.job_id ? (
            <Link className="btn" to={`/jobs/${conversation.job_id}`}>
              查看岗位
            </Link>
          ) : null}
          {!closed ? (
            <button type="button" onClick={() => setClosing(true)} disabled={busy}>
              结束沟通
            </button>
          ) : null}
        </div>
      </header>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      {conversation.suggests_recruiter_reply_event ? (
        <Alert tone="info">
          <div>检测到HR回复。要把这个岗位标记为「已回复」吗？</div>
          <div className="btn-row mt-1">
            <Link className="btn btn-primary btn-sm" to={`/jobs/${conversation.job_id}`}>
              前往岗位记录HR回复
            </Link>
          </div>
          <div className="small faint mt-1">
            AI 只是读懂了消息，不会自动改变岗位状态。
          </div>
        </Alert>
      ) : null}

      <div className="detail-grid">
        <div>
          <Card
            title="对话记录"
            sub={`${conversation.messages.length} 条`}
            actions={
              <div className="btn-row">
                <button type="button" className="btn-sm" onClick={() => setAppending(true)} disabled={busy}>
                  追加HR消息
                </button>
                <button type="button" className="btn-sm" onClick={() => fileRef.current?.click()} disabled={busy}>
                  上传截图
                </button>
              </div>
            }
          >
            <input
              ref={fileRef}
              type="file"
              accept=".png,.jpg,.jpeg,.webp"
              style={{ display: 'none' }}
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) void uploadScreenshot(file)
              }}
            />
            <div className="stack">
              {conversation.messages.map((message: RecruiterMessageOut) => (
                <div
                  key={message.id}
                  className="jd-text"
                  style={{
                    maxHeight: 'none',
                    background:
                      message.direction === 'user' ? 'var(--accent-soft)' : 'var(--surface-2)',
                  }}
                >
                  <div className="small faint mb-1">
                    {DIRECTION_LABEL[message.direction]} ·{' '}
                    {message.source_message_time_text || formatDateTime(message.created_at)}
                  </div>
                  {message.raw_text}
                </div>
              ))}
            </div>
          </Card>

          <Card title="回复草稿" sub="AI 起草，你来决定发不发">
            <div className="row mb-1">
              <span className="small muted">回复语言</span>
              {LANGUAGES.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  className={language === item.key ? 'btn-primary btn-sm' : 'btn-sm'}
                  onClick={() => setLanguage(item.key)}
                >
                  {item.label}
                </button>
              ))}
            </div>

            <div className="field">
              <label htmlFor="rc-reply">回复内容（可自由编辑）</label>
              <textarea
                id="rc-reply"
                rows={8}
                value={reply}
                placeholder="还没有生成回复草稿，可以自己写，也可以点「重新分析」。"
                onChange={(e) => {
                  setReply(e.target.value)
                  setReplyDirty(true)
                }}
              />
              <div className="field-hint">
                {reply.length} 字 · 标记已回复时保存的是你编辑后的最终文本
              </div>
            </div>

            <div className="btn-row">
              <button type="button" onClick={() => void copyReply()} disabled={!reply.trim()}>
                {copied ? '已复制 ✓' : '复制回复'}
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={!reply.trim() || busy || closed}
                onClick={() => setConfirmSend(true)}
              >
                标记已回复
              </button>
              <button type="button" className="btn-sm" onClick={() => void reanalyze(false)} disabled={busy}>
                重新分析
              </button>
              <button type="button" className="btn-sm" onClick={() => void reanalyze(true)} disabled={busy}>
                高质量重分析
              </button>
            </div>

            <div className="field-hint mt-1">
              复制不会改变任何状态。JobAgent 不会替你发送 —— 请在招聘平台或聊天工具里自己发出后再回来标记。
            </div>
          </Card>
        </div>

        <div>
          <Card title="AI分析" sub={analysis ? undefined : '尚未分析'}>
            {analysis ? (
              <AnalysisPanel analysis={analysis} />
            ) : (
              <p className="faint small mt-0">
                还没有分析结果。追加一条招聘方消息，或点「重新分析」。
              </p>
            )}
          </Card>

          <Card title="跟进">
            <div className="row mb-1">
              <span className="small muted">稍后跟进</span>
              {FOLLOW_UPS.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  className="btn-sm"
                  disabled={busy || closed}
                  onClick={() =>
                    void run(() => api.scheduleFollowUp(id, item.key), `已安排在${item.label}跟进`)
                  }
                >
                  {item.label}
                </button>
              ))}
            </div>
            <dl className="meta-list">
              <dt>状态</dt>
              <dd>
                <StatusBadge value={conversation.status} />
              </dd>
              <dt>下次跟进</dt>
              <dd>
                {conversation.next_action_at ? formatDateTime(conversation.next_action_at) : '未设置'}
              </dd>
              <dt>最近消息</dt>
              <dd>
                {conversation.last_message_at ? formatDateTime(conversation.last_message_at) : '—'}
              </dd>
              {conversation.close_reason ? (
                <>
                  <dt>结束原因</dt>
                  <dd>{conversation.close_reason}</dd>
                </>
              ) : null}
            </dl>
          </Card>

          {conversation.job_id ? (
            <Card title="求职进度">
              <ApplicationTimeline events={conversation.job_events} />
            </Card>
          ) : null}
        </div>
      </div>

      {appending ? (
        <Modal
          title="追加HR消息"
          onClose={() => setAppending(false)}
          footer={
            <>
              <button type="button" onClick={() => setAppending(false)}>
                取消
              </button>
              <button type="button" className="btn-primary" onClick={() => void appendMessage()} disabled={busy}>
                保存并分析
              </button>
            </>
          }
        >
          <div className="field">
            <label htmlFor="rc-append">粘贴新的HR消息</label>
            <textarea
              id="rc-append"
              ref={appendRef}
              rows={8}
              value={appendText}
              onChange={(e) => setAppendText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault()
                  void appendMessage()
                }
              }}
            />
            <div className="field-hint">分析时会带上此前对话的摘要与最近几条消息。</div>
          </div>
          <div className="btn-row">
            <button
              type="button"
              className="btn-sm"
              onClick={async () => {
                try {
                  setAppendText(await navigator.clipboard.readText())
                } catch {
                  setFeedback({ tone: 'warn', text: '无法读取剪贴板，请使用 Ctrl+V 手动粘贴。' })
                  appendRef.current?.focus()
                }
              }}
            >
              读取剪贴板
            </button>
          </div>
        </Modal>
      ) : null}

      {confirmSend ? (
        <Modal
          title="确认已回复？"
          onClose={() => setConfirmSend(false)}
          footer={
            <>
              <button type="button" onClick={() => setConfirmSend(false)}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  setConfirmSend(false)
                  void run(() => api.markReplySent(id, reply), '已记录你发送的回复')
                }}
              >
                确认已回复
              </button>
            </>
          }
        >
          <p className="muted mt-0">你已经在招聘平台/通讯工具中发送这条回复了吗？</p>
          <div className="greeting">{reply}</div>
          <div className="field-hint mt-1">
            JobAgent 不会发送任何消息 —— 这里只记录你自己已经发出的内容（以上为最终文本）。
          </div>
        </Modal>
      ) : null}

      {closing ? (
        <Modal
          title="结束沟通"
          onClose={() => setClosing(false)}
          footer={
            <>
              <button type="button" onClick={() => setClosing(false)}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  setClosing(false)
                  void run(
                    () => api.closeConversation(id, closeReason, alsoReject),
                    '已结束沟通',
                  )
                }}
              >
                确认结束
              </button>
            </>
          }
        >
          <div className="field">
            <label>结束原因</label>
            <div className="btn-row">
              {CLOSE_REASONS.map((reason) => (
                <button
                  key={reason}
                  type="button"
                  className={closeReason === reason ? 'btn-primary btn-sm' : 'btn-sm'}
                  onClick={() => setCloseReason(reason)}
                >
                  {reason}
                </button>
              ))}
            </div>
          </div>
          {conversation.job_id ? (
            <div className="checkbox-row">
              <input
                id="rc-reject"
                type="checkbox"
                checked={alsoReject}
                onChange={(e) => setAlsoReject(e.target.checked)}
              />
              <label htmlFor="rc-reject">同时记录职位拒绝</label>
            </div>
          ) : null}
          <div className="field-hint mt-1">
            结束沟通不会改变岗位状态，除非你勾选上面这一项。
          </div>
        </Modal>
      ) : null}
    </>
  )
}
