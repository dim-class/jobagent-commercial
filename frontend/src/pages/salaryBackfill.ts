import { api } from '@/api/client'
import { consoleExtension } from '@/pages/consoleExtension'

/** Create a backfill run covering every eligible job, authorise the whole of
 *  it, and start it - the three calls that were only reachable through
 *  更多功能 → 历史缺薪回填 → 准备并处理全部.
 *
 *  Shared rather than duplicated: the console prompt and the panel now offer
 *  the same action, and two copies of a sequence that authorises browser work
 *  would eventually disagree about what it authorises.
 *
 *  Still one explicit human click, and still every stop condition: login,
 *  verification, foreground loss, manual pause and worker error all halt it.
 *  Raising the session cap buys continuity, never permission.
 */
export async function startFullSalaryBackfill(): Promise<{ ok: boolean; message: string }> {
  const plan = await api.getSalaryBackfillPlan()
  if (!plan.eligible_jobs) {
    return { ok: false, message: '没有可回填的岗位。' }
  }
  const created = await api.createSalaryBackfillRun(plan)
  await api.authorizeSalaryBackfillRemaining(created.id)
  const reply = await consoleExtension(
    'start-salary-backfill', undefined, undefined, undefined, created.id,
  )
  if (!reply.ok) {
    // The run exists and is authorised; only the browser side failed to start.
    // Say so rather than implying nothing happened - it can be resumed.
    return { ok: false, message: reply.error || '计划已创建，但浏览器未启动，可在「更多功能」里恢复。' }
  }
  return {
    ok: true,
    message: `已开始补全 ${created.total_jobs} 个岗位的薪资；登录失效、验证页或前台丢失会自动暂停。`,
  }
}
