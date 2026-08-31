import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/api/client'
import { Card } from '@/components/ui'
import type { AutoMatchReview } from '@/types'

/** Read-only derived view. Mounting/refreshing never plans or starts paid calls. */
export default function AutoMatchReviewPanel({ taskId }: { taskId: number }) {
  const [data, setData] = useState<AutoMatchReview | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let current = true
    setError('')
    api.getAutoMatchReview(taskId).then(result => {
      if (current) setData(result)
    }).catch(() => { if (current) setError('无法读取自动匹配结果，请刷新重试（不会调用 AI）。') })
    return () => { current = false }
  }, [taskId, refresh])
  return <Card title="自动匹配 · 人工复核" sub="只读结果；AI 建议不代表已投递或已复核。">
    <button className="btn btn-secondary" onClick={() => setRefresh(n => n + 1)}>刷新匹配结果（免费）</button>
    {error && <p role="alert">{error}</p>}
    {data?.enabled ? <>
      <p>状态：{data.state} · 简历 #{data.resume_id} · {data.model} · 名额 {data.used}/{data.cap} · 完成 {data.completed} · 失败 {data.failed} · 结果未确认 {data.uncertain}</p>
      <p>未确认/失败的调用不会自动重试。暂停或取消不能撤销已发出的模型调用。</p>
      {data.items.length === 0 && <p>本轮尚无匹配结果。</p>}
      {data.items.map(item => <section key={item.job_id} className="mt-3">
        <h3>{item.bucket} · <Link to={`/jobs/${item.job_id}`}>{item.title}</Link> · {item.company}</h3>
        <p>匹配分：{item.score ?? '未得出'} · 模型建议：{item.verdict ?? '无'} · {item.state}{item.cached ? '（缓存）' : ''}</p>
        <p>{item.summary}</p>
        {item.error && <p role="alert">{item.error}</p>}
        {item.review_reasons.map(reason => <p key={reason}>待核实：{reason}</p>)}
      </section>)}
    </> : <p>本任务未启用自动匹配。可在扩展启动新搜索计划时勾选，并明确确认最多 3 个岗位的 AI 费用。</p>}
  </Card>
}
