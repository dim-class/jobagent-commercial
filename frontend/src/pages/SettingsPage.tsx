import { useCallback, useEffect, useState } from 'react'

import { ApiError, api } from '@/api/client'
import { Alert, Card, Loading } from '@/components/ui'
import type { AiSettingsOut, AppSettings, BatchAnalyzeResponse } from '@/types'

export default function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [batchRunning, setBatchRunning] = useState(false)
  const [batchResult, setBatchResult] = useState<BatchAnalyzeResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  //: The key is typed here and goes straight to the backend, which writes it
  //: to `.env`. It is never read back: the field starts empty every time and
  //: the page only ever learns whether one is set and its last four chars.
  const [ai, setAi] = useState<AiSettingsOut | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [modelFast, setModelFast] = useState('')
  const [modelSmart, setModelSmart] = useState('')
  const [aiBusy, setAiBusy] = useState(false)
  const [aiNote, setAiNote] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setSettings(await api.settings())
      const current = await api.getAiSettings()
      setAi(current)
      setBaseUrl(current.base_url)
      setModelFast(current.model_fast)
      setModelSmart(current.model_smart)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '加载设置失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function runBatch() {
    setBatchRunning(true)
    setError(null)
    setBatchResult(null)
    try {
      setBatchResult(await api.analyzeBatch(false))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '批量分析失败')
    } finally {
      setBatchRunning(false)
    }
  }

  async function saveAi() {
    if (aiBusy) return
    setAiBusy(true); setAiNote(''); setError(null)
    try {
      const saved = await api.saveAiSettings({
        // An empty box means "leave it alone", never "erase it" - otherwise
        // saving a model name would silently wipe the key.
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        base_url: baseUrl.trim(),
        model_fast: modelFast.trim(),
        model_smart: modelSmart.trim(),
      })
      setAi(saved)
      setApiKey('')
      setAiNote('已保存，立即生效，不需要重启后端。')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '保存失败')
    } finally { setAiBusy(false) }
  }

  if (loading && !settings) return <Loading text="正在加载设置…" />
  if (!settings) return <Alert tone="error">{error ?? '无法加载设置。'}</Alert>

  return (
    <>
      <header className="page-head">
        <div>
          <h1>设置</h1>
          <p>运行时配置只读展示。所有密钥都保存在后端的 .env 中，前端永远拿不到。</p>
        </div>
        <div className="page-actions">
          <button type="button" onClick={() => void load()}>
            刷新
          </button>
        </div>
      </header>

      {error ? <Alert tone="error" onDismiss={() => setError(null)}>{error}</Alert> : null}

      <Card title="AI 接口" sub="填你自己的 Key。保存后立即生效，不用重启">
        <p className="small faint">
          支持任何 <strong>OpenAI 兼容</strong>的接口：留空就是 OpenAI 官方；
          DeepSeek、Moonshot、通义等填各自的接口地址即可。
          Key 只写进后端的 <span className="mono">{ai?.env_path ?? 'backend/.env'}</span>，
          不进数据库、不进日志、不会被任何接口返回。
        </p>
        <div className="field">
          <label htmlFor="ai-key">API Key</label>
          <input
            id="ai-key"
            type="password"
            autoComplete="off"
            value={apiKey}
            placeholder={ai?.configured ? `已配置（${ai.hint}），留空则不修改` : '尚未配置'}
            onChange={e => setApiKey(e.target.value)}
          />
          <p className="field-hint">留空表示保持不变。这里永远不会显示已保存的 Key。</p>
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="ai-base">接口地址</label>
            <input id="ai-base" value={baseUrl} placeholder="留空 = OpenAI 官方"
              onChange={e => setBaseUrl(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="ai-fast">常规模型</label>
            <input id="ai-fast" value={modelFast} onChange={e => setModelFast(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="ai-smart">高质量模型</label>
            <input id="ai-smart" value={modelSmart} onChange={e => setModelSmart(e.target.value)} />
          </div>
        </div>
        <div className="row">
          <button type="button" className="btn-primary" disabled={aiBusy} onClick={() => void saveAi()}>
            {aiBusy ? '保存中…' : '保存'}
          </button>
          {aiNote ? <span className="small faint">{aiNote}</span> : null}
        </div>
      </Card>

      {!settings.openai_configured ? (
        <Alert tone="warn">
          未检测到 <code className="mono">OPENAI_API_KEY</code>。在项目根目录复制{' '}
          <code className="mono">.env.example</code> 为 <code className="mono">.env</code>，填入密钥后重启后端。
        </Alert>
      ) : null}

      <div className="grid grid-2">
        <Card title="OpenAI 配置">
          <dl className="meta-list">
            <dt>API Key</dt>
            <dd>{settings.openai_configured ? '已配置（值不会显示）' : '未配置'}</dd>
            <dt>批量分析模型</dt>
            <dd className="mono">{settings.model_fast}</dd>
            <dt>高质量模型</dt>
            <dd className="mono">{settings.model_smart}</dd>
            <dt>提示词版本</dt>
            <dd className="mono">{settings.prompt_version}</dd>
            <dt>单次分析上限</dt>
            <dd>{settings.max_analyses_per_run} 个岗位</dd>
          </dl>
          <div className="field-hint mt-1">
            模型 ID 来自环境变量 OPENAI_MODEL_FAST / OPENAI_MODEL_SMART，换模型不需要改代码。
          </div>
        </Card>

        <Card title="运行环境">
          <dl className="meta-list">
            <dt>版本</dt>
            <dd>v{settings.version}</dd>
            <dt>数据库</dt>
            <dd className="mono">{settings.database_url}</dd>
            <dt>策略文件</dt>
            <dd className="mono">{settings.strategy_path}</dd>
            <dt>自动投递</dt>
            <dd>{settings.auto_apply ? '开启' : '关闭'}</dd>
          </dl>
        </Card>
      </div>

      <Card
        title="批量分析"
        sub={`一次最多分析 ${settings.max_analyses_per_run} 个尚未分析的岗位`}
        actions={
          <button
            type="button"
            className="btn-primary btn-sm"
            disabled={batchRunning || !settings.openai_configured}
            onClick={() => void runBatch()}
          >
            {batchRunning ? '分析中…' : '分析全部未分析岗位'}
          </button>
        }
      >
        <p className="muted mt-0">
          使用批量模型 <code className="mono">{settings.model_fast}</code> 逐个分析。已缓存的结果会直接复用，
          不会重复消耗额度。
        </p>
        {batchResult ? (
          <Alert tone={batchResult.failed > 0 ? 'warn' : 'success'}>
            待处理 {batchResult.requested} 个 · 本次分析 {batchResult.analyzed} 个 · 命中缓存{' '}
            {batchResult.cached} 个 · 失败 {batchResult.failed} 个（上限 {batchResult.limit}）
            {batchResult.failed > 0 ? (
              <ul className="bullet-list mt-1">
                {batchResult.items
                  .filter((item) => !item.ok)
                  .slice(0, 5)
                  .map((item) => (
                    <li key={item.job_id}>
                      岗位 #{item.job_id}：{item.error}
                    </li>
                  ))}
              </ul>
            ) : null}
          </Alert>
        ) : null}
      </Card>

      <Card title="安全策略">
        <ul className="bullet-list">
          <li>OpenAI API Key 只存在于后端进程，不写入数据库、不返回给前端、不打印到日志。</li>
          <li>
            不存在全局自动投递模式。M6 只允许一个岗位经双重人工确认后尝试一次首次招呼；
            不批量、不后台、不自动重试，也不发送任何后续消息。
          </li>
          <li>不绕过验证码、反爬机制、登录保护或频率限制；不保存招聘网站账号密码。</li>
          <li>AI 只负责推荐，绝不能生成确认或触发投递；每个 M6 动作都必须由本人逐岗位最终确认。</li>
          <li>简历原文保存在本机 SQLite 中；调用模型时只发送结构化摘要与必要节选。</li>
        </ul>
      </Card>
    </>
  )
}
