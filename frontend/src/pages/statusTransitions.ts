// A mirror of `application_workflow.ALLOWED_TRANSITIONS`.
//
// The backend is the authority: it refuses an illegal transition with a 422.
// This copy exists only so the UI can grey out a button that could not have
// worked, instead of offering a click whose sole possible outcome is an error
// toast. `backend/tests/test_status_transitions_mirror.py` fails if the two
// ever disagree.

import type { JobStatus } from '@/types'

export const ALLOWED_TRANSITIONS: Record<JobStatus, readonly JobStatus[]> = {
  new: ['applied', 'skipped', 'saved', 'reviewed'],
  reviewed: ['applied', 'skipped', 'saved'],
  saved: ['applied', 'skipped', 'reviewed'],
  skipped: [],
  applied: ['replied', 'interview', 'offer', 'rejected'],
  replied: ['interview', 'offer', 'rejected'],
  interview: ['offer', 'rejected', 'interview'],
  offer: ['rejected'],
  rejected: [],
}

export function canTransition(from: JobStatus, to: JobStatus): boolean {
  return ALLOWED_TRANSITIONS[from]?.includes(to) ?? false
}

/** Why a status button is unavailable, or null when it is available. */
export function transitionBlockedReason(from: JobStatus, to: JobStatus): string | null {
  if (from === to) return '该岗位已是此状态'
  if (canTransition(from, to)) return null
  return '当前状态不支持此操作，需先「恢复待处理」'
}
