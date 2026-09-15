import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { announceAiSettingsSaved } from '@/api/aiSettingsEvents'
import { ApiError, api } from '@/api/client'
import { Alert } from '@/components/ui'

/** Shown on every page while no API key is configured, with the key field in
 *  it. The banner it replaces told the user to fill in `.env` and restart the
 *  backend - an instruction nobody using the finished product should need, and
 *  it named a different file from the one the settings page wrote. A different
 *  provider or model needs two more fields, which live in 设置. */
export default function AiKeyBanner() {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')

  async function save(event: FormEvent) {
    event.preventDefault()
    const typed = key.trim()
    if (busy || !typed) return
    setBusy(true); setProblem('')
    try {
      const saved = await api.saveAiSettings({ api_key: typed })
      setKey('')
      if ((saved.overridden ?? []).includes('OPENAI_API_KEY')) {
        setProblem('已写入，但后端进程里设置了同名环境变量 OPENAI_API_KEY，它优先，这次保存的 Key 暂时不会被使用。')
      }
      announceAiSettingsSaved()
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Alert tone="warn">
      <form className="row" onSubmit={event => void save(event)}>
        <span>还没有配置 AI 接口，AI 匹配分析暂不可用；搜索和采集不受影响。</span>
        <input
          type="password"
          autoComplete="off"
          aria-label="API Key"
          placeholder="粘贴 API Key"
          value={key}
          onChange={event => setKey(event.target.value)}
        />
        <button type="submit" className="btn-primary btn-sm" disabled={busy || !key.trim()}>
          {busy ? '保存中…' : '保存'}
        </button>
        <Link to="/settings" className="small">换服务商或模型</Link>
      </form>
      {problem ? <p className="small mt-1" role="alert">{problem}</p> : null}
    </Alert>
  )
}
