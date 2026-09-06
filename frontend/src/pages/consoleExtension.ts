export type ConsoleAction = 'status' | 'start' | 'pause' | 'resume' | 'cancel'
  | 'start-batch' | 'pause-batch' | 'resume-batch' | 'cancel-batch'
  | 'start-salary-backfill' | 'pause-salary-backfill' | 'resume-salary-backfill'
  | 'cancel-salary-backfill' | 'execute-application'
  //: Read-only: reports the filters already set on a BOSS tab the human
  //: has open. No navigation, no DOM, no page change.
  | 'read-search-filters'
  //: Read-only: BOSS's own salary bands and codes, off the open results page.
  | 'read-salary-filter'
export interface ConsoleBatchStatus {
  state: 'running' | 'paused' | 'completed' | 'stopped'
  taskIds: number[]
  currentIndex: number
  currentTaskId: number
  candidateCap: number
  requiresResume: boolean
  lastError: string | null
}
export interface ConsoleReply {
  ok: boolean
  /** A BOSS results URL carrying only reusable filter parameters, rebuilt by
   *  the extension from an allowlist - never the raw tab URL, which holds
   *  session tokens. Present only for `read-search-filters`. */
  filterUrl?: string
  /** BOSS's own salary bands, label and code, read off the open results page.
   *  Present only for `read-salary-filter`. */
  options?: { label: string; code: string }[]
  error?: string
  code?: string
  protocol?: number
  extensionVersion?: string
  capabilities?: string[]
  /** The ceilings the loaded extension build enforces. Absent on an older
   *  build - which is itself the answer when a start is refused. */
  limits?: { target: number; opens: number; scrolls: number; pages: number }
  runner?: { taskId: number; phase: string; paused: boolean; paid: boolean; candidateCap: number } | null
  batch?: ConsoleBatchStatus | null
  salaryBackfill?: { runId: number; active: boolean; updatedAt: string } | null
  application?: { approvalId: number; outcome: 'unknown' | 'failed'; detail: string } | null
  scan?: { conversations: number; matched: number; skipped: number; scroll_rounds: number;
    observed: number; imported: number; duplicates: number; ai_used: false } | null
}

export const DEFAULT_BATCH_CANDIDATE_CAP = 1
export const MAX_CONSOLE_BATCH_TASKS = 16

export class ConsoleConnectionError extends Error {
  readonly code: string
  constructor(code: string, message: string) {
    super(message)
    this.name = 'ConsoleConnectionError'
    this.code = code
  }
}

/** Deterministic UI-only selection; the worker still validates every id/task. */
export function selectBoundedPendingTasks<T extends { id: number; state: string | null }>(tasks: T[], count: number): T[] {
  if (!Number.isInteger(count) || count < 1 || count > MAX_CONSOLE_BATCH_TASKS) {
    throw new Error(`批次任务数必须是 1–${MAX_CONSOLE_BATCH_TASKS} 的整数。`)
  }
  return tasks.filter(task => task.state === 'pending').slice(0, count)
}

