import { useCallback, useEffect, useState } from 'react'

import { ApiError, api } from '@/api/client'
import { Alert, Card, Loading } from '@/components/ui'
import type { AppSettings, BatchAnalyzeResponse } from '@/types'

export default function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [batchRunning, setBatchRunning] = useState(false)
  const [batchResult, setBatchResult] = useState<BatchAnalyzeResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setSettings(await api.settings())
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
            <code className="mono">AUTO_APPLY</code> 恒为 false：v0.1 不做自动投递，也不自动发送招呼语。
          </li>
          <li>不绕过验证码、反爬机制、登录保护或频率限制；不保存招聘网站账号密码。</li>
          <li>AI 只负责推荐，投递与沟通全部由本人确认后手动完成。</li>
          <li>简历原文保存在本机 SQLite 中；调用模型时只发送结构化摘要与必要节选。</li>
        </ul>
      </Card>
    </>
  )
}
