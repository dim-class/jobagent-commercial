import { useEffect, useState } from 'react'

import { api, ApiError } from '@/api/client'
import { consoleExtension } from '@/pages/consoleExtension'
import type { SalaryBackfillPlan, SalaryBackfillRun } from '@/types'

export default function SalaryBackfillPanel() {
  const [plan, setPlan] = useState<SalaryBackfillPlan | null>(null)
  const [run, setRun] = useState<SalaryBackfillRun | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  async function refresh() {
    const [nextPlan, active] = await Promise.all([
      api.getSalaryBackfillPlan(), api.getActiveSalaryBackfillRun(),
    ])
    setPlan(nextPlan)
    if (active) setRun(active)
    else if (run) setRun(await api.getSalaryBackfillRun(run.id))
  }

  useEffect(() => { void refresh().catch(() => setMessage('无法读取薪资回填状态。')) }, [])

  async function act(action: 'start-salary-backfill' | 'resume-salary-backfill'
    | 'pause-salary-backfill' | 'cancel-salary-backfill') {
    if (!run) return
    setBusy(true)
    try {
      const reply = await consoleExtension(action, undefined, undefined, undefined, run.id)
      if (!reply.ok) throw new Error(reply.error || '扩展拒绝了操作。')
      setMessage('操作已确认，请刷新进度。')
      await refresh()
    } catch (error) {
      setMessage(error instanceof ApiError || error instanceof Error ? error.message : '操作失败。')
    } finally { setBusy(false) }
  }

  // `all` raises this one run's session cap to its whole remaining set before the
  // first start, so a small plan runs as one continuous session instead of a
  // string of three-job batches. It buys continuity, never permission: login,
  // verification, foreground loss, worker error and manual pause still stop it.
  async function create(all: boolean) {
    if (!plan || plan.eligible_jobs < 1) return
    const confirmation = all
      ? `确认创建并连续处理全部 ${plan.eligible_jobs} 个历史缺薪岗位？\n`
        + '登录失效、验证页、前台丢失、手动暂停或错误仍会立即停止。'
      : `确认创建 ${plan.eligible_jobs} 个历史缺薪岗位的回填计划？\n每批最多 3 个，每批后自动暂停。`
    if (!window.confirm(confirmation)) return
    setBusy(true)
    try {
      const created = await api.createSalaryBackfillRun(plan)
      setRun(created)
      if (all) await api.authorizeSalaryBackfillRemaining(created.id)
      const reply = await consoleExtension('start-salary-backfill', undefined, undefined, undefined, created.id)
      if (!reply.ok) setMessage(reply.error || '计划已创建，但未启动。')
      else setMessage(all
        ? `已启动，本次连续处理全部 ${created.total_jobs} 个；异常时会自动暂停。`
        : '首批已启动，最多处理 3 个。')
      await refresh()
    } catch (error) { setMessage(error instanceof Error ? error.message : '创建失败。') }
    finally { setBusy(false) }
  }

  /** One explicit click authorizes the whole remainder of this run, then runs it. */
  async function runRemaining(action: 'start-salary-backfill' | 'resume-salary-backfill') {
    if (!run) return
    const remaining = run.total_jobs - run.processed_jobs
    if (remaining < 1 || !window.confirm(
      `确认本次连续处理剩余 ${remaining} 个岗位？\n登录失效、验证页、手动暂停或错误仍会立即停止。`,
    )) return
    setBusy(true)
    try {
      await api.authorizeSalaryBackfillRemaining(run.id)
      const reply = await consoleExtension(action, undefined, undefined, undefined, run.id)
      if (!reply.ok) throw new Error(reply.error || '扩展拒绝了操作。')
      setMessage(`已授权连续处理剩余 ${remaining} 个；异常时会自动暂停。`)
      await refresh()
    } catch (error) {
      setMessage(error instanceof ApiError || error instanceof Error ? error.message : '操作失败。')
    } finally { setBusy(false) }
  }

  const remaining = run ? run.total_jobs - run.processed_jobs : 0
  const continuous = !!run && run.session_cap > 3

  return <section id="salary-backfill" className="card" style={{ marginTop: 16 }}>
    <h2>历史缺薪回填</h2>
    <p>只处理已入库的 BOSS 详情链接；通过现有 canonical intake 补薪，不新建岗位。</p>
    {plan && <p>岗位 {plan.total_jobs} 个 · 已有薪资 {plan.salary_present} 个 ·
      可回填 {plan.eligible_jobs} 个 · 不可回填 {plan.ineligible_jobs} 个</p>}
    {run ? <>
      <p>计划 #{run.id}：{run.state} · {run.processed_jobs}/{run.total_jobs} ·
        成功 {run.updated_jobs} · 无法读取 {run.unavailable_jobs} · 失败 {run.failed_jobs}
        · 本次授权 {run.session_processed}/{run.session_cap}
        {run.paused_reason ? ` · 暂停：${run.paused_reason}` : ''}</p>
      {run.state === 'paused' && (continuous
        ? <button disabled={busy} onClick={() => void act('resume-salary-backfill')}>
          继续处理剩余 {remaining} 个
        </button>
        : <>
          <button disabled={busy} onClick={() => void act('resume-salary-backfill')}>继续下一批 3 个</button>
          <button disabled={busy} onClick={() => void runRemaining('resume-salary-backfill')}>
            处理剩余全部 {remaining} 个
          </button>
        </>)}
      {run.state === 'pending' && <>
        <button disabled={busy} onClick={() => void act('start-salary-backfill')}>
          {continuous ? `开始（本次授权 ${run.session_cap} 个）` : '开始首批 3 个'}
        </button>
        {!continuous && <button disabled={busy} onClick={() => void runRemaining('start-salary-backfill')}>
          直接处理全部 {remaining} 个
        </button>}
      </>}
      {run.state === 'running' && <button disabled={busy} onClick={() => void act('pause-salary-backfill')}>暂停</button>}
      {['pending', 'running', 'paused'].includes(run.state) && <button disabled={busy}
        onClick={() => void act('cancel-salary-backfill')}>取消计划</button>}
    </> : <>
      <button disabled={busy || !plan?.eligible_jobs} onClick={() => void create(true)}>
        准备并处理全部 {plan?.eligible_jobs ?? 0} 个
      </button>
      <button disabled={busy || !plan?.eligible_jobs} onClick={() => void create(false)}>
        每批 3 个
      </button>
    </>}
    <button disabled={busy} onClick={() => void refresh()}>刷新进度</button>
    {message && <p>{message}</p>}
    <p className="small faint">
      启动前请把同一 Chrome 窗口里的 BOSS 标签页停在<strong>搜索结果页</strong>
      （<code>/web/geek/job</code> 或 <code>/web/geek/recommend</code>）或<strong>职位详情页</strong>
      （<code>/job_detail/…</code>）；首页、聊天页、登录页与验证页不会被采用，扩展会直接拒绝启动。
    </p>
  </section>
}
