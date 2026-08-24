# Claude Worker Task

State: proof 2 (read-only resume)

## Objective

Prove the same persistent worker session can be resumed without changing product code.

## Scope

- Read `CLAUDE.md` and this task.
- Rely on the prior conversation context to confirm this is the continuation after proof 1.
- Read only the names of the repository's top-level entries.
- Write `docs/orchestration/RESULT.md` with `proof_step: 2`, confirmation that both instruction
  files were read, and confirmation that proof 1 is present in the resumed conversation context.

## Exclusions

- Do not modify product code, configuration, tests, build output, STATUS.md, or TASK.md.
- Do not inspect `.env`, credentials, browser data, databases, or other secrets.
- Do not run builds, tests, network calls, or implementation work.

## Acceptance

- Only `docs/orchestration/RESULT.md` changes during the worker invocation.
- The result is concise and contains no repository source or secret values.
