# Claude Worker Task

State: proof 1 (read-only)

## Objective

Prove the persistent worker can read repository instructions without changing product code.

## Scope

- Read `CLAUDE.md` and this task.
- Read only the names of the repository's top-level entries.
- Write `docs/orchestration/RESULT.md` with `proof_step: 1`, confirmation that both instruction
  files were read, and the number of top-level directories observed.

## Exclusions

- Do not modify product code, configuration, tests, build output, STATUS.md, or TASK.md.
- Do not inspect `.env`, credentials, browser data, databases, or other secrets.
- Do not run builds, tests, network calls, or implementation work.

## Acceptance

- Only `docs/orchestration/RESULT.md` changes during the worker invocation.
- The result is concise and contains no repository source or secret values.
