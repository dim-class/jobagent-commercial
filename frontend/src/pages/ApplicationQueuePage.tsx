import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import AppliedBackfillPanel from '@/pages/AppliedBackfillPanel'
import { ApiError, api, type QueueFilters } from '@/api/client'
import { consoleExtension } from '@/pages/consoleExtension'
import {
  ResumePicker,
  UNKNOWN_CHOICE,
  useSelectableResumes,
  type ResumeChoice,
} from '@/components/ResumePicker'
import {
  Alert,
  Card,
  EmptyState,
  Loading,
  Modal,
  ScoreBadge,
  StatusBadge,
  VerdictBadge,
  formatDateTime,
} from '@/components/ui'
import type {
  ApplicationApprovalOut,
  BatchAnalyzePlan,
  ApplicationProposal,
  JobStatus,
  QueueResponse,
  Verdict,
} from '@/types'

/** Plain-language readings of an M6 preflight refusal.
 *
 * Several of these are the gate working, not a fault - most often a job that
 * has already been contacted, whose control now reads 继续沟通. Handing the
 * raw status to the reader made a correct refusal look identical to a bug.
 */
const M6_PREFLIGHT_REASON: Record<string, string> = {
  posting_closed:
    'BOSS 显示该职位已关闭，没有执行投递。这个岗位已自动跳过（原因记为「职位已关闭」），'
    + '分析记录都还在；如果是误判，在岗位库里「恢复待处理」即可。',
  posting_closed_not_skipped:
    'BOSS 显示该职位已关闭，没有执行投递。自动跳过没有成功，所以它还在列表里 —— '
    + '请手动点「跳过」，原因写「职位已关闭」。',
  control_wrong_state:
    '这个岗位已经沟通过了（按钮显示「继续沟通」）。JobAgent 只负责第一次投递，'
    + '不会点击已有对话的按钮。换一个还没沟通过的岗位即可。',
  control_missing: '页面上找不到「立即沟通」按钮，可能页面还没加载完或版式变了。',
  control_ambiguous: '页面上有多个「立即沟通」按钮，无法确定该点哪个，已停止。',
  control_disabled: '「立即沟通」按钮处于不可点击状态。',
  login_required: 'BOSS 要求登录。请自己在浏览器里登录后再试。',
  verification: 'BOSS 正在显示安全验证。请自己完成验证 —— JobAgent 不会代你处理。',
  wrong_page: '当前标签页不是岗位详情页。',
  identity_mismatch: '页面上的岗位与本次确认的不是同一个，已停止。',
}

/** Plain-language readings of what happened to the greeting after the click.
 *
 * The click and the greeting are two separate actions (2026-09-03), and only
 * the click was ever reported. On 2026-09-06 four applications in a row went
 * out with no greeting because BOSS navigated the tab to the chat page, and
 * the queue said 「已执行一次立即沟通」 for all of them - the user found out an
 * hour later by looking at BOSS. A skipped greeting is now named, with what to
 * do about it, because the fix is thirty seconds of typing in a conversation
 * that already exists.
 */
const M6_GREETING_REASON: Record<string, string> = {
  left_job_page: '点击后页面既不是岗位详情页也不是聊天页，招呼语没有发出去，请到 BOSS 手动补一条。',
  wrong_job: '点击后页面变成了另一个岗位，招呼语没有发出去，请到 BOSS 手动补一条。',
  chat_wrong_job:
    'BOSS 跳到聊天页后打开的是另一个岗位的对话，为避免发错人没有发送。'
    + '请在 BOSS 里找到这个岗位的对话自己补一条。',
  chat_job_ambiguous:
    '聊天页上出现了多个岗位链接，无法确定这条对话属于哪个岗位，没有发送。请到 BOSS 手动补一条。',
  chat_job_unknown:
    '聊天页上读不到这条对话对应的岗位，无法确认发给谁，没有发送。请到 BOSS 手动补一条。',
  chat_list_unknown:
    'BOSS 聊天页的结构变了，无法把左侧联系人列表排除在核对范围外，'
    + '为避免发错人没有发送。请到 BOSS 手动补一条。',
  input_not_empty: 'BOSS 自己已经发了一条招呼语，所以没有再发第二条。',
  already_sent:
    '这条招呼语已经点过一次发送，没有重复点。'
    + '请到 BOSS 看一眼这个对话，确认是发出去了还是没发出去。',
  no_composer: '没等到输入框出现，招呼语没有发出去，请到 BOSS 手动补一条。',
  ambiguous_composer: '页面上有多个输入框，无法确定发给谁，没有发送。',
  no_send_control: '找不到发送按钮，招呼语没有发出去，请到 BOSS 手动补一条。',
  input_rejected: 'BOSS 拒绝或改写了输入内容，没有发送。',
  foreground_lost: '中途切走了窗口，招呼语没有发出去，请到 BOSS 手动补一条。',
  'greeting_unavailable:chat': 'BOSS 点击后跳到了聊天页，页面还在跳转中，招呼语没有发出去，请手动补一条。',
  'greeting_unavailable:job_detail': '页面没有响应，招呼语没有发出去，请到 BOSS 手动补一条。',
  'greeting_unavailable:other': 'BOSS 跳到了别的页面，招呼语没有发出去，请到 BOSS 手动补一条。',
  'greeting_unavailable:unreadable': '无法确认页面状态，招呼语没有发出去，请到 BOSS 手动核对。',
}

/** What the attempt detail says about the greeting, in one sentence. */
function explainM6Greeting(detail: string | undefined): string {
  if (!detail) return ''
  if (detail.startsWith('clicked_and_greeted')) return '招呼语已发送。'
  const prefix = 'clicked_greeting_skipped:'
  if (!detail.startsWith(prefix)) return ''
  // The worker appends `|<shape>` diagnostics to some statuses.
  const status = detail.slice(prefix.length).split('|')[0]
  return M6_GREETING_REASON[status] || `招呼语没有发出去（${status}），请到 BOSS 手动补一条。`
}

