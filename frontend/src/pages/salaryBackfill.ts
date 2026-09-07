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
export async function startFullSalaryBackfill(
  //: Let it keep going with the BOSS tab behind other windows (authorized
  //: 2026-09-06). The tab must still exist and still be BOSS; only "in front"
  //: is dropped, and the strictly-foreground screenshot OCR is skipped rather
  //: than attempted - it recovered 4 salaries where re-reading the detail page
  //: recovered 775.
  background = false,
): Promise<{ ok: boolean; message: string }> {
  // An unfinished run blocks every new one - `create_run` refuses while any
  // is pending/running/paused. A run paused by a worker error on 2026-09-06
  // therefore silently swallowed every automatic backfill after it, and 120
  // consecutive new jobs came in with no salary while the console said
  // nothing. Deal with the old run first, in the open.
  const active = await api.getActiveSalaryBackfillRun()
  if (active) {
    const left = active.items.filter(item => item.state === 'pending').length
    if (left > 0) {
      // Still has work: continue it rather than abandoning what it recorded.
      await api.authorizeSalaryBackfillRemaining(active.id)
      const resumed = await consoleExtension(
        'start-salary-backfill', undefined, undefined, undefined, active.id,
        undefined, undefined, background,
      )
      return resumed.ok
        ? { ok: true, message: `继续上次未完成的薪资补全，还剩 ${left} 个岗位。` }
        : { ok: false, message: resumed.error || '上次的补全计划未能继续，请在「更多功能」里查看。' }
    }
    // Nothing left in it - it only stands in the way of a fresh plan.
    await api.cancelSalaryBackfillRun(active.id)
  }

  const plan = await api.getSalaryBackfillPlan()
  if (!plan.eligible_jobs) {
    return { ok: false, message: '没有可回填的岗位。' }
  }
  const created = await api.createSalaryBackfillRun(plan)
  await api.authorizeSalaryBackfillRemaining(created.id)
  const reply = await consoleExtension(
    'start-salary-backfill', undefined, undefined, undefined, created.id,
    undefined, undefined, background,
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
