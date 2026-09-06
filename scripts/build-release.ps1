<#
.SYNOPSIS
    Build a self-contained JobAgent release for someone who has neither Python
    nor Node installed.

.DESCRIPTION
    Additive by design: this script writes only into `dist-release/` and never
    touches the development setup. `scripts/dev.ps1` keeps working exactly as
    before, Vite still serves the console on :5173 while you are editing, and
    the unpacked extension you already loaded in Chrome is untouched.

    What it produces (`dist-release/JobAgent/`):

        JobAgent.exe        one process: the API and the console UI on :8000
        启动 JobAgent.cmd    double-click launcher (starts, waits, opens Chrome)
        extension/          the built Chrome extension, to load unpacked
        data/               created on first run: database + career strategy
        SETUP.md            the same guide, next to the thing it describes

    The recipient still loads the extension by hand: an unpacked extension is
    the only way to run one that is not on the Chrome Web Store, and its id is
    machine-specific, which is exactly why `CORS_ORIGIN_REGEX` matches the
    *shape* of an extension origin rather than a fixed id.

.EXAMPLE
    .\scripts\build-release.ps1
#>
[CmdletBinding()]
param(
    # Where to assemble the release. Anything already there is replaced.
    [string]$OutDir = "dist-release",
    # Skip the frontend/extension builds when they are already current.
    [switch]$SkipWebBuilds
)

$ErrorActionPreference = 'Stop'

# npm and PyInstaller both write progress to stderr. Under `Stop`, PowerShell
# turns any native stderr line into a terminating error, so a perfectly normal
# build aborts on its own log output. Success is decided by $LASTEXITCODE.
function Invoke-Native {
    param([scriptblock]$Command, [string]$What)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Command } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$What 失败（退出码 $LASTEXITCODE）" }
}
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root 'backend\.venv\Scripts\python.exe'
$Target = Join-Path $Root $OutDir
$Payload = Join-Path $Target 'JobAgent'

function Step([string]$text) { Write-Host "==> $text" -ForegroundColor Cyan }

if (-not (Test-Path $Python)) {
    throw "找不到 $Python —— 先运行 .\scripts\dev.ps1 setup"
}

if (-not $SkipWebBuilds) {
    Step '构建前端（vite build）'
    Push-Location (Join-Path $Root 'frontend')
    try { Invoke-Native { & npm run build } '前端构建' } finally { Pop-Location }

    Step '构建扩展（tsc）'
    Push-Location (Join-Path $Root 'extension')
    try { Invoke-Native { & npm run build } '扩展构建' } finally { Pop-Location }
}

Step 'PyInstaller 打包后端'
Invoke-Native { & $Python -m PyInstaller --version | Out-Null } 'PyInstaller 检查'

$Work = Join-Path $Target '_build'
Remove-Item -Recurse -Force $Target -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $Work | Out-Null

# `--add-data` resolves its source relative to --specpath, not the working
# directory, so these are absolute. Their *destinations* mirror the repository
# layout because `core/paths.py` already resolves a frozen build's BACKEND_DIR
# and CONFIG_DIR under the bundle root - the code anticipated being packaged,
# so the package matches the code rather than the other way round.
$AlembicDir = Join-Path (Join-Path $Root 'backend') 'alembic'
$AlembicIni = Join-Path (Join-Path $Root 'backend') 'alembic.ini'
$StrategyTemplate = Join-Path (Join-Path $Root 'config') 'career_strategy.yaml'

Push-Location (Join-Path $Root 'backend')
try {
    # `alembic` ships its migration templates as data and imports versions/
    # dynamically; the Agents SDK reads its own version through
    # `importlib.metadata` at import time - and so do its own dependencies,
    # which is why the metadata copy is recursive rather than a list of names
    # discovered one crash at a time. The SDK also loads prompt text from
    # `.md` files beside its own modules, so its data comes along too. Neither is discoverable by static
    # analysis, so both are named here rather than left to PyInstaller to
    # guess - the failure mode is an exe that exits instantly with a traceback
    # nobody sees.
    Invoke-Native { & $Python -m PyInstaller `
        --name JobAgent `
        --onefile `
        --noconfirm `
        --distpath (Join-Path $Work 'dist') `
        --workpath (Join-Path $Work 'work') `
        --specpath $Work `
        --add-data "$AlembicDir;backend/alembic" `
        --add-data "$AlembicIni;backend" `
        --add-data "$StrategyTemplate;config" `
        --collect-all alembic `
        --recursive-copy-metadata openai-agents `
        --collect-all agents `
        --hidden-import app.main `
        --console `
        launcher.py } 'PyInstaller 打包'
} finally { Pop-Location }

Step '组装发布目录'
New-Item -ItemType Directory -Force $Payload | Out-Null
Copy-Item (Join-Path $Work 'dist\JobAgent.exe') $Payload
Copy-Item -Recurse (Join-Path $Root 'frontend\dist') (Join-Path $Payload 'frontend-dist')
New-Item -ItemType Directory -Force (Join-Path $Payload 'extension') | Out-Null
foreach ($item in 'manifest.json', 'popup.html', 'dist') {
    Copy-Item -Recurse (Join-Path $Root "extension\$item") (Join-Path $Payload 'extension')
}
Copy-Item (Join-Path $Root 'SETUP.md') $Payload
Copy-Item (Join-Path $Root 'config\career_strategy.yaml') (Join-Path $Payload 'career_strategy.default.yaml')

@'
@echo off
chcp 65001 > nul
title JobAgent
echo 正在启动 JobAgent（第一次会慢一些，要建数据库）...
start "" "%~dp0JobAgent.exe"
timeout /t 6 /nobreak > nul
start "" "http://127.0.0.1:8000/#/setup"
echo.
echo 浏览器已经打开。这个窗口关掉就等于退出 JobAgent。
echo 第一次使用请先按 SETUP.md 第 3 节加载 Chrome 扩展。
echo.
pause
'@ | Set-Content -Path (Join-Path $Payload '启动 JobAgent.cmd') -Encoding UTF8

Remove-Item -Recurse -Force $Work
$zip = Join-Path $Target 'JobAgent-windows.zip'
Compress-Archive -Path $Payload -DestinationPath $zip -Force

Step "完成：$zip"
Write-Host "  解压后双击「启动 JobAgent.cmd」即可，无需安装 Python 或 Node。" -ForegroundColor Green
