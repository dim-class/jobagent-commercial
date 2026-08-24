import { useCallback, useEffect, useState } from 'react'

import { ApiError, api } from '@/api/client'
import { Alert, Card, Loading } from '@/components/ui'
import type { CareerStrategy } from '@/types'

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

/** A list of strings edited as removable chips plus an "add" input. */
function TagEditor({
  label,
  hint,
  values,
  placeholder,
  onChange,
}: {
  label: string
  hint?: string
  values: string[]
  placeholder: string
  onChange: (next: string[]) => void
}) {
  const [draft, setDraft] = useState('')

  function add() {
    const value = draft.trim()
    if (!value || values.includes(value)) {
      setDraft('')
      return
    }
    onChange([...values, value])
    setDraft('')
  }

  return (
    <div className="field">
      <label>{label}</label>
      <div className="tag-editor">
        {values.length === 0 ? <span className="faint small">（空）</span> : null}
        {values.map((value) => (
          <span key={value} className="chip">
            {value}
            <button
              type="button"
              className="tag-remove"
              aria-label={`移除 ${value}`}
              onClick={() => onChange(values.filter((v) => v !== value))}
            >
              ✕
            </button>
          </span>
        ))}
      </div>
      <div className="row">
        <input
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
        />
        <button type="button" className="btn-sm" onClick={add}>
          添加
        </button>
      </div>
      {hint ? <div className="field-hint">{hint}</div> : null}
    </div>
  )
}

