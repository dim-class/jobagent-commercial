// 本次实际投递简历 picker (v0.7)
//
// Shared by every "确认已投递" dialog so the question is always asked the same
// way. The selection defaults to the active analysis resume purely as a
// convenience - it is a *suggestion*, and whatever the user leaves here is what
// gets recorded. Nothing downstream substitutes the active resume for a blank
// answer: unanswered is stored as `unknown`.

import { useEffect, useState } from 'react'

import { api } from '@/api/client'
import type { ResumeListItem, ResumeUsage } from '@/types'

export interface ResumeChoice {
  resumeId: number | null
  usage: ResumeUsage
}

export const UNKNOWN_CHOICE: ResumeChoice = { resumeId: null, usage: 'unknown' }

/** Load selectable variants, newest first, archived ones excluded. */
export function useSelectableResumes(): {
  resumes: ResumeListItem[]
  activeId: number | null
  loaded: boolean
} {
  const [resumes, setResumes] = useState<ResumeListItem[]>([])
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .listResumes()
      .then((list) => {
        if (cancelled) return
        setResumes(list)
        setLoaded(true)
      })
      .catch(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const active = resumes.find((r) => r.is_active)
  return { resumes, activeId: active ? active.id : null, loaded }
}

export function ResumePicker({
  resumes,
  value,
  onChange,
  analyzedResumeId,
}: {
  resumes: ResumeListItem[]
  value: ResumeChoice
  onChange: (choice: ResumeChoice) => void
  /** The variant this job was analysed against, if known - drives the notice. */
  analyzedResumeId?: number | null
}) {
  const selectedId = value.usage === 'used' ? value.resumeId : null
  const analyzed = resumes.find((r) => r.id === analyzedResumeId)
  const selected = resumes.find((r) => r.id === selectedId)

  // Purely informational: applying with a different variant than the one the
  // job was scored against is perfectly valid, and never blocked.
  const mismatch =
    analyzed && selected && analyzed.id !== selected.id ? { analyzed, selected } : null

  return (
    <div className="field">
      <label>你实际使用了哪份简历？</label>
      {!resumes.length ? (
        <p className="faint small">还没有可选的简历版本，将记录为「不确定」。</p>
      ) : (
        <div className="stack">
          {resumes.map((resume) => (
            <label key={resume.id} className="checkbox-row">
              <input
                type="radio"
                name="applied-resume"
                checked={selectedId === resume.id}
                onChange={() => onChange({ resumeId: resume.id, usage: 'used' })}
              />
              <span>
                {resume.label}
                {resume.is_active ? <span className="chip">当前AI分析简历</span> : null}
              </span>
            </label>
          ))}
          <label className="checkbox-row">
            <input
              type="radio"
              name="applied-resume"
              checked={value.usage === 'no_resume'}
              onChange={() => onChange({ resumeId: null, usage: 'no_resume' })}
            />
            <span>未提交简历（仅沟通）</span>
          </label>
          <label className="checkbox-row">
            <input
              type="radio"
              name="applied-resume"
              checked={value.usage === 'unknown'}
              onChange={() => onChange(UNKNOWN_CHOICE)}
            />
            <span>不确定</span>
          </label>
        </div>
      )}

      {mismatch ? (
        <p className="field-hint">
          当前职位分析基于「{mismatch.analyzed.label}」，但你选择使用「{mismatch.selected.label}
          」投递。这完全可以，只是分析结果对应的是另一份简历。
        </p>
      ) : null}

      <p className="field-hint">
        这条记录只用于统计哪份简历更有效，不会改变「当前AI分析简历」。
      </p>
    </div>
  )
}
