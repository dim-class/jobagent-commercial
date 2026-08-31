[CmdletBinding()]
param(
    [ValidateRange(0.1, 100.0)]
    [decimal]$MaxBudgetUsd = 6.0
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$currentRoot = [System.IO.Path]::GetFullPath((Get-Location).ProviderPath)
if (-not $currentRoot.Equals($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Run this wrapper from the repository root: $repoRoot"
}

$orchestrationRoot = Join-Path $repoRoot "docs\orchestration"
$taskPath = Join-Path $orchestrationRoot "TASK.md"
$resultPath = Join-Path $orchestrationRoot "RESULT.md"
$sessionPath = Join-Path $orchestrationRoot ".claude-worker-session-id"
$lockPath = Join-Path $orchestrationRoot ".claude-worker.lock"

foreach ($requiredPath in @($taskPath, $resultPath, (Join-Path $repoRoot "CLAUDE.md"))) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required orchestration file is missing: $requiredPath"
    }
}

$lockStream = $null
$lockOwned = $false
try {
    try {
        $lockStream = [System.IO.File]::Open(
            $lockPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $lockText = [System.Text.Encoding]::UTF8.GetBytes(
            "pid=$PID`nstarted_utc=$([DateTime]::UtcNow.ToString('o'))`n"
        )
        $lockStream.Write($lockText, 0, $lockText.Length)
        $lockStream.Flush()
        $lockOwned = $true
    }
    catch [System.IO.IOException] {
        throw "Claude worker is already running, or a stale lock exists: $lockPath"
    }

    $claudeCommand = $null
    if ($env:CLAUDE_CLI_PATH) {
        if (-not (Test-Path -LiteralPath $env:CLAUDE_CLI_PATH -PathType Leaf)) {
            throw "CLAUDE_CLI_PATH does not point to a file."
        }
        $claudeCommand = (Resolve-Path -LiteralPath $env:CLAUDE_CLI_PATH).Path
    }
    else {
        $resolved = Get-Command "claude" -CommandType Application -ErrorAction SilentlyContinue
        if ($resolved) {
            $claudeCommand = $resolved.Source
        }
        else {
            $fallback = Join-Path $env:USERPROFILE ".local\bin\claude.exe"
            if (Test-Path -LiteralPath $fallback -PathType Leaf) {
                $claudeCommand = (Resolve-Path -LiteralPath $fallback).Path
            }
        }
    }
    if (-not $claudeCommand) {
        throw "Claude CLI was not found. Install it or set CLAUDE_CLI_PATH."
    }

    $isResume = Test-Path -LiteralPath $sessionPath -PathType Leaf
    if ($isResume) {
        $storedText = (Get-Content -LiteralPath $sessionPath -Raw).Trim()
        $parsedSessionId = [Guid]::Empty
        if (-not [Guid]::TryParse($storedText, [ref]$parsedSessionId)) {
            throw "Stored Claude session id is invalid: $sessionPath"
        }
        $sessionId = $parsedSessionId.ToString()
    }
    else {
        $sessionId = [Guid]::NewGuid().ToString()
    }

    $resultHashBefore = $null
    if (Test-Path -LiteralPath $resultPath -PathType Leaf) {
        $resultHashBefore = (Get-FileHash -LiteralPath $resultPath -Algorithm SHA256).Hash
    }

    $workerPrompt = @"
You are the single persistent Claude Code implementation worker for this repository.
Read CLAUDE.md and docs/orchestration/TASK.md completely. TASK.md is the only current delegation.
Implement the task, run its relevant tests, and fix failures within this invocation whenever possible.
Do not ask what to do next. Do not inspect or expose secrets. Do not paste repository source into reports.
Write docs/orchestration/RESULT.md before finishing. Keep it concise: files changed, key behavior,
exact test/build counts, blockers, and remaining live verification. Do not include full logs.
For a read-only proof task, product files must remain unchanged; updating RESULT.md is still required.
"@

    $budget = $MaxBudgetUsd.ToString([System.Globalization.CultureInfo]::InvariantCulture)
    $claudeArgs = @(
        "--print",
        "--output-format", "json",
        "--max-budget-usd", $budget,
        "--append-system-prompt", "Supervisor handoff: the current on-disk docs/orchestration/TASK.md supersedes all unfinished work and background notifications from resumed history. Your first tool action must read TASK.md anew. Do not resume a previous command or rerun old tests before that read. Follow its current test limits. Do not use git stash, reset, checkout, or clean; preserve this dirty worktree.",
        "--no-chrome",
        "--disable-slash-commands",
        "--dangerously-skip-permissions"
    )
    if ($isResume) {
        $claudeArgs += @("--resume", $sessionId)
    }
    else {
        $claudeArgs += @("--session-id", $sessionId, "--name", "jobagent-codex-worker")
    }
    $claudeArgs += $workerPrompt

    $rawOutput = @(& $claudeCommand @claudeArgs 2>&1)
    $claudeExitCode = $LASTEXITCODE
    $joinedOutput = $rawOutput -join "`n"

    $payload = $null
    try {
        $payload = $joinedOutput | ConvertFrom-Json
    }
    catch {
        if ($isResume) {
            throw "Stored Claude session $sessionId could not be resumed (exit $claudeExitCode)."
        }
        throw "Claude worker did not return valid JSON (exit $claudeExitCode)."
    }

    if ($null -eq $payload -or $payload -is [array]) {
        throw "Claude worker returned no single result object (exit $claudeExitCode); task not accepted."
    }
    if ($claudeExitCode -ne 0 -or $payload.is_error) {
        $hasResult = $payload.PSObject.Properties.Name -contains "result"
        $reason = if ($hasResult -and $payload.result) { [string]$payload.result } else { "Claude CLI failed." }
        if ($reason.Length -gt 500) { $reason = $reason.Substring(0, 500) + "..." }
        if ($isResume) {
            throw "Stored Claude session $sessionId could not be resumed or complete the task: $reason"
        }
        throw "Claude worker session $sessionId could not be created: $reason"
    }

    $returnedSessionId = [Guid]::Empty
    if (-not [Guid]::TryParse([string]$payload.session_id, [ref]$returnedSessionId)) {
        throw "Claude returned no valid session identity."
    }
    if ($returnedSessionId.ToString() -ne $sessionId) {
        throw "Claude returned a different session id; refusing to continue."
    }

    if (-not $isResume) {
        [System.IO.File]::WriteAllText(
            $sessionPath,
            "$sessionId`n",
            [System.Text.UTF8Encoding]::new($false)
        )
    }

    if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) {
        throw "Claude finished without writing RESULT.md."
    }
    $resultFile = Get-Item -LiteralPath $resultPath
    if ($resultFile.Length -eq 0) {
        throw "Claude wrote an empty RESULT.md."
    }
    if ($resultFile.Length -gt 12288) {
        throw "RESULT.md is not concise (maximum 12 KiB)."
    }
    $resultHashAfter = (Get-FileHash -LiteralPath $resultPath -Algorithm SHA256).Hash
    if ($resultHashBefore -eq $resultHashAfter) {
        throw "Claude did not update RESULT.md for this delegation."
    }

    Write-Output "CLAUDE_WORKER_SESSION_ID=$sessionId"
    Write-Output "CLAUDE_WORKER_REUSED=$($isResume.ToString().ToLowerInvariant())"
    Write-Output "CLAUDE_WORKER_RESULT=$resultPath"
}
finally {
    if ($lockStream) {
        $lockStream.Dispose()
    }
    if ($lockOwned -and (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
        Remove-Item -LiteralPath $lockPath -Force
    }
}