/** Handshake metadata is diagnostic only; the worker still validates every action. */
export function assessConsoleConnection(reply: ConsoleReply): { ready: boolean; detail: string } {
  if (!reply.ok) return { ready: false, detail: reply.error || '扩展后台拒绝了状态检查；请刷新控制台。' }
  if (reply.protocol !== 1) return { ready: false, detail: '扩展协议不兼容；需安装匹配版本后刷新控制台。' }
  if (!reply.extensionVersion || !Array.isArray(reply.capabilities)) {
    return { ready: false, detail: '旧版扩展已响应，但缺少版本/能力握手；需更新后刷新控制台。' }
  }
  if (typeof reply.extensionVersion !== 'string' || !/^\d+(\.\d+){0,3}$/.test(reply.extensionVersion)
    || !reply.capabilities.includes('console-search-v1')) {
    return { ready: false, detail: '扩展缺少兼容的控制台采集能力；不会启用启动按钮。' }
  }
  const runner = reply.runner
  if (runner !== null && (!runner || !Number.isSafeInteger(runner.taskId) || runner.taskId < 1
    || typeof runner.phase !== 'string' || runner.phase.length > 80
    || typeof runner.paused !== 'boolean' || typeof runner.paid !== 'boolean'
    || !Number.isInteger(runner.candidateCap) || runner.candidateCap < 1 || runner.candidateCap > 20)) {
    return { ready: false, detail: '扩展运行状态响应无效；不会把未知状态当作空闲。' }
  }
  const batch = reply.batch
  if (batch !== null && batch !== undefined && (!batch || !['running', 'paused', 'completed', 'stopped'].includes(batch.state)
    || !Array.isArray(batch.taskIds) || batch.taskIds.length < 1 || batch.taskIds.length > MAX_CONSOLE_BATCH_TASKS
    || new Set(batch.taskIds).size !== batch.taskIds.length
    || !batch.taskIds.every(id => Number.isSafeInteger(id) && id > 0)
    || !Number.isInteger(batch.currentIndex) || batch.currentIndex < 0 || batch.currentIndex >= batch.taskIds.length
    || batch.currentTaskId !== batch.taskIds[batch.currentIndex]
    || !Number.isInteger(batch.candidateCap) || batch.candidateCap < 1 || batch.candidateCap > 20
    || typeof batch.requiresResume !== 'boolean'
    || (batch.lastError !== null && typeof batch.lastError !== 'string'))) {
    return { ready: false, detail: '扩展批次状态响应无效；不会启用批次操作。' }
  }
  const batchReady = reply.capabilities.includes('console-batch-v1') ? ' · 有界批次可用' : ''
  return { ready: true, detail: `版本 ${reply.extensionVersion} · 协议 1 · 免费采集能力已就绪${batchReady}` }
}

/** One bounded request. Timeouts are UNKNOWN, never an automatic retry. */
export function consoleExtension(action: ConsoleAction, taskId?: number, candidateCap?: number,
  taskIds?: number[], runId?: number, approvalId?: number, jobId?: number,
  //: Let a *search* continue with the BOSS tab behind other windows
  //: (authorized 2026-09-04). Applying is never backgrounded.
  background?: boolean): Promise<ConsoleReply> {
  return new Promise((resolve, reject) => {
    const id = crypto.randomUUID()
    let bridgeSeen = false
    const listener = (event: MessageEvent) => {
      if (event.source !== window || event.origin !== location.origin
        || event.data?.id !== id) return
      if (action === 'status' && event.data?.channel === 'jobagent-console-receipt' && event.data.protocol === 1) {
        bridgeSeen = true
        return // does not extend the deadline or acknowledge the command
      }
      if (event.data?.channel !== 'jobagent-console-response') return
      clearTimeout(timer)
      window.removeEventListener('message', listener)
      const result = event.data.result as ConsoleReply | undefined
      if (!result || typeof result.ok !== 'boolean') reject(new ConsoleConnectionError('invalid_response', '扩展响应无效，请刷新状态。'))
      else resolve(result)
    }
    const timer = window.setTimeout(() => {
      window.removeEventListener('message', listener)
      reject(new ConsoleConnectionError(action === 'status' ? (bridgeSeen ? 'worker_timeout' : 'bridge_no_response') : 'command_unknown',
        action === 'status'
          ? (bridgeSeen ? '桥接脚本已响应，但扩展后台状态检查超时；请刷新状态，不会自动启动任务。'
            : '未收到控制台桥接回应，原因未确定。请确认正在使用装有 JobAgent 的正常 Chrome，并检查是否启用、允许访问本地控制台及版本是否匹配；不能据此断言扩展未安装。')
          : '执行结果尚未确认，请刷新状态，不要重复启动；不会自动重试。'))
    }, action === 'status' ? 2500
      : action === 'execute-application' ? 30000 : 15000)
    window.addEventListener('message', listener)
    window.postMessage({ channel: 'jobagent-console-request', id, action, taskId, taskIds,
      candidateCap, runId, approvalId, jobId, background }, location.origin)
  })
}
