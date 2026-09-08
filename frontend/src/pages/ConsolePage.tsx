import { Link } from 'react-router-dom'

import ConsoleSearchPanel from '@/pages/ConsoleSearchPanel'
import SalaryBackfillPanel from '@/pages/SalaryBackfillPanel'

/**
 * 搜索岗位 - the page the daily workflow actually starts on.
 *
 * It used to carry a second half behind 更多功能, and every part of it had
 * been superseded rather than removed:
 *
 * - **待处理事项** was a second dashboard. `/dashboard` (数据概览) shows the
 *   same five tiles, and the two that matter during a search - 待分析岗位 and
 *   投递队列待处理 - are already inline in the search panel, next to the
 *   buttons that act on them.
 * - **新建任务 / 任务列表 / 关联已有岗位 / 候选人 / 事件记录** was the M5a
 *   manual-task surface: create a task by hand, search the library, associate
 *   jobs into it one at a time, then score them. 搜索 -> 全部分析 -> 投递队列
 *   does all of that in three clicks and is what gets used. Measured before
 *   deleting: of 890 rows in `job_search_tasks` exactly one was made by that
 *   form (2026-08-23, and its name still matches the form's own placeholder);
 *   `orchestration_events` held 2 rows in total, the last on 2026-08-28.
 * - **CrossTaskMatchPanel** (M5b) scored the candidates of several completed
 *   tasks - the same job 全部分析 does from the search panel in one click.
 *
 * The backend keeps all of it: this removed the console surface, not the M5a
 * or M5b endpoints, which are authorized milestones with their own tests.
 *
 * SalaryBackfillPanel stays, and is no longer folded away. The search panel
 * can *start* a backfill; only this panel can resume a paused one, run the
 * whole remainder in one session, or replace a stale plan - and a run that
 * pauses silently is exactly how 120 consecutive jobs once arrived with no
 * salary.
 */
export default function ConsolePage() {
  return (
    <>
      <header className="page-head">
        <div>
          <h1>搜索适合我的岗位</h1>
          <p>选择城市和数量，JobAgent 会按当前简历挑选岗位方向。</p>
        </div>
      </header>

      <ConsoleSearchPanel />

      <div className="row mb-1">
        <Link className="btn btn-primary" to="/jobs">查看岗位库</Link>
      </div>

      <SalaryBackfillPanel />
    </>
  )
}
