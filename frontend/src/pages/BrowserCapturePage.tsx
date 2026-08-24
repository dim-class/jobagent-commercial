import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import { Alert, Card, Loading } from '@/components/ui'
import type { BrowserStatus, CaptureResponse } from '@/types'

// The browser can stay open for a long time, so poll gently.
const POLL_INTERVAL_MS = 4000

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

export default function BrowserCapturePage() {
  const navigate = useNavigate()

  const [status, setStatus] = useState<BrowserStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [capturing, setCapturing] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [captured, setCaptured] = useState<CaptureResponse | null>(null)

  // Avoid overlapping polls when the backend is slow.
  const polling = useRef(false)

  const refresh = useCallback(async (showSpinner = false) => {
    if (polling.current) return
    polling.current = true
    if (showSpinner) setLoading(true)
    try {
      setStatus(await api.browserStatus())
    } catch (err) {
      if (err instanceof ApiError && err.code === 'network_error') {
        setStatus(null)
      }
    } finally {
      polling.current = false
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh(true)
    const timer = window.setInterval(() => void refresh(), POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  async function handleStart() {
    setStarting(true)
    setFeedback({ tone: 'info', text: '正在启动浏览器…' })
    try {
      const result = await api.startBrowser()
      setStatus(result)
      setFeedback({
        tone: 'success',
        text: result.already_running
          ? '浏览器已在运行中，请切换到浏览器窗口继续操作。'
          : '浏览器已启动，请在弹出的浏览器窗口中自行登录并搜索岗位。',
      })
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '启动浏览器失败' })
    } finally {
      setStarting(false)
    }
  }

  async function handleStop() {
    setStopping(true)
    try {
      const result = await api.stopBrowser()
      setFeedback({ tone: 'info', text: result.message })
      await refresh()
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '关闭浏览器失败' })
    } finally {
      setStopping(false)
    }
  }

  async function handleCapture() {
    setCapturing(true)
    setCaptured(null)
    setFeedback({ tone: 'info', text: '正在读取当前页面…' })
    try {
      const result = await api.captureCurrentJob(false)
      setCaptured(result)
      setFeedback({
        tone: result.duplicate ? 'warn' : 'success',
        text: result.message,
      })
      await refresh()
    } catch (err) {
      setCaptured(null)
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '采集失败' })
    } finally {
      setCapturing(false)
    }
  }

  async function handleAnalyze() {
    if (!captured) return
    setAnalyzing(true)
    setFeedback({ tone: 'info', text: '正在进行 AI 匹配分析…' })
    try {
      const analysis = await api.analyzeJob(captured.job_id)
      setFeedback({
        tone: 'success',
        text: `分析完成：${analysis.result.overall_score} 分${
          analysis.meta.cached ? '（读取缓存，未消耗额度）' : ''
        }`,
      })
      navigate(`/jobs/${captured.job_id}`)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '分析失败' })
    } finally {
      setAnalyzing(false)
    }
  }

  const running = status?.running ?? false
  const canCapture = running && !capturing
  // The backend reports this when a site declines to render for an
  // automation-controlled browser. We do not work around it - we point the
  // user at the intake method that does not involve automation at all.
  const siteBlocked =
    (status?.message ?? '').includes('空白') ||
    (feedback?.text ?? '').includes('空白')

  return (
    <>
      <header className="page-head">
        <div>
          <h1>浏览器采集</h1>
          <p>
            打开一个可见的浏览器，由你自己登录和搜索；Job Agent 只读取你当前打开的那一个岗位页面。
          </p>
        </div>
        <div className="page-actions">
          <button type="button" onClick={() => void refresh(true)} disabled={loading}>
            刷新状态
          </button>
          {running ? (
            <button type="button" className="btn-danger" onClick={() => void handleStop()} disabled={stopping}>
              {stopping ? '关闭中…' : '停止浏览器'}
            </button>
          ) : (
            <button type="button" className="btn-primary" onClick={() => void handleStart()} disabled={starting}>
              {starting ? '启动中…' : '打开浏览器'}
            </button>
          )}
        </div>
      </header>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      {siteBlocked ? (
        <Alert tone="warn">
          <div>
            BOSS 当前可能阻止自动化浏览器访问。建议使用「快速采集」：
            在正常浏览器中复制职位内容后导入。
          </div>
          <div className="btn-row mt-1">
            <Link className="btn btn-primary btn-sm" to="/quick-capture">
              前往快速采集
            </Link>
          </div>
        </Alert>
      ) : null}

      <div className="grid grid-2">
        <Card title="浏览器状态">
          {loading && !status ? (
            <Loading text="正在读取浏览器状态…" />
          ) : (
            <dl className="meta-list">
              <dt>状态</dt>
              <dd>
                {running ? (
                  <span className="badge badge-good">已启动</span>
                ) : (
                  <span className="badge badge-neutral">未启动</span>
                )}
              </dd>
              <dt>说明</dt>
              <dd>{status?.message || '—'}</dd>
              <dt>支持网站</dt>
              <dd>{status?.supported_hosts?.join('、') || '—'}</dd>
              {status?.channel ? (
                <>
                  <dt>浏览器内核</dt>
                  <dd className="mono">{status.channel}</dd>
                </>
              ) : null}
              <dt>打开标签数</dt>
              <dd>{status?.page_count ?? 0}</dd>
              <dt>配置目录</dt>
              <dd className="mono small">{status?.profile_dir || '—'}</dd>
            </dl>
          )}
        </Card>

        <Card title="当前页面" sub="采集时会读取这个页面">
          <dl className="meta-list">
            <dt>网站</dt>
            <dd>{status?.site ? status.site.toUpperCase() : '未识别'}</dd>
            <dt>页面标题</dt>
            <dd>{status?.current_title || '—'}</dd>
            <dt>URL</dt>
            <dd className="mono small" style={{ wordBreak: 'break-all' }}>
              {status?.current_url || '—'}
            </dd>
          </dl>
          <div className="btn-row mt-2">
            <button
              type="button"
              className="btn-primary"
              onClick={() => void handleCapture()}
              disabled={!canCapture}
            >
              {capturing ? '采集中…' : '采集当前岗位'}
            </button>
          </div>
          {!running ? (
            <div className="field-hint mt-1">请先点击「打开浏览器」。</div>
          ) : null}
        </Card>
      </div>

      <Card title="使用步骤">
        <div className="field-hint mb-1">
          如果招聘网站阻止自动化浏览器，请改用 <Link to="/quick-capture">快速采集</Link>
          ——在你平时用的浏览器里复制职位内容后粘贴导入。
        </div>
        <ol className="bullet-list">
          <li>点击「打开浏览器」，等待浏览器窗口弹出。</li>
          <li>在弹出的浏览器中<strong>自行登录</strong>招聘网站（BOSS 直聘）。Job Agent 不会代你登录，也不保存任何账号密码。</li>
          <li>自己搜索并打开一个<strong>具体岗位的详情页</strong>（不是搜索结果列表）。</li>
          <li>回到这里点击「采集当前岗位」。</li>
          <li>需要 AI 匹配分析时，再手动点击「AI分析」——采集本身不消耗 API 额度。</li>
        </ol>
        <div className="field-hint mt-1">
          浏览器登录状态会保存在本机的独立配置目录中，下次打开无需重新登录；不会影响你日常使用的浏览器。
          如果网站要求验证，请在浏览器里自己完成后再回来采集。
        </div>
      </Card>

      {captured ? (
        <Card
          title={captured.duplicate ? '该岗位已存在' : '采集成功'}
          sub={captured.selected_url ?? undefined}
        >
          <dl className="meta-list">
            <dt>公司</dt>
            <dd>{captured.fields.company || '未识别'}</dd>
            <dt>岗位</dt>
            <dd>{captured.fields.title}</dd>
            <dt>城市</dt>
            <dd>{captured.fields.city || '未识别'}</dd>
            <dt>薪资</dt>
            <dd>{captured.fields.salary_text || '未识别'}</dd>
            <dt>经验要求</dt>
            <dd>{captured.fields.experience_text || '未识别'}</dd>
            <dt>JD 字数</dt>
            <dd>{captured.fields.description_chars}</dd>
          </dl>

          {captured.fields.fields_missing.length > 0 ? (
            <div className="field-hint mt-1">
              未识别到的字段：{captured.fields.fields_missing.join('、')}
              （不影响保存，可在岗位详情中查看原文）
            </div>
          ) : null}

          <div className="btn-row mt-2">
            <Link className="btn" to={`/jobs/${captured.job_id}`}>
              {captured.duplicate ? '查看已有岗位' : '查看岗位'}
            </Link>
            {!captured.duplicate ? (
              <button
                type="button"
                className="btn-primary"
                onClick={() => void handleAnalyze()}
                disabled={analyzing}
              >
                {analyzing ? '分析中…' : 'AI分析'}
              </button>
            ) : null}
          </div>
        </Card>
      ) : null}

      <Card title="边界说明">
        <ul className="bullet-list">
          <li>浏览器始终可见，登录、搜索、翻页、验证码全部由你本人操作。</li>
          <li>Job Agent 只读取你当前打开的岗位页面，不会自动翻页、不会批量抓取。</li>
          <li>不会点击「立即沟通」，不会自动投递，也不会自动发送任何招呼语。</li>
          <li>不保存招聘网站账号密码；Cookie 只留在本机的浏览器配置目录里，不会通过接口暴露。</li>
        </ul>
      </Card>
    </>
  )
}
