/**
 * Where the local JobAgent backend lives.
 *
 * Loopback only, and it must stay that way: `manifest.json` grants host
 * permission for exactly these origins, so pointing this at anything else
 * would simply fail rather than quietly send job data somewhere remote.
 */

// eslint-disable-next-line no-var
var JobAgentConfig = {
  BACKEND_BASE: 'http://127.0.0.1:8000',
  PREVIEW_PATH: '/api/extension/jobs/preview',
  IMPORT_PATH: '/api/extension/jobs/import',
  TASKS_PATH: '/api/tasks',
  SESSIONS_PATH: '/api/extension/sessions',
  SESSIONS_ACTIVE_PATH: '/api/extension/sessions/active',
}
