<#
.SYNOPSIS
    One-click local JobAgent console launcher for Windows.

.DESCRIPTION
    Starts the existing FastAPI backend and Vite frontend only when they are
    not already healthy, waits for both loopback services, then opens the
    simplified console. Processes started by this script are hidden and their
    listener PIDs are recorded under .tmp so Stop-JobAgent.cmd never stops an
    unrelated process.
#>

[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$NoOpen,
    [switch]$SingleProcess,
    # Restart the backend when its source changes. Off by default, because for
    # someone who is only *running* JobAgent a file watcher is pure overhead.
    # On while editing it: without this the backend keeps serving the code it
    # started with, and a change that is already on disk, built, and tested
    # simply does not take effect - which reads as a broken feature rather than
    # a stale process, and cost several rounds of exactly that.
    [switch]$Reload,
    [string]$DataDir,
    [ValidateRange(1024, 65535)][int]$BackendPort = 8000,
    [ValidateRange(1024, 65535)][int]$FrontendPort = 5173
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $Root 'backend'
$FrontendDir = Join-Path $Root 'frontend'
$Python = Join-Path $BackendDir '.venv\Scripts\python.exe'
$StateDir = Join-Path $Root '.tmp\launcher'
$BackendPidFile = Join-Path $StateDir "backend-$BackendPort.pid"
$FrontendPidFile = Join-Path $StateDir "frontend-$FrontendPort.pid"
$ConsoleUrl = if ($SingleProcess) {
    "http://127.0.0.1:$BackendPort/#/setup"
} else {
    "http://127.0.0.1:$FrontendPort/#/console"
}

function Get-ListenerPid([int]$Port) {
    $match = netstat -ano | Select-String "^\s*TCP\s+127\.0\.0\.1:$Port\s+.*LISTENING\s+(\d+)\s*$" |
        Select-Object -First 1
    if (-not $match) { return $null }
    return [int]$match.Matches[0].Groups[1].Value
}

function Test-Http([string]$Url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    } catch { return $false }
}

function Test-SingleProcessFrontend([string]$Url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
        return $response.StatusCode -eq 200 -and
            $response.Headers['Content-Type'] -like 'text/html*'
    } catch { return $false }
}

function Test-OwnedListener([int]$Port, [string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $savedPid = [int](Get-Content -LiteralPath $Path -Raw).Trim()
    $listenerPid = Get-ListenerPid $Port
    return $listenerPid -and $listenerPid -eq $savedPid
}

function Wait-Http([string]$Name, [string]$Url, [int]$Seconds = 40) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if (Test-Http $Url) { return }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "$Name did not become ready at $Url within $Seconds seconds. Check .tmp\launcher logs."
}

function Resolve-Npm {
    $command = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\nodejs\npm.cmd'),
        (Join-Path $env:ProgramFiles 'nodejs\npm.cmd'),
        (Join-Path ${env:ProgramFiles(x86)} 'nodejs\npm.cmd')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    throw 'Node.js/npm was not found. Install Node.js 20+ once, then double-click Start-JobAgent.cmd again.'
}

function Save-ListenerPid([int]$Port, [string]$Path) {
    $pidValue = Get-ListenerPid $Port
    if (-not $pidValue) { throw "No listener PID was found for port $Port after startup." }
    Set-Content -LiteralPath $Path -Value $pidValue -Encoding ascii
}

function ConvertTo-PowerShellLiteral([string]$Value) {
    return "'" + $Value.Replace("'", "''") + "'"
}

function Stop-OwnedListener([int]$Port, [string]$Path, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Host "$Name was not started by the one-click launcher; leaving it unchanged."
        return
    }
    $savedPid = [int](Get-Content -LiteralPath $Path -Raw).Trim()
    $listenerPid = Get-ListenerPid $Port
    if ($listenerPid -and $listenerPid -eq $savedPid) {
        Stop-Process -Id $savedPid -Force
        Write-Host "Stopped $Name (PID $savedPid)."
    } elseif ($listenerPid) {
        Write-Warning "$Name port $Port now belongs to PID $listenerPid, not saved PID $savedPid; leaving it unchanged."
    }
    Remove-Item -LiteralPath $Path -Force
}

New-Item -ItemType Directory -Path $StateDir -Force | Out-Null

if ($Stop) {
    Stop-OwnedListener $FrontendPort $FrontendPidFile 'JobAgent frontend'
    Stop-OwnedListener $BackendPort $BackendPidFile 'JobAgent backend'
    exit 0
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Backend environment is missing. Run scripts\dev.ps1 setup once before using the launcher.'
}

if ($SingleProcess -and -not (Test-Path -LiteralPath (Join-Path $FrontendDir 'dist\index.html'))) {
    throw 'Built frontend is missing. Run npm run build in frontend before using single-process mode.'
}

