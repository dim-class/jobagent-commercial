import { useState } from 'react'

import { ApiError, api } from '@/api/client'
import { Card } from '@/components/ui'
import type { AppliedBackfillMatch, AppliedBackfillPlan } from '@/types'

/** A pasted block is split on the newline character itself. */
const NEWLINE = String.fromCharCode(10)

/**
 * Record applications the user already made on BOSS, from a list they copied.
 *
 * JobAgent never opens or reads the recruitment site here - the text is present
 * because a human selected it and pressed Ctrl+C. That is deliberately more
 * restrictive than the suspended conversation scan, and needs none of it back.
 */
export default function AppliedBackfillPanel({ onDone }: { onDone?: () => void }) {
  const [text, setText] = useState('')
  const [plan, setPlan] = useState<AppliedBackfillPlan | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [confirming, setConfirming] = useState(false)

  async function readPlan() {
    if (busy || !text.trim()) return
    setBusy(true); setError(''); setMessage(''); setConfirming(false)
    try {
      const result = await api.planAppliedBackfill(text)
      setPlan(result)
      // Only the confident matches start selected. A company-only hit is the
      // reader's decision: several roles at one company is normal, and the
      // wrong pick records an application that did not happen.
      setSelected(new Set(result.confident.map(row => row.job_id)))
    } catch (err) {
      setPlan(null)
      setError(err instanceof ApiError ? err.message : '解析失败')
    } finally {
      setBusy(false)
    }
  }

  async function record() {
    if (busy || !selected.size) return
    setBusy(true); setError('')
    try {
      const ids = [...selected]
      const result = await api.confirmAppliedBackfill(ids, ids.length)
      setMessage(
        `已补录 ${result.recorded.length} 个`
        + (result.skipped.length ? `；${result.skipped.length} 个未能记录` : ''),
      )
      setPlan(null); setSelected(new Set()); setText(''); setConfirming(false)
      onDone?.()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '补录失败')
      setConfirming(false)
    } finally {
      setBusy(false)
    }
  }

  function toggle(jobId: number) {
    // Any change invalidates a pending confirmation: the count shown must
    // always be the count recorded, and the backend refuses a mismatch.
    setConfirming(false)
    setSelected(current => {
      const next = new Set(current)
      if (next.has(jobId)) next.delete(jobId)
      else next.add(jobId)
      return next
    })
  }

  function rows(items: AppliedBackfillMatch[], selectable: boolean) {
    return items.map(row => (
      <tr key={row.job_id}>
        <td>
          {selectable ? (
            <input
              type="checkbox"
              aria-label={`选择 ${row.company} ${row.title}`}
              checked={selected.has(row.job_id)}
              onChange={() => toggle(row.job_id)}
            />
          ) : null}
        </td>
        <td>
          <div className="cell-title">{row.company}</div>
          <div className="cell-sub">{row.title}</div>
        </td>
        <td className="small">{row.reason || '公司名与职位名都匹配'}</td>
      </tr>
    ))
  }

  const pasteLines = text.split(NEWLINE).filter(line => line.trim()).length

  return (
    <Card title="补录已投递" sub="把你在 BOSS 上已经投过的岗位记录进来">
      <p className="small faint">
        在 BOSS 打开「消息」或「我的 → 沟通过的职位」，选中列表复制，粘贴到下面。
        JobAgent 不会打开、点击或读取 BOSS 的任何页面——内容是你自己复制过来的。
      </p>

      <div className="field mt-1">
        <label htmlFor="backfill-text">粘贴内容</label>
        <textarea
          id="backfill-text"
          rows={6}
          value={text}
          placeholder="阿里云  云计算技术服务工程师&#10;搜狐  运维开发工程师（SRE岗）&#10;…"
          onChange={e => { setText(e.target.value); setPlan(null); setConfirming(false) }}
        />
        {pasteLines ? <p className="small faint">{pasteLines} 行</p> : null}
      </div>

      <div className="row mt-1">
        <button type="button" className="btn-primary" disabled={busy || !text.trim()} onClick={() => void readPlan()}>
          {busy && !plan ? '解析中…' : '在岗位库中匹配'}
        </button>
        <span className="small faint">匹配不消耗 AI 额度，也不会改动任何状态。</span>
      </div>

      {error ? <p className="small mt-1" role="alert">{error}</p> : null}
      {message ? <p className="small mt-1">{message}</p> : null}

      {plan ? (
        <div className="mt-1">
          {plan.notes.map(note => (
            <p key={note} className="small faint">{note}</p>
          ))}

          {plan.confident.length ? (
            <>
              <p className="mt-1"><strong>可直接补录（{plan.confident.length}）</strong></p>
              <div className="table-wrap">
                <table>
                  <thead><tr><th /><th>公司 / 职位</th><th>依据</th></tr></thead>
                  <tbody>{rows(plan.confident, true)}</tbody>
                </table>
              </div>
            </>
          ) : null}

          {plan.needs_review.length ? (
            <>
              <p className="mt-1">
                <strong>需要你判断（{plan.needs_review.length}）</strong>
                <span className="small faint"> · 默认不勾选</span>
              </p>
              <div className="table-wrap">
                <table>
                  <thead><tr><th /><th>公司 / 职位</th><th>原因</th></tr></thead>
                  <tbody>{rows(plan.needs_review.filter(r => r.can_apply), true)}</tbody>
                </table>
              </div>
              {plan.needs_review.some(r => !r.can_apply) ? (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th /><th>公司 / 职位</th><th>不能补录的原因</th></tr></thead>
                    <tbody>{rows(plan.needs_review.filter(r => !r.can_apply), false)}</tbody>
                  </table>
                </div>
              ) : null}
            </>
          ) : null}

          {plan.already_applied.length ? (
            <>
              <p className="mt-1">
                <strong>此前已记录（{plan.already_applied.length}）</strong>
                <span className="small faint"> · 不会重复记录</span>
              </p>
              <div className="table-wrap">
                <table>
                  <thead><tr><th /><th>公司 / 职位</th><th>说明</th></tr></thead>
                  <tbody>{rows(plan.already_applied, false)}</tbody>
                </table>
              </div>
            </>
          ) : null}

          {confirming ? (
            <div className="card-block mt-1" role="alert">
              <p>
                <strong>确认把这 {selected.size} 个岗位记为「已投递」？</strong>
              </p>
              <p className="small faint">
                这只是补录你已经完成的动作：JobAgent 不会投递、不会打招呼、不会发任何消息。
                每个岗位各写一条记录，事后可以逐个「恢复待处理」撤销。
              </p>
              <div className="row mt-1">
                <button type="button" className="btn-primary" disabled={busy} onClick={() => void record()}>
                  {busy ? '记录中…' : `确认补录 ${selected.size} 个`}
                </button>
                <button type="button" className="btn-sm" disabled={busy} onClick={() => setConfirming(false)}>
                  取消
                </button>
              </div>
            </div>
          ) : (
            <div className="row mt-1">
              <button
                type="button"
                className="btn-primary"
                disabled={busy || !selected.size}
                onClick={() => setConfirming(true)}
              >
                标记选中的 {selected.size} 个为已投递
              </button>
            </div>
          )}
        </div>
      ) : null}
    </Card>
  )
}
