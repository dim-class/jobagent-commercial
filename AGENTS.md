# Codex supervisor

- The user communicates with Codex only. Claude Code is the implementation worker.
- Read `CLAUDE.md`, `docs/orchestration/STATUS.md`, and this file before delegating.
- Put one complete, bounded milestone in `docs/orchestration/TASK.md`; never paste source or
  duplicate `CLAUDE.md` into a prompt.
- Invoke Claude only through `scripts/orchestration/invoke-claude.ps1`. One invocation must
  implement, test, fix failures, and write a concise `docs/orchestration/RESULT.md`.
- Review `RESULT.md`, then inspect the actual Git diff and run focused acceptance tests. If this
  checkout has no `.git`, say so and review the explicit changed-file set instead; do not pretend
  a diff was reviewed.
- Redelegate only after a concrete acceptance failure. Keep delta tasks short and reuse the stored
  Claude session.
- Never inspect, print, or place secrets in TASK/RESULT/status files. Do not expose `.env` values.
- Keep `STATUS.md` to current state, blocker, and next action. Do not change product scope during
  orchestration maintenance.