export default function StrategyPage() {
  const [strategy, setStrategy] = useState<CareerStrategy | null>(null)
  const [path, setPath] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const response = await api.getStrategy()
      setStrategy(response.strategy)
      setPath(response.path)
      setDirty(false)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载求职策略失败' })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  function patch(changes: Partial<CareerStrategy>) {
    setStrategy((prev) => (prev ? { ...prev, ...changes } : prev))
    setDirty(true)
  }

  async function save() {
    if (!strategy) return
    setSaving(true)
    try {
      const response = await api.saveStrategy(strategy)
      setStrategy(response.strategy)
      setDirty(false)
      setFeedback({
        tone: 'success',
        text: '策略已保存。它是分析缓存键的一部分，已分析的岗位下次会重新分析。',
      })
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '保存失败' })
    } finally {
      setSaving(false)
    }
  }

  if (loading && !strategy) return <Loading text="正在加载求职策略…" />
  if (!strategy) return <Alert tone="error">无法加载求职策略。</Alert>

  return (
    <>
      <header className="page-head">
        <div>
          <h1>求职策略</h1>
          <p>
            决定「什么是好岗位」。保存后会写回 <code className="mono">{path}</code>，也可以直接编辑该
            YAML 文件。
          </p>
        </div>
        <div className="page-actions">
          <button type="button" onClick={() => void load()} disabled={saving}>
            放弃修改
          </button>
          <button type="button" className="btn-primary" onClick={() => void save()} disabled={saving || !dirty}>
            {saving ? '保存中…' : dirty ? '保存策略' : '已保存'}
          </button>
        </div>
      </header>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      <div className="grid grid-2">
        <Card title="目标城市">
          <TagEditor
            label="城市列表"
            hint="按优先级排列，第一个最优先。"
            values={strategy.target_cities}
            placeholder="例如：北京"
            onChange={(target_cities) => patch({ target_cities })}
          />
          <div className="checkbox-row">
            <input
              id="remote"
              type="checkbox"
              checked={strategy.remote_ok}
              onChange={(e) => patch({ remote_ok: e.target.checked })}
            />
            <label htmlFor="remote">接受远程岗位（不受城市限制）</label>
          </div>
        </Card>

        <Card title="经验与薪资偏好">
          <div className="field-row">
            <div className="field">
              <label htmlFor="exp-min">偏好最低年限</label>
              <input
                id="exp-min"
                type="number"
                min={0}
                value={strategy.experience_policy.preferred_min_years}
                onChange={(e) =>
                  patch({
                    experience_policy: {
                      ...strategy.experience_policy,
                      preferred_min_years: Number(e.target.value),
                    },
                  })
                }
              />
            </div>
            <div className="field">
              <label htmlFor="exp-max">偏好最高年限</label>
              <input
                id="exp-max"
                type="number"
                min={0}
                value={strategy.experience_policy.preferred_max_years}
                onChange={(e) =>
                  patch({
                    experience_policy: {
                      ...strategy.experience_policy,
                      preferred_max_years: Number(e.target.value),
                    },
                  })
                }
              />
            </div>
            <div className="field">
              <label htmlFor="exp-flex">宽松度</label>
              <select
                id="exp-flex"
                value={strategy.experience_policy.flexibility}
                onChange={(e) =>
                  patch({
                    experience_policy: { ...strategy.experience_policy, flexibility: e.target.value },
                  })
                }
              >
                <option value="strict">严格</option>
                <option value="balanced">平衡</option>
                <option value="lenient">宽松</option>
              </select>
            </div>
          </div>
          <div className="field-hint mb-1">
            超出偏好年限的岗位不会被自动拒绝；只有 JD 写明硬性门槛时才作为硬性条件。
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="sal-min">最低期望月薪（元）</label>
              <input
                id="sal-min"
                type="number"
                min={0}
                step={1000}
                value={strategy.salary.min_monthly_cny}
                onChange={(e) =>
                  patch({ salary: { ...strategy.salary, min_monthly_cny: Number(e.target.value) } })
                }
              />
            </div>
            <div className="field">
              <label htmlFor="sal-ideal">理想月薪（元）</label>
              <input
                id="sal-ideal"
                type="number"
                min={0}
                step={1000}
                value={strategy.salary.ideal_monthly_cny}
                onChange={(e) =>
                  patch({ salary: { ...strategy.salary, ideal_monthly_cny: Number(e.target.value) } })
                }
              />
            </div>
          </div>
        </Card>
      </div>

      <Card title="目标岗位方向">
        <TagEditor
          label="岗位名称关键词"
          hint="不区分大小写的子串匹配，因此「Cloud Engineer」也会命中「Senior Cloud Engineer」。"
          values={strategy.preferred_roles}
          placeholder="例如：Platform Engineer"
          onChange={(preferred_roles) => patch({ preferred_roles })}
        />
      </Card>

      <Card title="相关技能">
        <TagEditor
          label="技能清单"
          hint="用于计算 JD 与简历的技能重合度，会在调用模型之前先算好。"
          values={strategy.relevant_skills}
          placeholder="例如：Terraform"
          onChange={(relevant_skills) => patch({ relevant_skills })}
        />
      </Card>

      <Card title="排除关键词">
        <TagEditor
          label="需要规避的岗位关键词"
          hint="命中后大幅扣分；若同时具备足够的云/自动化技能重合，会被判定为「可豁免」。"
          values={strategy.excluded_keywords}
          placeholder="例如：桌面运维"
          onChange={(excluded_keywords) => patch({ excluded_keywords })}
        />
        <TagEditor
          label="可豁免技能"
          hint="出现这些技能时，排除关键词的影响会被减弱（例如技术型售前）。"
          values={strategy.excluded_soft_override_skills}
          placeholder="例如：Kubernetes"
          onChange={(excluded_soft_override_skills) => patch({ excluded_soft_override_skills })}
        />
      </Card>

      <Card title="补充说明">
        <div className="field">
          <label htmlFor="notes">发给模型的额外说明</label>
          <textarea
            id="notes"
            rows={4}
            value={String(strategy.preferences?.notes ?? '')}
            placeholder="例如：希望从云运维向平台工程方向发展。"
            onChange={(e) => patch({ preferences: { ...strategy.preferences, notes: e.target.value } })}
          />
          <div className="field-hint">这段文字会原样出现在提示词里，尽量简短具体。</div>
        </div>
      </Card>
    </>
  )
}