function explainM6Failure(code: string | undefined, message: string): string {
  const status = code?.startsWith('m6_preflight/') ? code.slice('m6_preflight/'.length) : ''
  const reason = M6_PREFLIGHT_REASON[status]
  return reason ? `${reason}\n（原始状态：${status}）` : message
}

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

const SORT_LABEL: Record<string, string> = {
  recommended: '推荐优先',
  score: '匹配分',
  newest: '最新',
  salary: '薪资',
}

const STATE_LABEL: Record<string, string> = {
  ready: '优先处理',
  pending: '待处理',
  later: '稍后处理',
  dismissed: '已跳过',
  completed: '已处理',
}

export default function ApplicationQueuePage() {
  const [data, setData] = useState<QueueResponse | null>(null)
  // Refreshing the greetings is a paid action, so it uses the same
  // plan-then-confirm shape every paid action in this app uses: asking what a
  // run would cost writes nothing and calls no model.
  const [greetPlan, setGreetPlan] = useState<BatchAnalyzePlan | null>(null)
  const [greetIds, setGreetIds] = useState<number[]>([])
  const [greetBusy, setGreetBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [busyJob, setBusyJob] = useState<number | null>(null)
  const [copiedJob, setCopiedJob] = useState<number | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [selected, setSelected] = useState<Set<number>>(new Set())

  const [filters, setFilters] = useState<QueueFilters>({ sort: 'recommended', limit: 100 })
  //: Off by default: this records real actions, so it is opened deliberately.
  const [showBackfill, setShowBackfill] = useState(false)
  const [keywordInput, setKeywordInput] = useState('')

  // Confirmation dialogs: applying and skipping are real decisions.
  const [confirmApply, setConfirmApply] = useState<ApplicationProposal | null>(null)
  const [confirmApplyFromM6, setConfirmApplyFromM6] = useState(false)
  const [applyNote, setApplyNote] = useState('')
  // Which resume the user says they actually submitted. Defaults to the
  // active analysis resume as a convenience; never substituted silently.
  const [appliedResume, setAppliedResume] = useState<ResumeChoice>(UNKNOWN_CHOICE)
  const { resumes, activeId } = useSelectableResumes()
  const [skipTarget, setSkipTarget] = useState<ApplicationProposal | null>(null)
  const [skipReason, setSkipReason] = useState('')
  const [m6Target, setM6Target] = useState<ApplicationProposal | null>(null)
  const [m6ResumeId, setM6ResumeId] = useState<number | null>(null)
  const [m6DynamicAccepted, setM6DynamicAccepted] = useState(false)
  const [m6Approval, setM6Approval] = useState<ApplicationApprovalOut | null>(null)
  const [m6Busy, setM6Busy] = useState(false)
  const [m6Attempted, setM6Attempted] = useState(false)
  const [m6StatusUnknown, setM6StatusUnknown] = useState(false)
  //: Why the last attempt did not happen, shown *in* the dialog. The first
  //: live run failed with a precise reason that went to the page-level
  //: banner behind the modal, so the only thing visible was a button that
  //: had turned itself off.
  const [m6Error, setM6Error] = useState<string | null>(null)
  //: True when the page is running a content script from an extension
  //: build that no longer exists - the normal state right after reloading
  //: the extension, and curable only by reloading this page.
  const [m6StaleBridge, setM6StaleBridge] = useState(false)
  //: The exact text JobAgent will type, editable before confirming. Empty
  //: means "let BOSS decide", which is what it always did before.
  const [m6Greeting, setM6Greeting] = useState('')
  const [m6SendGreeting, setM6SendGreeting] = useState(true)

  const load = useCallback(async (active: QueueFilters) => {
    setLoading(true)
    try {
      setData(await api.applicationQueue(active))
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载队列失败' })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(filters)
  }, [filters, load])

  const summary = data?.summary
  const items = data?.items ?? []
  const cityOptions = useMemo(() => Object.keys(data?.facets.cities ?? {}), [data])
  const skipReasons = data?.facets.skip_reasons ?? []

  /** What re-analyzing the listed jobs would cost. Pure read - spends nothing.
   *
   *  `force` is deliberately false: the analysis cache key includes the prompt
   *  version, so a job already analyzed under the current one is a cache hit
   *  and costs nothing, while one written by an older prompt is a miss and
   *  gets the new greeting. The plan's `cached` / `pending` split therefore
   *  already says exactly how many greetings are out of date. */
  async function openGreetingPlan() {
    const jobIds = items.map(item => item.job_id)
    if (jobIds.length === 0) return
    setGreetBusy(true)
    setFeedback(null)
    try {
      const plan = await api.analyzeBatchPlan(jobIds)
      setGreetIds(jobIds)
      setGreetPlan(plan)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '生成计划失败' })
    } finally {
      setGreetBusy(false)
    }
  }

  async function runGreetingRefresh() {
    if (!greetPlan || greetIds.length === 0) return
    setGreetBusy(true)
    setFeedback({ tone: 'info', text: `正在重新分析 ${greetPlan.in_batch} 个岗位…` })
    try {
      const result = await api.analyzeBatch(false, greetIds)
      setGreetPlan(null)
      setGreetIds([])
      const failed = result.failed > 0 ? `，失败 ${result.failed} 个（不会自动重试）` : ''
      const deferred = result.requested > result.items.length
        ? `；还有 ${result.requested - result.items.length} 个超出本批次上限，未处理，可以再点一次`
        : ''
      setFeedback({
        tone: result.failed > 0 ? 'warn' : 'success',
        text: `完成：新生成招呼语 ${result.analyzed} 个，${result.cached} 个已是最新（未花钱）${failed}${deferred}。`,
      })
      await load(filters)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '重新分析失败' })
    } finally {
      setGreetBusy(false)
    }
  }

  function updateFilter<K extends keyof QueueFilters>(key: K, value: QueueFilters[K]) {
    setFilters((prev) => ({ ...prev, [key]: value }))
  }

  async function run(jobId: number, action: () => Promise<{ message: string }>) {
    setBusyJob(jobId)
    try {
      const result = await action()
      setFeedback({ tone: 'success', text: result.message })
      setSelected((prev) => {
        const next = new Set(prev)
        next.delete(jobId)
        return next
      })
      await load(filters)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '操作失败' })
    } finally {
      setBusyJob(null)
    }
  }

  async function copyGreeting(proposal: ApplicationProposal) {
    // Copying is not a decision: it must never change the job's status.
    try {
      await navigator.clipboard.writeText(proposal.greeting_message)
      setCopiedJob(proposal.job_id)
      window.setTimeout(() => setCopiedJob(null), 2000)
    } catch {
      setFeedback({ tone: 'warn', text: '浏览器拒绝了剪贴板访问，请手动选中招呼语复制。' })
    }
  }

  /** Put the greeting on the clipboard as the posting's own page opens.
   *
   * Rendered as a real anchor so the browser treats the new tab as a plain user
   * navigation (a popup blocker would eat a scripted `window.open`). Neither
   * half is a decision: opening a page and filling the clipboard change no
   * status and send nothing. This remains the fully manual path: you paste,
   * read it, click send, and only then come back and record it. The separate
   * M6 path is deliberately not reachable from this helper.
   */
  function openForApplying(proposal: ApplicationProposal) {
    if (proposal.greeting_message) void copyGreeting(proposal)
    setFeedback({
      tone: 'info',
      text: `已打开「${proposal.title}」的岗位页${
        proposal.greeting_message ? '，招呼语已复制到剪贴板' : ''
      }。发送后回到这里点「标记已投递」才会记录。`,
    })
  }

  async function bulk(action: 'later' | 'skip') {
    const ids = [...selected]
    if (ids.length === 0) return
    setFeedback({ tone: 'info', text: `正在处理 ${ids.length} 个岗位…` })
    try {
      for (const id of ids) {
        if (action === 'later') await api.deferJob(id, 'tomorrow')
        else await api.skipJob(id)
      }
      setSelected(new Set())
      setFeedback({ tone: 'success', text: `已${action === 'later' ? '稍后处理' : '跳过'} ${ids.length} 个岗位` })
      await load(filters)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '批量操作失败' })
    }
  }

  function toggleSelected(jobId: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(jobId)) next.delete(jobId)
      else next.add(jobId)
      return next
    })
  }

  function toggleExpanded(jobId: number) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(jobId)) next.delete(jobId)
      else next.add(jobId)
      return next
    })
  }

  function openM6Confirmation(proposal: ApplicationProposal) {
    setM6Target(proposal)
    setM6ResumeId(activeId ?? null)
    setM6DynamicAccepted(false)
    setM6Approval(null)
    setM6Attempted(false)
    setM6StatusUnknown(false)
    setM6Error(null)
    setM6StaleBridge(false)
    // Seeded from this job's own draft, and editable: what the dialog shows is
    // what gets typed, so it has to be the thing the human reads and can change.
    setM6Greeting(proposal.greeting_message || '')
    setM6SendGreeting(Boolean(proposal.greeting_message))
  }

  /** Bind the approval and execute it, from the one confirmation.
   *
   * There used to be two screens: one to bind the snapshot, another to look at
   * it and execute. The second guarded against a *time gap* - "confirming once
   * in a queue days earlier is not enough" - which does not exist when the two
   * are seconds apart, and the check it performed by eye is done far more
   * strictly by `validate` on the backend, which refuses a snapshot that no
   * longer matches the job, the resume or the page identity.
   *
   * So the dialog shows the exact job, URL, resume and greeting warning, and
   * one confirmation both binds and executes. What is not merged away: the
   * per-job confirmation itself, the acceptance checkbox, and the fact that a
   * confirmation authorizes exactly one attempt.
   */
  async function confirmAndExecuteM6() {
    if (!m6Target || !m6ResumeId || !m6DynamicAccepted || m6Attempted) return
    setM6Error(null)
    setM6StaleBridge(false)
    setM6Attempted(true) // One confirmation can dispatch at most one command.
    setM6Busy(true)
    let approval: ApplicationApprovalOut
    try {
      approval = await api.createApplicationApproval(
        m6Target.job_id, m6ResumeId, m6SendGreeting ? m6Greeting.trim() : '',
      )
      setM6Approval(approval)
    } catch (err) {
      setM6Attempted(false) // Nothing was dispatched, so this may be retried.
      setM6Busy(false)
      const message = err instanceof ApiError ? err.message : '生成确认失败'
      setM6Error(message)
      setFeedback({ tone: 'error', text: message })
      return
    }
    await executeM6Approval(approval)
  }

  async function executeM6Approval(bound?: ApplicationApprovalOut) {
    // Takes the approval explicitly: when called straight after creating one,
    // React has not committed `m6Approval` yet, and reading the stale state
    // here would dispatch nothing at all.
    const approval = bound ?? m6Approval
    if (!approval || approval.state !== 'pending') return
    setM6Attempted(true) // One confirmation can dispatch at most one command.
    setM6Busy(true)
    let staleReason = ''
    // Call immediately in this click handler so the extension bridge receives
    // a real browser user activation before any await occurs.
    const pending = consoleExtension(
      'execute-application', undefined, undefined, undefined, undefined, approval.id,
    )
    try {
      const reply = await pending
      if (!reply.ok) {
        if (reply.code === 'extension_context_unavailable' || reply.code === 'worker_unavailable') {
          setM6StaleBridge(true)
        }
        staleReason = reply.code?.startsWith('m6_preflight/')
          ? reply.code.slice('m6_preflight/'.length)
          : ''
        throw new Error(explainM6Failure(reply.code, reply.error || '扩展未确认执行结果'))
      }
      const attemptedJob = m6Target
      const greetingNote = explainM6Greeting(reply.application?.detail)
      setFeedback({
        tone: 'warn',
        text: '已执行一次「立即沟通」，并记录为结果待确认。'
          + (greetingNote ? greetingNote + ' ' : '')
          + '请先在 BOSS 核对沟通是否建立，勿直接重试；再在弹窗中确认，确认后会通过现有唯一记录路径进入「已投递」列表。',
      })
      setM6Target(null)
      setM6Approval(null)
      setApplyNote('确认后由 JobAgent 沟通；已在 BOSS 人工核对结果')
      // 仅沟通, because that is what M6 did: it clicks 立即沟通 and sends the
      // greeting. No resume is submitted by that action, so defaulting to the
      // analysis resume made every M6 application start out claiming a resume
      // that was never sent - and the user had to correct it by hand each time.
      //
      // This is not an inference about something unknown (v0.7 forbids those):
      // it is what the flow demonstrably does. Still a default, not a decision
      // - the picker is right there, and choosing 不确定 or a variant is one
      // click away for anyone who did attach one separately.
      setAppliedResume({ resumeId: null, usage: 'no_resume' })
      setConfirmApplyFromM6(true)
      setConfirmApply(attemptedJob)
      await load(filters)
    } catch (err) {
      // If the worker claimed the attempt and then vanished, retain the real
      // executing state so the user can close it as unknown. Never re-enable
      // this approval's execute button after an ambiguous command.
      setM6StatusUnknown(true)
      try {
        setM6Approval(await api.applicationApproval(approval.id))
        setM6StatusUnknown(false)
      } catch { /* backend may be unavailable; keep modal open and approval non-retryable */ }
      const message = err instanceof Error ? err.message : '执行结果未知；请到 BOSS 人工核对，勿重复点击。'
      setM6Error(message)
      setFeedback({ tone: 'error', text: message })
      // A closed posting that was skipped is no longer in the queue, so the
      // list has to be re-read: leaving it on screen contradicts the message
      // that just said it was retired.
      if (staleReason === 'posting_closed') await load(filters)
    } finally {
      setM6Busy(false)
    }
  }

  async function refreshM6Attempt() {
    if (!m6Approval || !m6Attempted) return
    setM6Busy(true)
    try {
      setM6Approval(await api.applicationApproval(m6Approval.id))
      setM6StatusUnknown(false)
    } catch (err) {
      setM6StatusUnknown(true)
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '尝试状态仍无法确认；不会重试' })
    } finally {
      setM6Busy(false)
    }
  }

  async function abandonM6Attempt() {
    if (!m6Approval || m6Approval.state !== 'executing') return
    setM6Busy(true)
    try {
      const closed = await api.abandonApplicationAttempt(m6Approval.id)
      setM6Approval(closed)
      setFeedback({
        tone: 'warn',
        text: '已由你确认结束这次未返回结果的尝试，并记为「结果待确认」。请先到 BOSS 核对；系统不会自动重试。',
      })
      await load(filters)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '结束未知尝试失败' })
    } finally {
      setM6Busy(false)
    }
  }

  const target = summary?.daily_target ?? 10
  const appliedToday = summary?.applied_today ?? 0

  return (
    <>
      <header className="page-head">
        <div>
          <h1>今日投递队列</h1>
          <p>
            AI 只负责推荐。你可以自己投递完回来记录，也可以逐个岗位确认后，
            让 JobAgent 在你眼前点一次「立即沟通」。
          </p>
        </div>
        <div className="page-actions">
          <button type="button" onClick={() => void load(filters)} disabled={loading}>
            刷新
          </button>
          <Link className="btn" to="/quick-capture">
            快速采集
          </Link>
        </div>
      </header>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      {/* Filtering the queue without saying so would be a silent exclusion.
          The count comes from the API and always matches what vanished. */}
      {summary && summary.early_career_hidden > 0 ? (
        <Alert tone="info">
          按当前「候选阶段」设置（
          {summary.early_career_policy === 'exclude'
            ? '排除应届/校招/实习'
            : summary.early_career_policy === 'only'
              ? '只看应届/校招/实习'
              : summary.early_career_policy}
          ），已隐藏 <strong>{summary.early_career_hidden}</strong> 个岗位。{' '}
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={() =>
              updateFilter('include_early_career', !filters.include_early_career)
            }
          >
            {filters.include_early_career ? '重新隐藏' : '查看这些岗位'}
          </button>
          <Link className="btn-ghost btn-sm" to="/strategy">
            修改设置
          </Link>
        </Alert>
      ) : null}


      <div className="grid grid-stats">
        <section className="card stat">
          <span className="stat-label">待处理</span>
          <span className="stat-value">{summary?.pending ?? 0}</span>
        </section>
        <section className="card stat">
          <span className="stat-label">强烈推荐</span>
          <span className="stat-value">{summary?.strong_apply ?? 0}</span>
        </section>
        <section className="card stat">
          <span className="stat-label">建议投递</span>
          <span className="stat-value">{summary?.apply ?? 0}</span>
        </section>
        <section className="card stat">
          <span className="stat-label">稍后处理</span>
          <span className="stat-value">{summary?.later ?? 0}</span>
        </section>
        <section className="card stat">
          <span className="stat-label">今日已投</span>
          <span className="stat-value">
            {appliedToday} <span className="small faint">/ {target}</span>
          </span>
          <span className="stat-hint">
            仅供参考 · {summary?.timezone ?? 'Asia/Tokyo'}
            {summary && summary.replied_today > 0 ? ` · 今日回复 ${summary.replied_today}` : ''}
          </span>
        </section>
      </div>

      <div className="row mb-1">
        <button
          type="button"
          className="btn-sm"
          aria-expanded={showBackfill}
          onClick={() => setShowBackfill(current => !current)}
        >
          {showBackfill ? '收起补录' : '补录已投递（粘贴 BOSS 沟通列表）'}
        </button>
        <span className="small faint">
          在 BOSS 上自己投递过、但这里没记录的岗位，可以在此补上。
        </span>
      </div>

      <div className="row mb-1">
        <button
          type="button"
          className="btn-sm"
          disabled={greetBusy || items.length === 0}
          onClick={() => void openGreetingPlan()}
        >
          {greetBusy ? '处理中…' : '刷新招呼语'}
        </button>
        <span className="small faint">
          招呼语的写法改过之后，旧岗位仍是旧版本。点一下先看要花多少次调用，再决定。
        </span>
      </div>

      {greetPlan ? (
        <Card title="确认重新分析">
          <ul className="small">
            <li>列表中的岗位：{greetPlan.selected} 个</li>
            <li>本批次上限：{greetPlan.limit} 个</li>
            <li>本次实际处理：{greetPlan.in_batch} 个</li>
            <li>其中招呼语已是最新、不花钱：{greetPlan.cached} 个</li>
            <li>
              <strong>预计新增 AI 调用：{greetPlan.pending} 次</strong>
              （模型 {greetPlan.model}，简历「{greetPlan.resume_name}」）
            </li>
          </ul>
          {greetPlan.deferred > 0 ? (
            <p className="small text-warn">
              超出本批次上限，本次只处理前 {greetPlan.in_batch} 个，剩余 {greetPlan.deferred} 个
              不会处理。系统不会自行放宽上限；本次跑完后再点一次即可继续。
            </p>
          ) : null}
          <p className="small faint">
            重新分析会一并更新分数和结论，不只是招呼语——分数可能小幅变动。
            已投递的记录不受影响。
          </p>
          <div className="btn-row">
            <button
              type="button"
              className="btn btn-primary"
              disabled={greetBusy || greetPlan.pending === 0}
              onClick={() => void runGreetingRefresh()}
            >
              {greetBusy ? '分析中…' : `确认花费 ${greetPlan.pending} 次调用`}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={greetBusy}
              onClick={() => { setGreetPlan(null); setGreetIds([]) }}
            >
              取消
            </button>
          </div>
          {greetPlan.pending === 0 ? (
            <p className="small faint mt-1">列表里的招呼语都已经是最新版本，无需重新分析。</p>
          ) : null}
        </Card>
      ) : null}

      {showBackfill ? (
        <AppliedBackfillPanel onDone={() => { setShowBackfill(false); void load(filters) }} />
      ) : null}

      <Card title="筛选">
        <div className="filters">
          <div className="field">
            <label htmlFor="q-city">城市</label>
            <select
              id="q-city"
              value={filters.city ?? ''}
              onChange={(e) => updateFilter('city', e.target.value || undefined)}
            >
              <option value="">全部城市</option>
              {cityOptions.map((city) => (
                <option key={city} value={city}>
                  {city}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label htmlFor="q-verdict">AI 结论</label>
            <select
              id="q-verdict"
              value={filters.verdict ?? ''}
              onChange={(e) => updateFilter('verdict', (e.target.value || undefined) as Verdict)}
            >
              <option value="">全部</option>
              <option value="strong_apply">强烈推荐</option>
              <option value="apply">推荐投递</option>
              <option value="maybe">可以考虑</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="q-exp">我的经验年数</label>
            <select
              id="q-exp"
              value={filters.max_required_years ?? ''}
              onChange={(e) =>
                updateFilter(
                  'max_required_years',
                  e.target.value ? Number(e.target.value) : undefined,
                )
              }
            >
              <option value="">不限</option>
              <option value="1">1 年</option>
              <option value="2">2 年</option>
              <option value="3">3 年</option>
              <option value="5">5 年</option>
              <option value="8">8 年</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="q-score">最低匹配分</label>
            <select
              id="q-score"
              value={filters.min_score ?? ''}
              onChange={(e) =>
                updateFilter('min_score', e.target.value ? Number(e.target.value) : undefined)
              }
            >
              <option value="">不限</option>
              <option value="90">90 分以上</option>
              <option value="80">80 分以上</option>
              <option value="70">70 分以上</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="q-status">状态</label>
            <select
              id="q-status"
              value={filters.status ?? ''}
              onChange={(e) => updateFilter('status', (e.target.value || undefined) as JobStatus)}
            >
              <option value="">全部</option>
              <option value="new">待处理</option>
              <option value="reviewed">已查看</option>
              <option value="saved">已收藏</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="q-keyword">关键词</label>
            <form
              onSubmit={(e) => {
                e.preventDefault()
                updateFilter('keyword', keywordInput.trim() || undefined)
              }}
            >
              <input
                id="q-keyword"
                value={keywordInput}
                placeholder="职位 / 公司 / 技能"
                onChange={(e) => setKeywordInput(e.target.value)}
              />
            </form>
          </div>

          <div className="field">
            <label htmlFor="q-sort">排序</label>
            <select
              id="q-sort"
              value={filters.sort ?? 'recommended'}
              onChange={(e) => updateFilter('sort', e.target.value as QueueFilters['sort'])}
            >
              {(data?.facets.sorts ?? ['recommended', 'score', 'newest', 'salary']).map((key) => (
                <option key={key} value={key}>
                  {SORT_LABEL[key] ?? key}
                </option>
              ))}
            </select>
          </div>
        </div>

        <p className="small faint mt-1">
          「我的经验年数」只看要求不超过这个年数的岗位；没写要求的一律保留。
        </p>

        <div className="row mt-1">
          <div className="checkbox-row">
            <input
              id="q-maybe"
              type="checkbox"
              checked={Boolean(filters.include_maybe)}
              onChange={(e) => updateFilter('include_maybe', e.target.checked || undefined)}
            />
            <label htmlFor="q-maybe">包含 Maybe</label>
          </div>
          <button
            type="button"
            className="btn-sm"
            onClick={() => {
              setKeywordInput('')
              setFilters({ sort: 'recommended', limit: 100 })
            }}
          >
            重置筛选
          </button>
        </div>
      </Card>

      {selected.size > 0 ? (
        <Alert tone="info">
          <div className="row-between">
            <span>已选择 {selected.size} 个岗位</span>
            <div className="btn-row">
              <button type="button" className="btn-sm" onClick={() => void bulk('later')}>
                批量稍后处理
              </button>
              <button type="button" className="btn-sm" onClick={() => void bulk('skip')}>
                批量跳过
              </button>
              <button type="button" className="btn-sm" onClick={() => setSelected(new Set())}>
                取消选择
              </button>
            </div>
          </div>
          <div className="small faint mt-1">
            「已投递」不支持批量操作 —— 它代表一次真实的外部行为，需要逐个确认。
          </div>
        </Alert>
      ) : null}

      {loading && items.length === 0 ? <Loading text="正在加载投递队列…" /> : null}

      {!loading && items.length === 0 ? (
        <Card>
          {summary && summary.pending === 0 && summary.applied_today > 0 ? (
            <EmptyState
              icon="✅"
              title="今天的投递队列已经处理完了。"
              text={`今日已记录 ${summary.applied_today} 次投递。`}
              action={
                <Link className="btn" to="/jobs">
                  查看岗位库
                </Link>
              }
            />
          ) : (
            <EmptyState
              icon="📭"
              title="暂无待投递岗位。先采集并分析几个职位吧。"
              action={
                <Link className="btn btn-primary" to="/quick-capture">
                  快速采集
                </Link>
              }
            />
          )}
        </Card>
      ) : null}

      {items.map((proposal) => {
        const busy = busyJob === proposal.job_id
        const isOpen = expanded.has(proposal.job_id)
        return (
          <section className="card" key={proposal.job_id}>
            <div className="row-between">
              <div className="row">
                <input
                  type="checkbox"
                  style={{ width: 'auto' }}
                  aria-label={`选择 ${proposal.title}`}
                  checked={selected.has(proposal.job_id)}
                  onChange={() => toggleSelected(proposal.job_id)}
                />
                <ScoreBadge score={proposal.overall_score} />
                <div>
                  <div className="cell-title">
                    <Link to={`/jobs/${proposal.job_id}`}>{proposal.company}</Link>
                  </div>
                  <div className="cell-sub">{proposal.title}</div>
                </div>
              </div>
              <div className="row">
                <VerdictBadge verdict={proposal.verdict} />
                <StatusBadge status={proposal.job_status} />
                {proposal.proposal_state === 'later' ? (
                  <span className="badge badge-neutral">
                    {STATE_LABEL.later}
                    {proposal.review_after ? ` · ${formatDateTime(proposal.review_after)}` : ''}
                  </span>
                ) : null}
              </div>
            </div>

            {proposal.company_applied_title ? (
              <p className="small faint mt-1">
                已投递该公司的「{proposal.company_applied_title}」。BOSS 的对话是按人建立的，
                如果是同一个 HR，这个岗位的按钮会显示「继续沟通」，JobAgent 会拒绝执行。
                同公司未必同 HR，仅作提醒。
              </p>
            ) : null}

            <div className="job-card-meta mt-1">
              {proposal.early_career ? (
                <span className="chip chip-bad" title="标题或 JD 显示这是应届/校招/实习岗位">
                  应届/校招
                </span>
              ) : null}
              {proposal.city ? <span className="chip">{proposal.city}</span> : null}
              {proposal.salary_text ? <span className="chip">{proposal.salary_text}</span> : null}
              {proposal.experience_text ? (
                <span className="chip">{proposal.experience_text}</span>
              ) : null}
              <span className="chip">{proposal.source}</span>
              {proposal.matched_skills.slice(0, 3).map((skill) => (
                <span key={skill} className="chip chip-good">
                  {skill}
                </span>
              ))}
              {proposal.missing_skills.slice(0, 2).map((skill) => (
                <span key={skill} className="chip chip-bad">
                  缺 {skill}
                </span>
              ))}
            </div>

            {proposal.reasoning_summary ? (
              <p className="small muted mt-1">{proposal.reasoning_summary}</p>
            ) : null}

            {proposal.greeting_message ? (
              <div className="mt-1">
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  onClick={() => toggleExpanded(proposal.job_id)}
                >
                  {isOpen ? '收起招呼语 ▲' : '展开招呼语 ▼'}
                </button>
                {isOpen ? (
                  <div className="greeting mt-1">{proposal.greeting_message}</div>
                ) : (
                  <div className="job-card-summary">{proposal.greeting_message}</div>
                )}
              </div>
            ) : null}

            <div className="btn-row mt-2">
              <Link className="btn btn-sm" to={`/jobs/${proposal.job_id}`}>
                查看岗位
              </Link>
              {proposal.source_url ? (
                <a
                  className="btn btn-sm"
                  href={proposal.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="在新标签页打开岗位原页面，并把招呼语复制到剪贴板"
                  onClick={() => openForApplying(proposal)}
                >
                  去投递 ↗
                </a>
              ) : (
                <span className="small faint" title="该岗位没有可核对的原始链接，可能是早期导入的记录">
                  无可用岗位链接
                </span>
              )}
              <button
                type="button"
                className="btn-sm"
                disabled={!proposal.greeting_message}
                onClick={() => void copyGreeting(proposal)}
              >
                {copiedJob === proposal.job_id ? '已复制 ✓' : '复制招呼语'}
              </button>
              <button
                type="button"
                className="btn-primary btn-sm"
                disabled={busy}
                onClick={() => {
                  setApplyNote('')
                  setAppliedResume(
                    activeId ? { resumeId: activeId, usage: 'used' } : UNKNOWN_CHOICE,
                  )
                  setConfirmApplyFromM6(false)
                  setConfirmApply(proposal)
                }}
              >
                标记已投递
              </button>
              {proposal.source_url ? (
                <button
                  type="button"
                  className="btn-sm"
                  disabled={busy || m6Busy}
                  onClick={() => openM6Confirmation(proposal)}
                >
                  确认并沟通
                </button>
              ) : null}
              <button
                type="button"
                className="btn-sm"
                disabled={busy}
                onClick={() => void run(proposal.job_id, () => api.deferJob(proposal.job_id, 'tomorrow'))}
              >
                稍后处理
              </button>
              <button
                type="button"
                className="btn-sm"
                disabled={busy}
                onClick={() => {
                  setSkipReason('')
                  setSkipTarget(proposal)
                }}
              >
                跳过
              </button>
            </div>
          </section>
        )
      })}

      {confirmApply ? (
        <Modal
          title={confirmApplyFromM6 ? '核对并记录本次投递' : '确认已投递？'}
          onClose={() => {
            setConfirmApply(null)
            setConfirmApplyFromM6(false)
          }}
          footer={
            <>
              <button type="button" onClick={() => {
                setConfirmApply(null)
                setConfirmApplyFromM6(false)
              }}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  const proposal = confirmApply
                  setConfirmApply(null)
                  setConfirmApplyFromM6(false)
                  void run(proposal.job_id, () =>
                    api.markApplied(proposal.job_id, applyNote, appliedResume),
                  )
                }}
              >
                确认已投递
              </button>
            </>
          }
        >
          <p className="mt-0">
            <strong>{confirmApply.company}</strong>
            <br />
            {confirmApply.title}
          </p>
          <p className="muted">
            {confirmApplyFromM6
              ? '请先查看 BOSS：该岗位的沟通是否已经成功建立？只有确认成功后才记录为已投递；取消会保留“结果待确认”的审计记录。'
              : '你已经在招聘平台完成了实际投递或沟通吗？'}
          </p>
          <ResumePicker
            resumes={resumes}
            value={appliedResume}
            onChange={setAppliedResume}
          />
          {confirmApplyFromM6 ? (
            <p className="small faint">
              已默认「仅沟通」：刚才点的是「立即沟通」并发送招呼语，这个动作本身不提交简历。
              如果你另外单独发过简历，改选对应的那份即可。
            </p>
          ) : null}
          <div className="field">
            <label htmlFor="apply-note">备注（可选）</label>
            <input
              id="apply-note"
              value={applyNote}
              placeholder="例如：已通过 BOSS 打招呼"
              onChange={(e) => setApplyNote(e.target.value)}
            />
          </div>
          <div className="field-hint">
            {confirmApplyFromM6
              ? '确认后这个岗位进入已投递列表。'
              : '这里只是记录，不会操作招聘网站。'}
          </div>
        </Modal>
      ) : null}

      {m6Target ? (
        <Modal
          title="确认投递这一个岗位？"
          onClose={() => {
            if (!m6Busy && m6Approval?.state !== 'executing' && !m6StatusUnknown) {
              setM6Target(null)
              setM6Approval(null)
            }
          }}
          footer={
            <>
              <button type="button" disabled={m6Busy || m6Approval?.state === 'executing' || m6StatusUnknown} onClick={() => {
                setM6Target(null)
                setM6Approval(null)
                setM6Error(null)
              }}>
                取消
              </button>
              {m6StatusUnknown ? (
                <button type="button" disabled={m6Busy} onClick={() => void refreshM6Attempt()}>
                  {m6Busy ? '正在刷新…' : '刷新尝试状态'}
                </button>
              ) : null}
              {m6Error ? (
            <Alert tone="error">
              <span style={{ whiteSpace: 'pre-line' }}>{m6Error}</span>
              {m6Approval?.state === 'pending' || (!m6Approval && m6Attempted) ? (
                <>
                  <br />
                  这次没有点击任何按钮，本确认也没有被消耗。关闭本窗口后可以重新确认一次。
                </>
              ) : null}
              {m6StaleBridge ? (
                <>
                  <br />
                  <button type="button" className="btn-sm mt-1" onClick={() => window.location.reload()}>
                    刷新本页
                  </button>
                </>
              ) : null}
            </Alert>
          ) : null}
          {m6Approval?.state === 'executing' ? (
                <button
                  type="button"
                  disabled={m6Busy}
                  onClick={() => void abandonM6Attempt()}
                >
                  {m6Busy ? '正在记录…' : '我已人工核对，结束为结果未知'}
                </button>
              ) : (
                <button
                  type="button"
                  className="btn-primary"
                  disabled={m6Busy || !m6ResumeId || !m6DynamicAccepted || m6Attempted
                    || (m6SendGreeting && !m6Greeting.trim())}
                  onClick={() => void confirmAndExecuteM6()}
                >
                  {m6Busy
                    ? '正在执行…'
                    : m6Attempted
                      ? '本确认已发出，不可重试'
                      : '确认并执行一次投递'}
                </button>
              )}
            </>
          }
        >
          <p className="mt-0">
            <strong>{m6Approval?.company ?? m6Target.company}</strong>
            <br />
            {m6Approval?.title ?? m6Target.title}
          </p>
          <Alert tone="warn">
            {m6SendGreeting ? (
              <>
                BOSS 有时会在点击「立即沟通」时自己发送一条招呼语，有时不会——两种情况都实际遇到过。
                <strong> JobAgent 只在聊天框为空时才填写下面这段</strong>，
                框里已经有内容就不发，避免连发两条。
              </>
            ) : (
              <>
                BOSS 会在点击「立即沟通」时发送平台动态决定的首次招呼语。
                <strong> JobAgent 无法在点击前预览、独立核实或控制其正文</strong>，实际发送内容可能变化。
              </>
            )}
          </Alert>
          <div className="field">
            <label>岗位链接</label>
            <div className="small">{m6Approval?.canonical_url ?? m6Target.source_url ?? '（无可打开的链接）'}</div>
          </div>
          <div className="field">
            <label htmlFor="m6-resume">本次简历</label>
            <select
              id="m6-resume"
              value={m6ResumeId ?? ''}
              disabled={m6Busy || m6Attempted}
              onChange={(e) => setM6ResumeId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">请选择</option>
              {resumes.filter((resume) => !resume.archived).map((resume) => (
                <option key={resume.id} value={resume.id}>{resume.label}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="m6-greeting">首次招呼语</label>
            <div className="checkbox-row">
              <input
                id="m6-send-greeting"
                type="checkbox"
                checked={m6SendGreeting}
                disabled={m6Busy || m6Attempted}
                onChange={(e) => setM6SendGreeting(e.target.checked)}
              />
              <label htmlFor="m6-send-greeting">
                由 JobAgent 填写并发送下面这段（仅在聊天框为空时）
              </label>
            </div>
            {m6SendGreeting ? (
              <>
                <textarea
                  id="m6-greeting"
                  rows={4}
                  value={m6Greeting}
                  disabled={m6Busy || m6Attempted}
                  onChange={(e) => setM6Greeting(e.target.value)}
                />
                <div className="field-hint">
                  这就是会被逐字打出去的内容，确认前可以随意修改。
                  如果 BOSS 自己已经在聊天框里放了招呼语，这段<strong>不会</strong>发送——
                  避免连发两条。只发一次，之后的任何消息仍然由你自己发。
                </div>
              </>
            ) : (
              <div className="greeting">
                不填写。点击「立即沟通」后由 BOSS 决定是否发送以及发送什么，
                JobAgent 无法预览、核实或控制。
              </div>
            )}
          </div>
          <div className="checkbox-row">
            <input
              id="m6-dynamic-accept"
              type="checkbox"
              checked={m6DynamicAccepted}
              disabled={m6Busy || m6Attempted}
              onChange={(e) => setM6DynamicAccepted(e.target.checked)}
            />
            <label htmlFor="m6-dynamic-accept">
              {m6SendGreeting
                ? '我已读过上面的招呼语，确认由 JobAgent 向这一个岗位发送它一次。'
                : '我接受 BOSS 为这个岗位动态生成未知的首次招呼语，并理解 JobAgent 无法预览、核实或控制正文。'}
            </label>
          </div>
          <div className="field-hint">
            确认后立即执行这一个岗位的一次尝试；不会批量、后台、自动重试或发送后续消息。
            此处不要求、生成、保存或沿用任何预计正文；AI 也不能代替你勾选确认。
            当前没有可靠的站点成功状态 fixture，因此点击后先记为「结果待确认」，不会自动标记已投递。
          </div>
          {m6Approval?.state === 'executing' ? (
            <Alert tone="warn">
              扩展已领取这次尝试，但没有返回终态。请先到 BOSS 人工核对。上方按钮只把本地记录
              结束为「结果未知」，不会点击网页或重试投递。
            </Alert>
          ) : null}
          {m6StatusUnknown ? (
            <Alert tone="warn">
              后端暂时无法确认本次尝试是否已领取。为避免重复发送，本窗口不会允许再次执行或关闭；
              恢复本地服务后请点“刷新尝试状态”。
            </Alert>
          ) : null}
        </Modal>
      ) : null}

      {skipTarget ? (
        <Modal
          title="跳过这个岗位？"
          onClose={() => setSkipTarget(null)}
          footer={
            <>
              <button type="button" onClick={() => setSkipTarget(null)}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  const proposal = skipTarget
                  setSkipTarget(null)
                  void run(proposal.job_id, () => api.skipJob(proposal.job_id, skipReason))
                }}
              >
                确认跳过
              </button>
            </>
          }
        >
          <p className="mt-0">
            <strong>{skipTarget.company}</strong> · {skipTarget.title}
          </p>
          <div className="field">
            <label>跳过原因（可选）</label>
            <div className="chip-list">
              {skipReasons.map((reason) => (
                <button
                  key={reason}
                  type="button"
                  className={skipReason === reason ? 'btn-primary btn-sm' : 'btn-sm'}
                  onClick={() => setSkipReason(reason)}
                >
                  {reason}
                </button>
              ))}
            </div>
          </div>
          <div className="field-hint">跳过之后仍然可以在岗位详情里「恢复待处理」。</div>
        </Modal>
      ) : null}
    </>
  )
}
