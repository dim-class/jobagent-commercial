import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import { Alert, Card, EmptyState, Loading, ScoreBadge, VerdictBadge } from '@/components/ui'
import type { CrossTaskMatchPlanOut, CrossTaskMatchRunResponse, SearchPlanTask } from '@/types'

export default function CrossTaskMatchPanel() {
  const [tasks, setTasks] = useState<SearchPlanTask[]>([])
  const [selected, setSelected] = useState<number[]>([])
  const [loading, setLoading] = useState(true)
  const [planning, setPlanning] = useState(false)
  const [running, setRunning] = useState(false)
  const [plan, setPlan] = useState<CrossTaskMatchPlanOut | null>(null)
  const [result, setResult] = useState<CrossTaskMatchRunResponse | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    api.listSearchPlan()
      .then(response => { if (alive) setTasks(response.items.filter(task => task.state === 'completed')) })
      .catch(err => { if (alive) setError(err instanceof ApiError ? err.message : '加载已完成任务失败') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  function toggle(taskId: number) {
    setSelected(current => current.includes(taskId)
      ? current.filter(id => id !== taskId)
      : [...current, taskId])
    setPlan(null)
    setResult(null)
    setError('')
  }

  function replaceSelection(taskIds: number[]) {
    setSelected(taskIds)
    setPlan(null)
    setResult(null)
    setError('')
  }

  async function buildPlan() {
    if (!selected.length) { setError('请先选择至少一个已完成的搜索任务。'); return }
    setPlanning(true); setError(''); setResult(null)
    try { setPlan(await api.getCrossTaskMatchPlan(selected)) }
    catch (err) { setPlan(null); setError(err instanceof ApiError ? err.message : '生成跨任务匹配计划失败') }
    finally { setPlanning(false) }
  }

  async function confirmRun() {
    if (!plan || plan.max_new_calls < 1) return
    setRunning(true); setError('')
    try {
      const response = await api.runCrossTaskMatch(plan)
      setResult(response)
      setPlan(response.plan)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '跨任务匹配失败；未自动重试')
    } finally { setRunning(false) }
  }

  return (
    <Card
      title="跨任务匹配与统一人工复核（M5b）"
      sub="只读取已完成 SearchPlan 的现有候选岗位；跨任务去重，不操作 Chrome、不投递"
    >
      {loading ? <Loading /> : tasks.length === 0 ? (
        <EmptyState icon="🧭" title="还没有已完成的搜索任务" text="先完成一个有界 SearchPlan，之后可在这里统一匹配。" />
      ) : (
        <>
          <div className="field">
            <label>选择精确的已完成任务（最多 20 个）</label>
            <div className="btn-row mb-1">
              <button
                type="button"
                className="btn-sm"
                disabled={selected.length === Math.min(tasks.length, 20)}
                onClick={() => replaceSelection(tasks.slice(0, 20).map(task => task.id))}
              >
                全选{tasks.length > 20 ? '前 20 个' : `全部 ${tasks.length} 个`}
              </button>
              <button
                type="button"
                className="btn-sm"
                disabled={selected.length === 0}
                onClick={() => replaceSelection([])}
              >
                清空选择
              </button>
              <span className="small faint">已选择 {selected.length} 个</span>
            </div>
            <div className="btn-row">
              {tasks.map(task => (
                <label className="checkbox small" key={task.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(task.id)}
                    disabled={!selected.includes(task.id) && selected.length >= 20}
                    onChange={() => toggle(task.id)}
                  />
                  #{task.id} {task.city ?? '—'} · {task.keywords ?? '—'}
                </label>
              ))}
            </div>
          </div>
          <button type="button" className="btn-sm" disabled={planning || !selected.length} onClick={() => void buildPlan()}>
            {planning ? '生成中…' : '生成只读匹配计划'}
          </button>
        </>
      )}

      {error ? <Alert tone="error">{error}</Alert> : null}

      {plan ? (
        <div className="card-block mt-1">
          <div className="small">
            任务 {plan.task_count} 个 · 去重后岗位 {plan.unique_jobs} 个 · 缓存 {plan.cached_jobs} 个 ·
            待分析 {plan.pending_jobs} 个<br />
            简历：{plan.active_resume_name} · fast 模型：{plan.model}
          </div>
          {plan.max_new_calls > 0 ? (
            <div className="mt-1">
              <Alert tone="warn">
                本次确认最多新增 {plan.max_new_calls} 次 fast-model 调用（全批次上限 3；失败也占名额）。
                这是独立费用确认，不会自动重试。
              </Alert>
              <button type="button" className="btn btn-sm" disabled={running || result !== null} onClick={() => void confirmRun()}>
                {running ? '匹配中…' : result ? '如需继续，请重新生成计划' : `确认最多 ${plan.max_new_calls} 次新调用`}
              </button>
            </div>
          ) : <div className="small faint mt-1">全部命中现有缓存，不需要付费调用。</div>}

          {result ? (
            <div className="small faint mt-1">
              本次占用 {result.calls_used} 个名额 · 新完成 {result.analyzed} 个 · 失败 {result.failed} 个
              {result.failed ? '；失败项未自动重试。' : ''}
            </div>
          ) : null}

          {plan.items.length === 0 ? (
            <EmptyState icon="📭" title="所选任务没有候选岗位" text="没有模型调用，也没有写入。" />
          ) : (
            <div className="table-wrap mt-1">
              <table>
                <thead><tr><th>匹配</th><th>公司 / 职位</th><th>条件</th><th>来源任务</th><th>人工复核</th><th>操作</th></tr></thead>
                <tbody>{plan.items.map(item => {
                  const failure = result?.results.find(row => row.job_id === item.job_id)?.error
                  return (
                    <tr key={item.job_id}>
                      <td className="nowrap"><ScoreBadge score={item.score} /><div><VerdictBadge verdict={item.verdict} /></div></td>
                      <td><div className="cell-title">{item.company}</div><div className="cell-sub">{item.title}</div></td>
                      <td className="small">{item.city ?? '城市缺失'} · {item.salary_text ?? '薪资缺失'}<br />{item.experience_text ?? '经验缺失'} · {item.education_text ?? '学历缺失'}</td>
                      <td className="small">{item.sources.map(source => `#${source.task_id} ${source.name}`).join('；')}</td>
                      <td><span className="badge badge-neutral">{item.bucket}</span>{item.review_reasons.map(reason => <div className="small faint" key={reason}>{reason}</div>)}{failure ? <div className="small text-danger">{failure}</div> : null}</td>
                      <td><Link className="btn btn-sm" to={`/jobs/${item.job_id}`}>查看详情</Link></td>
                    </tr>
                  )
                })}</tbody>
              </table>
            </div>
          )}
        </div>
      ) : null}
    </Card>
  )
}
