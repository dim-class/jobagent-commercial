# JobAgent Orchestration Status

- **Current state:** The reusable Codex-to-Claude worker architecture is configured; wrapper
  parsing, root enforcement, foreign-lock preservation, and failed-call cleanup pass. Live BOSS
  detail extraction reads title, salary, and URL; company, city, experience, education, and
  description are missing.
- **Blocker:** Anthropic rejected the first harmless worker proof because the Claude subscription
  session limit is reached until 23:30 Asia/Tokyo. No worker session id or lock was left behind.
- **Next action:** After the limit resets, run proof 1 and proof 2 through the wrapper and verify
  the same returned session id. Do not begin the BOSS implementation task during this setup.