$healthUrl = "http://127.0.0.1:$BackendPort/health"
$startedBackend = $false
if (-not (Test-Http $healthUrl)) {
    if (Get-ListenerPid $BackendPort) {
        if (Test-OwnedListener $BackendPort $BackendPidFile) {
            Wait-Http 'JobAgent backend' $healthUrl
            Write-Host 'JobAgent backend is already running; reusing it.'
        } else {
            throw "Port $BackendPort is occupied by a service that is not a healthy JobAgent backend."
        }
    } else {
        $backendOut = Join-Path $StateDir 'backend.stdout.log'
        $backendErr = Join-Path $StateDir 'backend.stderr.log'
        # Defined before the branch: both start paths use one of these, and a
        # variable set inside the `if` would be undefined on the `else` path.
        $reloadFlag = if ($Reload) { ' --reload' } else { '' }
        $reloadArgs = if ($Reload) { @('--reload') } else { @() }
        if ($SingleProcess) {
            $runtimeData = if ($DataDir) {
                [System.IO.Path]::GetFullPath($DataDir)
            } else {
                Join-Path $env:LOCALAPPDATA 'JobAgent'
            }
            $frontendDist = Join-Path $FrontendDir 'dist'
            $runtimeDataLiteral = ConvertTo-PowerShellLiteral $runtimeData
            $frontendDistLiteral = ConvertTo-PowerShellLiteral $frontendDist
            $pythonLiteral = ConvertTo-PowerShellLiteral $Python
            $command = "`$env:JOBAGENT_DATA_DIR=$runtimeDataLiteral; " +
                "`$env:JOBAGENT_SERVE_FRONTEND='true'; " +
                "`$env:JOBAGENT_FRONTEND_DIR=$frontendDistLiteral; " +
                "& $pythonLiteral -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort$reloadFlag"
            Start-Process -FilePath 'powershell.exe' `
                -ArgumentList @('-NoProfile', '-Command', $command) `
                -WorkingDirectory $BackendDir -WindowStyle Hidden `
                -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr | Out-Null
        } else {
            Start-Process -FilePath $Python `
                -ArgumentList (@('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$BackendPort") + $reloadArgs) `
                -WorkingDirectory $BackendDir -WindowStyle Hidden `
                -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr | Out-Null
        }
        Wait-Http 'JobAgent backend' $healthUrl
        Save-ListenerPid $BackendPort $BackendPidFile
        $startedBackend = $true
        Write-Host 'JobAgent backend started.'
    }
} else {
    if ($SingleProcess -and -not (Test-SingleProcessFrontend "http://127.0.0.1:$BackendPort/")) {
        throw "Port $BackendPort is running JobAgent development mode. Stop it before starting commercial mode."
    }
    Write-Host 'JobAgent backend is already running; reusing it.'
}

try {
    if ($SingleProcess) {
        Write-Host 'JobAgent single-process frontend is served by the backend.'
        Write-Host "JobAgent is ready: $ConsoleUrl"
        if (-not $NoOpen) { Start-Process $ConsoleUrl }
        exit 0
    }
    $frontendUrl = "http://127.0.0.1:$FrontendPort/"
    if (-not (Test-Http $frontendUrl)) {
        if (Get-ListenerPid $FrontendPort) {
            if (Test-OwnedListener $FrontendPort $FrontendPidFile) {
                Wait-Http 'JobAgent frontend' $frontendUrl
                Write-Host 'JobAgent frontend is already running; reusing it.'
            } else {
                throw "Port $FrontendPort is occupied by a service that is not the JobAgent frontend."
            }
        } else {
            $npm = Resolve-Npm
            $nodeDir = Split-Path -Parent $npm
            $frontendOut = Join-Path $StateDir 'frontend.stdout.log'
            $frontendErr = Join-Path $StateDir 'frontend.stderr.log'
            # npm.cmd delegates to node.exe by name. Explicitly hand its directory
            # to the hidden child because GUI-launched PowerShell can have a much
            # narrower PATH than an interactive terminal.
            $nodePathLiteral = ConvertTo-PowerShellLiteral ($nodeDir + ';')
            $npmLiteral = ConvertTo-PowerShellLiteral $npm
            $command = "`$env:PATH=$nodePathLiteral+`$env:PATH; & $npmLiteral run dev -- --host 127.0.0.1 --port $FrontendPort --strictPort"
            Start-Process -FilePath 'powershell.exe' `
                -ArgumentList @('-NoProfile', '-Command', $command) `
                -WorkingDirectory $FrontendDir -WindowStyle Hidden `
                -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr | Out-Null
            Wait-Http 'JobAgent frontend' $frontendUrl
            Save-ListenerPid $FrontendPort $FrontendPidFile
            Write-Host 'JobAgent frontend started.'
        }
    } else {
        Write-Host 'JobAgent frontend is already running; reusing it.'
    }
} catch {
    if ($startedBackend) {
        Stop-OwnedListener $BackendPort $BackendPidFile 'JobAgent backend rollback'
    }
    throw
}

Write-Host "JobAgent is ready: $ConsoleUrl"
if (-not $NoOpen) { Start-Process $ConsoleUrl }
