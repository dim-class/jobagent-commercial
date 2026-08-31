"use strict";
/** Narrow console transport, not a browser executor. No task polling or auto-start. */
(() => {
    const origins = ['http://127.0.0.1:5173', 'http://localhost:5173'];
    if (window.top !== window || !origins.includes(location.origin))
        return;
    window.addEventListener('message', event => {
        if (event.source !== window || event.origin !== location.origin || location.pathname !== '/'
            || !['#/console', '#/queue', '#/recruiter'].includes(location.hash))
            return;
        const data = event.data;
        if (!data || data.channel !== 'jobagent-console-request' || typeof data.id !== 'string'
            || data.id.length > 80 || !['status', 'start', 'pause', 'resume', 'cancel',
            'start-batch', 'pause-batch', 'resume-batch', 'cancel-batch',
            'start-salary-backfill', 'pause-salary-backfill', 'resume-salary-backfill',
            'cancel-salary-backfill', 'execute-application'].includes(data.action || ''))
            return;
        const reply = (result) => window.postMessage({
            channel: 'jobagent-console-response', id: data.id, result,
        }, location.origin);
        // A page load/timer must not initiate browser work. Human button activation only.
        if (data.action !== 'status' && !navigator.userActivation.isActive) {
            reply({ ok: false, error: '请点击控制台按钮执行；不接受后台自动启动。' });
            return;
        }
        // Diagnostic receipt is NOT worker success or permission to start anything.
        if (data.action === 'status')
            window.postMessage({
                channel: 'jobagent-console-receipt', id: data.id, protocol: 1,
            }, location.origin);
        try {
            chrome.runtime.sendMessage({ type: 'jobagent:console-command', action: data.action,
                taskId: data.taskId, taskIds: data.taskIds, candidateCap: data.candidateCap,
                runId: data.runId, approvalId: data.approvalId, jobId: data.jobId }, result => {
                if (chrome.runtime.lastError)
                    reply({ ok: false, code: 'worker_unavailable',
                        error: '桥接脚本已响应，但扩展后台不可达。请刷新控制台后重新检查；不会启动任务。' });
                else
                    reply(result);
            });
        }
        catch {
            reply({ ok: false, code: 'extension_context_unavailable',
                error: '桥接脚本已响应，但扩展上下文不可用（可能已更新或停用）。请刷新控制台后重新检查。' });
        }
    });
})();
