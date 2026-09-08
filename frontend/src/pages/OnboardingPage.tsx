import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import { Alert, Card, Loading } from '@/components/ui'
import type { CareerStrategy, ResumeDetail, SearchPlanOptions } from '@/types'

function splitValues(value: string): string[] {
  return [...new Set(value.split(/[，,\n]/).map((item) => item.trim()).filter(Boolean))]
}

export default function OnboardingPage() {
  const fileInput = useRef<HTMLInputElement>(null)
  const [strategy, setStrategy] = useState<CareerStrategy | null>(null)
  const [options, setOptions] = useState<SearchPlanOptions | null>(null)
  const [resume, setResume] = useState<ResumeDetail | null>(null)
  const [roles, setRoles] = useState('')
  const [skills, setSkills] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [strategyResponse, optionResponse] = await Promise.all([
        api.getStrategy(),
        api.getSearchPlanOptions(),
      ])
      setStrategy(strategyResponse.strategy)
      setOptions(optionResponse)
      setRoles(strategyResponse.strategy.preferred_roles.join('\n'))
      setSkills(strategyResponse.strategy.relevant_skills.join('\n'))
      try {
        setResume(await api.getActiveResume())
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 404)) throw err
        setResume(null)
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '加载首次设置失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  async function upload(file: File) {
    setBusy(true); setError(''); setMessage('正在本地解析简历…')
    try {
      await api.uploadResume(file)
      const active = await api.getActiveResume()
      setResume(active)
      if (!skills.trim()) {
        const parsedSkills = active.parsed_profile?.skills
        if (Array.isArray(parsedSkills)) setSkills(parsedSkills.join('\n'))
      }
      setMessage('简历已保存。请继续确认城市和岗位方向。')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '上传简历失败')
      setMessage('')
    } finally {
      setBusy(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  function toggleCity(city: string, checked: boolean) {
    if (!strategy || !options) return
    const current = strategy.target_cities.filter((item) => options.supported_cities.includes(item))
    if (checked && current.length >= options.max_selected_cities) {
      setError(`最多选择 ${options.max_selected_cities} 个城市。`)
      return
    }
    setError('')
    setStrategy({
      ...strategy,
      target_cities: checked ? [...current, city] : current.filter((item) => item !== city),
    })
  }

  async function save() {
    if (!strategy || !options) return
    const preferredRoles = splitValues(roles)
    const supportedCities = strategy.target_cities.filter((city) => options.supported_cities.includes(city))
    if (!resume) { setError('请先上传一份简历。'); return }
    if (!supportedCities.length) { setError('请至少选择一个城市。'); return }
    if (!preferredRoles.length) { setError('请至少填写一个岗位方向。'); return }
    setBusy(true); setError(''); setMessage('')
    try {
      const response = await api.saveStrategy({
        ...strategy,
        target_cities: supportedCities,
        preferred_roles: preferredRoles,
        relevant_skills: splitValues(skills),
      })
      setStrategy(response.strategy)
      setMessage('首次设置已完成。现在可以按这些偏好搜索岗位。')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '保存设置失败')
    } finally { setBusy(false) }
  }

  if (loading) return <Loading text="正在加载个人设置…" />
  const ready = !!resume && !!strategy?.target_cities.some((city) => options?.supported_cities.includes(city))
    && splitValues(roles).length > 0

  return <>
    <header className="page-head"><div><h1>开始使用 JobAgent</h1>
      <p>只需完成一次：上传简历、选择城市、填写岗位方向。以后可随时修改。</p></div></header>
    {error ? <Alert tone="error" onDismiss={() => setError('')}>{error}</Alert> : null}
    {message ? <Alert tone="success" onDismiss={() => setMessage('')}>{message}</Alert> : null}
    <div className="grid grid-2">
      <Card title="1. 当前简历" sub="文件只保存在本机；搜索不会把完整简历发送给招聘网站。">
        <p>{resume ? `已选择：${resume.label}` : '尚未上传简历'}</p>
        <input ref={fileInput} type="file" accept=".pdf,.docx,.txt" style={{ display: 'none' }}
          onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file) }} />
        <div className="btn-row"><button className="btn btn-secondary" disabled={busy}
          onClick={() => fileInput.current?.click()}>{resume ? '更换简历' : '上传简历'}</button>
          <Link className="btn btn-secondary" to="/resume">管理简历版本</Link></div>
      </Card>
      <Card title="2. 求职目标" sub="搜索只使用这里保存的城市和岗位方向。">
        <label>当前求职阶段<select value={strategy?.early_career_policy ?? 'include'}
          onChange={(event) => strategy && setStrategy({
            ...strategy,
            early_career_policy: event.target.value as CareerStrategy['early_career_policy'],
          })}>
          <option value="exclude">社招 / 有经验（排除应届、校招、实习）</option>
          <option value="include">两类都看</option>
          <option value="only">应届 / 在校（只看应届、校招、实习）</option>
        </select></label>
        <fieldset className="choice-group"><legend>意向城市（最多 {options?.max_selected_cities ?? 4} 个）</legend>
          <div className="row">{options?.supported_cities.map((city) => <label key={city} className="check-label">
            <input type="checkbox" checked={strategy?.target_cities.includes(city) ?? false}
              onChange={(event) => toggleCity(city, event.target.checked)} />{city}</label>)}</div>
        </fieldset>
        <label>岗位方向（每行一个）<textarea rows={5} value={roles}
          placeholder={'例如：\n后端工程师\n数据分析师'} onChange={(event) => setRoles(event.target.value)} /></label>
        <label>关键技能（可选，每行一个）<textarea rows={4} value={skills}
          placeholder={'例如：\nPython\nSQL'} onChange={(event) => setSkills(event.target.value)} /></label>
      </Card>
    </div>
    <Card title="3. 保存并开始">
      <div className="btn-row"><button className="btn btn-primary" disabled={busy || !strategy || !options}
        onClick={() => void save()}>{busy ? '处理中…' : '保存个人设置'}</button>
        {ready ? <Link className="btn btn-secondary" to="/console">前往搜索岗位</Link> : null}</div>
      <p className="small faint">当前仅列出 JobAgent 已验证城市；未知城市不会被替换为默认城市。</p>
    </Card>
  </>
}
