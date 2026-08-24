import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import { Alert, Card, Loading } from '@/components/ui'
import type {
  Confidence,
  ConfirmResponse,
  JobImportCandidate,
  ParseResponse,
  QuickCaptureSettings,
} from '@/types'

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

const CONFIDENCE_LABEL: Record<Confidence, string> = {
  high: '高',
  medium: '中',
  low: '低',
}

const CONFIDENCE_TONE: Record<Confidence, string> = {
  high: 'badge-good',
  medium: 'badge-warn',
  low: 'badge-bad',
}

const METHOD_LABEL: Record<string, string> = {
  deterministic: '本地规则解析',
  ai_text: 'AI 文本提取',
  ai_vision: 'AI 截图识别',
  hybrid: '本地规则 + AI 补全',
}

const ACCEPTED_IMAGE = '.png,.jpg,.jpeg,.webp'

function ConfidenceBadge({ level }: { level: Confidence }) {
  return (
    <span className={`badge ${CONFIDENCE_TONE[level]}`} title="解析可信度">
      {CONFIDENCE_LABEL[level]}
    </span>
  )
}

export default function QuickCapturePage() {
  const navigate = useNavigate()
  const fileInput = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const [text, setText] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [parsing, setParsing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [parsed, setParsed] = useState<ParseResponse | null>(null)
  const [draft, setDraft] = useState<JobImportCandidate | null>(null)
  const [saved, setSaved] = useState<ConfirmResponse | null>(null)
  const [capabilities, setCapabilities] = useState<QuickCaptureSettings | null>(null)
  // Set after an analysis whose verdict recommends applying, so we can point
  // the user at the queue. Nothing is persisted for this - the queue derives it.
  const [queued, setQueued] = useState(false)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    api.quickCaptureSettings().then(setCapabilities).catch(() => setCapabilities(null))
  }, [])

  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  const reset = useCallback(() => {
    setText('')
    setParsed(null)
    setDraft(null)
    setSaved(null)
    setQueued(false)
    setFeedback(null)
    textareaRef.current?.focus()
  }, [])

  async function readClipboard() {
    // Only ever on an explicit click - never polled, never automatic.
    if (!navigator.clipboard?.readText) {
      setFeedback({ tone: 'warn', text: '无法读取剪贴板，请使用 Ctrl+V 手动粘贴。' })
      textareaRef.current?.focus()
      return
    }
    try {
      const clip = await navigator.clipboard.readText()
      if (!clip.trim()) {
        setFeedback({ tone: 'warn', text: '剪贴板是空的，请先复制职位内容。' })
        return
      }
      setText(clip)
      setFeedback({ tone: 'success', text: `已读取剪贴板（${clip.length} 字），点击「解析职位」继续。` })
    } catch {
      setFeedback({ tone: 'warn', text: '无法读取剪贴板，请使用 Ctrl+V 手动粘贴。' })
      textareaRef.current?.focus()
    }
  }

  async function parseText() {
    if (!text.trim()) {
      setFeedback({ tone: 'warn', text: '没有检测到职位文本，请先粘贴内容。' })
      return
    }
    setParsing(true)
    setSaved(null)
    setFeedback({ tone: 'info', text: '正在解析…' })
    try {
      const result = await api.parseJobText(text, sourceUrl || null)
      setParsed(result)
      setDraft(result.candidate)
      setFeedback({ tone: result.ai_error ? 'warn' : 'success', text: result.ai_error ?? result.message })
    } catch (err) {
      setParsed(null)
      setDraft(null)
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '解析失败' })
    } finally {
      setParsing(false)
    }
  }

  async function parseImage(file: File) {
    setParsing(true)
    setSaved(null)
    setFeedback({ tone: 'info', text: `正在识别截图「${file.name}」…` })
    try {
      const result = await api.parseJobImage(file, sourceUrl || null)
      setParsed(result)
      setDraft(result.candidate)
      setFeedback({ tone: 'success', text: result.message })
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '图片识别失败' })
    } finally {
      setParsing(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  async function confirm() {
    if (!draft) return
    setSaving(true)
    setFeedback({ tone: 'info', text: '正在保存…' })
    try {
      const result = await api.confirmCandidate(draft)
      setSaved(result)
      setFeedback({ tone: result.duplicate ? 'warn' : 'success', text: result.message })
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '保存失败' })
    } finally {
      setSaving(false)
    }
  }

  async function analyzeSaved() {
    if (!saved) return
    try {
      setFeedback({ tone: 'info', text: '正在进行 AI 匹配分析…' })
      await api.analyzeJob(saved.job_id)
      navigate(`/jobs/${saved.job_id}`)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '分析失败' })
    }
  }

  function patch(changes: Partial<JobImportCandidate>) {
    setDraft((prev) => (prev ? { ...prev, ...changes } : prev))
  }

  const conf = draft?.confidence
  const busy = parsing || saving

  return (
    <>
      <header className="page-head">
        <div>
          <h1>快速采集</h1>
          <p>
            在你自己的浏览器里打开职位，复制内容后粘贴到这里。JobAgent 不会访问招聘网站，
            也不会打开你提供的链接。
          </p>
        </div>
        <div className="page-actions">
          <button type="button" onClick={reset} disabled={busy}>
            清空
          </button>
        </div>
      </header>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      <Card
        title="快速采集职位"
        sub="Ctrl+Enter 可直接解析"
        actions={
          <div className="btn-row">
            <button type="button" onClick={() => void readClipboard()} disabled={busy}>
              读取剪贴板
            </button>
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              disabled={busy || !capabilities?.openai_configured || !capabilities?.ai_extraction_enabled}
              title={
                capabilities && (!capabilities.openai_configured || !capabilities.ai_extraction_enabled)
                  ? '截图识别需要配置 OpenAI API'
                  : '上传职位页面截图'
              }
            >
              上传截图
            </button>
          </div>
        }
      >
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            const file = e.dataTransfer.files?.[0]
            if (file) void parseImage(file)
          }}
          style={dragging ? { outline: '2px dashed var(--accent)', outlineOffset: 4 } : undefined}
        >
          <div className="field">
            <label htmlFor="qc-text">粘贴职位内容</label>
            <textarea
              id="qc-text"
              ref={textareaRef}
              rows={14}
              value={text}
              placeholder={
                '在招聘网站上选中职位信息，Ctrl+C 复制，然后在这里 Ctrl+V 粘贴。\n' +
                '也可以把职位页面的截图直接拖拽到这个区域。'
              }
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault()
                  void parseText()
                }
              }}
            />
            <div className="field-hint">
              {text.length > 0 ? `${text.length} 字` : '支持整页粘贴，导航与推荐位会被自动过滤'}
              {dragging ? ' · 松开即可上传截图' : ''}
            </div>
          </div>

          <div className="field">
            <label htmlFor="qc-url">职位链接（可选）</label>
            <input
              id="qc-url"
              value={sourceUrl}
              placeholder="https://www.zhipin.com/job_detail/…"
              onChange={(e) => setSourceUrl(e.target.value)}
            />
            <div className="field-hint">
              仅用于标记来源网站，后端不会访问该链接；保存前会自动去掉跟踪参数。
            </div>
          </div>
        </div>

        <input
          ref={fileInput}
          type="file"
          accept={ACCEPTED_IMAGE}
          style={{ display: 'none' }}
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void parseImage(file)
          }}
        />

        <div className="btn-row">
          <button type="button" className="btn-primary" onClick={() => void parseText()} disabled={busy}>
            {parsing ? '解析中…' : '解析职位'}
          </button>
          <button type="button" onClick={reset} disabled={busy}>
            清空
          </button>
        </div>

        {capabilities && !capabilities.openai_configured ? (
          <div className="field-hint mt-1">
            未配置 OPENAI_API_KEY：文字解析照常可用，截图识别不可用。
          </div>
        ) : null}
      </Card>

      {parsing && !draft ? <Loading text="正在解析职位内容…" /> : null}

      {draft ? (
        <Card
          title="职位解析结果"
          sub={`${METHOD_LABEL[draft.extraction_method] ?? draft.extraction_method}${
            parsed?.ai_used ? '' : ' · 未调用 AI'
          }`}
          actions={conf ? <ConfidenceBadge level={conf.overall} /> : undefined}
        >
          <p className="muted small mt-0">
            请核对下面的字段，可以直接修改。确认无误后再保存 —— 保存前不会写入任何数据。
          </p>

          {draft.warnings.length > 0 ? (
            <div className="stack mb-1">
              {draft.warnings.map((w, i) => (
                <div key={i} className="alert alert-warn" style={{ marginBottom: 0 }}>
                  <span aria-hidden>⚠</span>
                  <div className="alert-body">{w}</div>
                </div>
              ))}
            </div>
          ) : null}

          <div className="field-row">
            <div className="field">
              <label htmlFor="qc-company">
                公司 {conf ? <ConfidenceBadge level={conf.company} /> : null}
              </label>
              <input
                id="qc-company"
                value={draft.company ?? ''}
                onChange={(e) => patch({ company: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="qc-title">
                职位 {conf ? <ConfidenceBadge level={conf.title} /> : null}
              </label>
              <input
                id="qc-title"
                value={draft.title ?? ''}
                onChange={(e) => patch({ title: e.target.value })}
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="qc-city">
                城市 {conf ? <ConfidenceBadge level={conf.city} /> : null}
              </label>
              <input
                id="qc-city"
                value={draft.city ?? ''}
                onChange={(e) => patch({ city: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="qc-salary">
                薪资 {conf ? <ConfidenceBadge level={conf.salary} /> : null}
              </label>
              <input
                id="qc-salary"
                value={draft.salary_text ?? ''}
                onChange={(e) => patch({ salary_text: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="qc-exp">
                经验 {conf ? <ConfidenceBadge level={conf.experience} /> : null}
              </label>
              <input
                id="qc-exp"
                value={draft.experience_text ?? ''}
                onChange={(e) => patch({ experience_text: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="qc-edu">学历</label>
              <input
                id="qc-edu"
                value={draft.education_text ?? ''}
                onChange={(e) => patch({ education_text: e.target.value })}
              />
            </div>
          </div>

          <div className="field">
            <label htmlFor="qc-desc">
              职位描述 {conf ? <ConfidenceBadge level={conf.description} /> : null}
            </label>
            <textarea
              id="qc-desc"
              rows={12}
              value={draft.raw_description}
              onChange={(e) => patch({ raw_description: e.target.value })}
            />
            <div className="field-hint">
              {draft.raw_description.length} 字 · 来源：{draft.source}
              {draft.source_url ? ` · ${draft.source_url}` : ''}
            </div>
          </div>

          <div className="btn-row mt-2">
            <button type="button" className="btn-primary" onClick={() => void confirm()} disabled={saving}>
              {saving ? '保存中…' : '确认并保存'}
            </button>
            <button type="button" onClick={() => void parseText()} disabled={busy}>
              重新解析
            </button>
            <button
              type="button"
              onClick={() => {
                setDraft(null)
                setParsed(null)
              }}
              disabled={busy}
            >
              取消
            </button>
          </div>
        </Card>
      ) : null}

      {saved ? (
        <Card title={saved.duplicate ? '该岗位已存在' : '已保存到岗位库'}>
          <dl className="meta-list">
            <dt>公司</dt>
            <dd>{saved.job.company || '未填写'}</dd>
            <dt>职位</dt>
            <dd>{saved.job.title}</dd>
            <dt>城市</dt>
            <dd>{saved.job.city || '未填写'}</dd>
            <dt>薪资</dt>
            <dd>{saved.job.salary_text || '未填写'}</dd>
          </dl>
          <div className="btn-row mt-2">
            <Link className="btn" to={`/jobs/${saved.job_id}`}>
              {saved.duplicate ? '查看已有岗位' : '查看岗位'}
            </Link>
            <button type="button" className="btn-primary" onClick={() => void analyzeSaved()}>
              AI分析
            </button>
            <button type="button" onClick={reset}>
              再采集一个
            </button>
          </div>
          {queued ? (
            <Alert tone="success">
              <div>✓ 已加入投递队列</div>
              <div className="btn-row mt-1">
                <Link className="btn btn-primary btn-sm" to="/queue">
                  前往投递队列
                </Link>
                <Link className="btn btn-sm" to={`/jobs/${saved.job_id}`}>
                  查看岗位
                </Link>
              </div>
            </Alert>
          ) : null}
          <div className="field-hint mt-1">
            采集不会自动触发 AI 匹配分析，只有点击「AI分析」才会消耗额度。
          </div>
        </Card>
      ) : null}

      <Card title="推荐流程">
        <ol className="bullet-list">
          <li>在你平时用的 Chrome / Edge 里打开招聘网站，正常登录、搜索。</li>
          <li>打开一个具体职位，选中职位信息并复制（Ctrl+A / Ctrl+C 也可以）。</li>
          <li>切回 JobAgent，点「读取剪贴板」或直接 Ctrl+V。</li>
          <li>点「解析职位」，核对并修改识别结果。</li>
          <li>点「确认并保存」，再按需点「AI分析」。</li>
        </ol>
        <div className="field-hint mt-1">
          JobAgent 只处理你主动提供的内容：不控制你的浏览器、不注入脚本、不读取
          Cookie、不访问招聘网站。剪贴板只在你点击按钮的那一刻读取一次。
          截图用完即弃，不会保存到磁盘。
        </div>
      </Card>
    </>
  )
}
