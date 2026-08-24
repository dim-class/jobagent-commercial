// Small presentational building blocks shared across pages.

import { useEffect, type ReactNode } from 'react'
import type { JobStatus, Verdict } from '@/types'

// --------------------------------------------------------------------- maps

export const VERDICT_LABELS: Record<Verdict, string> = {
  strong_apply: '强烈推荐',
  apply: '推荐投递',
  maybe: '可以考虑',
  skip: '不建议',
}

export const STATUS_LABELS: Record<JobStatus, string> = {
  new: '待处理',
  reviewed: '已查看',
  saved: '已收藏',
  skipped: '已跳过',
  applied: '已投递',
  replied: '已回复',
  interview: '面试中',
  offer: '已offer',
  rejected: '已拒绝',
}

const VERDICT_TONE: Record<Verdict, string> = {
  strong_apply: 'badge-good',
  apply: 'badge-ok',
  maybe: 'badge-warn',
  skip: 'badge-bad',
}

const STATUS_TONE: Partial<Record<JobStatus, string>> = {
  new: 'badge-neutral',
  reviewed: 'badge-neutral',
  saved: 'badge-ok',
  skipped: 'badge-neutral',
  applied: 'badge-ok',
  replied: 'badge-good',
  interview: 'badge-good',
  offer: 'badge-good',
  rejected: 'badge-bad',
}

export function scoreTone(score: number): string {
  if (score >= 80) return 'score-good'
  if (score >= 70) return 'score-ok'
  if (score >= 60) return 'score-warn'
  return 'score-bad'
}

// ------------------------------------------------------------------ badges

export function ScoreBadge({ score, large }: { score: number | null | undefined; large?: boolean }) {
  if (score === null || score === undefined) {
    return <span className={`score score-none${large ? ' score-lg' : ''}`}>未分析</span>
  }
  return (
    <span className={`score ${scoreTone(score)}${large ? ' score-lg' : ''}`}>
      <span className="score-num">{score}</span>
      <span className="score-max">/100</span>
    </span>
  )
}

export function VerdictBadge({ verdict }: { verdict: Verdict | null | undefined }) {
  if (!verdict) return <span className="badge badge-neutral">未分析</span>
  return <span className={`badge ${VERDICT_TONE[verdict]}`}>{VERDICT_LABELS[verdict]}</span>
}

export function StatusBadge({ status }: { status: JobStatus }) {
  return <span className={`badge ${STATUS_TONE[status] ?? 'badge-neutral'}`}>{STATUS_LABELS[status]}</span>
}

// ------------------------------------------------------------------ states

export function Loading({ text = '加载中…' }: { text?: string }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden />
      <span>{text}</span>
    </div>
  )
}

export function EmptyState({
  icon = '📭',
  title,
  text,
  action,
}: {
  icon?: string
  title: string
  text?: string
  action?: ReactNode
}) {
  return (
    <div className="empty">
      <div className="empty-icon" aria-hidden>
        {icon}
      </div>
      <div className="empty-title">{title}</div>
      {text ? <div className="empty-text">{text}</div> : null}
      {action}
    </div>
  )
}

export type AlertTone = 'info' | 'success' | 'warn' | 'error'

const ALERT_ICON: Record<AlertTone, string> = {
  info: 'ℹ️',
  success: '✅',
  warn: '⚠️',
  error: '⛔',
}

export function Alert({
  tone = 'info',
  children,
  onDismiss,
}: {
  tone?: AlertTone
  children: ReactNode
  onDismiss?: () => void
}) {
  return (
    <div className={`alert alert-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <span aria-hidden>{ALERT_ICON[tone]}</span>
      <div className="alert-body">{children}</div>
      {onDismiss ? (
        <button type="button" className="btn-ghost btn-sm" onClick={onDismiss} aria-label="关闭提示">
          ✕
        </button>
      ) : null}
    </div>
  )
}

export function Card({
  title,
  sub,
  actions,
  children,
}: {
  title?: string
  sub?: string
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="card">
      {title || actions ? (
        <header className="card-head">
          <div>
            <div className="card-title">{title}</div>
            {sub ? <div className="card-sub">{sub}</div> : null}
          </div>
          {actions}
        </header>
      ) : null}
      {children}
    </section>
  )
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <section className="card stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {hint ? <span className="stat-hint">{hint}</span> : null}
    </section>
  )
}

export function SubScore({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="subscore">
      <div className="subscore-label">{label}</div>
      <div className="subscore-value">{value === null ? '—' : value}</div>
      <div className="bar">
        <span style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
      </div>
    </div>
  )
}

export function BulletList({ items, empty = '无' }: { items: string[]; empty?: string }) {
  if (!items.length) return <p className="faint small mt-0">{empty}</p>
  return (
    <ul className="bullet-list">
      {items.map((item, index) => (
        <li key={`${index}-${item.slice(0, 12)}`}>{item}</li>
      ))}
    </ul>
  )
}

export function ChipList({ items, tone }: { items: string[]; tone?: 'good' | 'bad' }) {
  if (!items.length) return <p className="faint small mt-0">无</p>
  const className = tone ? `chip chip-${tone}` : 'chip'
  return (
    <div className="chip-list">
      {items.map((item, index) => (
        <span key={`${index}-${item}`} className={className}>
          {item}
        </span>
      ))}
    </div>
  )
}

// ------------------------------------------------------------------- modal

export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="modal">
        <header className="modal-head">
          <h2>{title}</h2>
          <button type="button" className="btn-ghost btn-sm" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer ? <footer className="modal-foot">{footer}</footer> : null}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ format

export function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
