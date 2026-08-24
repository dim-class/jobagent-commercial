<#
.SYNOPSIS
    Development helper for the local AI Job Agent (Windows / PowerShell).

.DESCRIPTION
    Running two long-lived servers from one PowerShell script is fragile, so
    this script opens each one in its own window by default. Use -NoNewWindow
    with a single task to keep it in the current console.

.EXAMPLE
    .\scripts\dev.ps1 setup      # create venv, install backend + frontend deps
    .\scripts\dev.ps1 seed       # initialise the DB and insert demo jobs
    .\scripts\dev.ps1 backend    # start FastAPI on http://127.0.0.1:8000
    .\scripts\dev.ps1 frontend   # start Vite    on http://127.0.0.1:5173
    .\scripts\dev.ps1 start      # both, in two new windows
    .\scripts\dev.ps1 test       # backend pytest + frontend production build
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'seed', 'migrate', 'backend', 'frontend', 'start', 'test',
                 'extension-build', 'smoke', 'playwright-install', 'browser-check',
                 'info', 'help')]
    [string]$Task = 'help',

    [switch]$NoNewWindow
)

$ErrorActionPreference = 'Stop'

# UTF-8 console so Chinese log output is readable.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Root         = Split-Path -Parent $PSScriptRoot
$BackendDir   = Join-Path $Root 'backend'
$FrontendDir  = Join-Path $Root 'frontend'
$ExtensionDir = Join-Path $Root 'extension'
$VenvDir      = Join-Path $BackendDir '.venv'
$VenvPython   = Join-Path $VenvDir 'Scripts\python.exe'

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Ok($message)   { Write-Host "OK  $message" -ForegroundColor Green }
function Write-Warn2($message){ Write-Host "!!  $message" -ForegroundColor Yellow }

function Resolve-NodeOnPath {
    # Node is not always on PATH (e.g. a portable install under LOCALAPPDATA).
    if (Get-Command node -ErrorAction SilentlyContinue) { return $true }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\nodejs'),
        (Join-Path $env:ProgramFiles 'nodejs'),
        (Join-Path ${env:ProgramFiles(x86)} 'nodejs')
    )
    foreach ($dir in $candidates) {
        if ($dir -and (Test-Path (Join-Path $dir 'node.exe'))) {
            $env:PATH = "$dir;$env:PATH"
            Write-Host "    (using Node at $dir)" -ForegroundColor DarkGray
            return $true
        }
    }
    return $false
}

function Assert-Backend {
    if (-not (Test-Path $VenvPython)) {
        throw "Backend virtualenv not found. Run: .\scripts\dev.ps1 setup"
    }
}

function Assert-Node {
    if (-not (Resolve-NodeOnPath)) {
        throw "Node.js 20+ was not found. Install it from https://nodejs.org/ and reopen PowerShell."
    }
}

function Invoke-InWindow($title, $workDir, $command) {
    if ($NoNewWindow) {
        Push-Location $workDir
        try { Invoke-Expression $command } finally { Pop-Location }
        return
    }
    Write-Step "Starting $title in a new window..."
    $inner = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " +
             "`$host.UI.RawUI.WindowTitle='$title'; " +
             "Set-Location '$workDir'; $command"
    Start-Process -FilePath 'powershell.exe' `
        -ArgumentList '-NoExit', '-NoProfile', '-Command', $inner | Out-Null
}

function Task-Setup {
    Write-Step 'Creating the backend virtualenv...'
    if (-not (Test-Path $VenvPython)) {
        python -m venv $VenvDir
    }
    & $VenvPython -m pip install --upgrade pip --quiet
    Write-Step 'Installing backend dependencies (editable + dev extras)...'
    & $VenvPython -m pip install -e "$BackendDir[dev]" --quiet
    Write-Ok 'Backend ready.'

    Task-PlaywrightInstall

    Assert-Node
    Write-Step 'Installing frontend dependencies...'
    Push-Location $FrontendDir
    try { npm install --no-fund --no-audit } finally { Pop-Location }
    Write-Ok 'Frontend ready.'

    Task-ExtensionBuild

    $envFile = Join-Path $Root '.env'
    if (-not (Test-Path $envFile)) {
        Copy-Item (Join-Path $Root '.env.example') $envFile
        Write-Warn2 "Created .env from .env.example - add your OPENAI_API_KEY to enable AI analysis."
    }
    Task-Migrate
    Write-Ok 'Setup complete. Next: .\scripts\dev.ps1 seed'
}

function Task-PlaywrightInstall {
    Assert-Backend
    # Idempotent: Playwright skips the download when the browser is already
    # present, so re-running setup does not re-download Chromium.
    Write-Step 'Installing the Playwright browser (skipped if already present)...'
    & $VenvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "playwright install failed (exit $LASTEXITCODE)." }
    Write-Ok 'Playwright browser ready.'
}

function Task-BrowserCheck {
    Assert-Backend
    Write-Step 'Launching the capture browser (a visible window should appear)...'
    Push-Location $BackendDir
    try { & $VenvPython (Join-Path $Root 'scripts\browser_check.py') } finally { Pop-Location }
}

function Task-Migrate {
    Assert-Backend
    # Creates a new database, or upgrades an existing one in place. Safe to
    # re-run; existing jobs and analyses are never lost.
    Write-Step 'Applying database migrations (alembic upgrade head)...'
    Push-Location $BackendDir
    try {
        & $VenvPython -m app.cli migrate
        if ($LASTEXITCODE -ne 0) { throw "migrate failed (exit $LASTEXITCODE)." }
    } finally { Pop-Location }
}

function Task-Seed {
    Assert-Backend
    Write-Step 'Initialising the database and seeding demo jobs...'
    Push-Location $BackendDir
    try {
        & $VenvPython -m app.cli migrate
        & $VenvPython -m app.cli seed
    } finally { Pop-Location }
    # Note: keep this file pure ASCII. Windows PowerShell 5.1 reads .ps1 as ANSI
    # unless the file has a UTF-8 BOM, which mangles any Chinese literal here.
    Write-Ok 'Demo jobs are fictional demo data - they are not real vacancies.'
}

function Task-Backend {
    Assert-Backend
    Invoke-InWindow 'jobagent-backend' $BackendDir `
        "& '$VenvPython' -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
    if (-not $NoNewWindow) { Write-Ok 'Backend  -> http://127.0.0.1:8000  (docs at /docs)' }
}

function Task-Frontend {
    Assert-Node
    Invoke-InWindow 'jobagent-frontend' $FrontendDir 'npm run dev'
    if (-not $NoNewWindow) { Write-Ok 'Frontend -> http://127.0.0.1:5173' }
}

function Task-Start {
    Task-Backend
    Start-Sleep -Seconds 2
    Task-Frontend
    Write-Host ''
    Write-Ok 'Both servers are starting in separate windows.'
    Write-Host '    Frontend: http://127.0.0.1:5173'
    Write-Host '    Backend : http://127.0.0.1:8000/docs'
    Write-Host '    Close those windows (or Ctrl+C inside them) to stop.'
}

function Task-ExtensionBuild {
    # The Chrome extension is compiled with plain tsc (one dev dependency).
    # Its dist/ is what Chrome loads unpacked AND what the extraction tests
    # inject, so a stale build shows up as a skipped test, not a silent pass.
    Assert-Node
    Write-Step 'Building the Chrome extension...'
    Push-Location $ExtensionDir
    try {
        if (-not (Test-Path (Join-Path $ExtensionDir 'node_modules'))) {
            npm install --no-fund --no-audit
            if ($LASTEXITCODE -ne 0) { throw "Extension npm install failed (exit $LASTEXITCODE)." }
        }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "Extension build failed (exit $LASTEXITCODE)." }
    } finally { Pop-Location }
    Write-Ok 'Extension build passed.'
}

function Task-Test {
    Assert-Backend
    # Before pytest: the extension extraction tests inject the built dist/.
    Task-ExtensionBuild
    Write-Step 'Running backend tests...'
    Push-Location $BackendDir
    try {
        & $VenvPython -m pytest
        if ($LASTEXITCODE -ne 0) { throw "Backend tests failed (exit $LASTEXITCODE)." }
    } finally { Pop-Location }
    Write-Ok 'Backend tests passed.'

    Assert-Node
    Write-Step 'Building the frontend...'
    Push-Location $FrontendDir
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed (exit $LASTEXITCODE)." }
    } finally { Pop-Location }
    Write-Ok 'Frontend build passed.'
}

function Task-Smoke {
    Assert-Backend
    Write-Step 'Running the OpenAI smoke test (makes ONE real API call if a key is set)...'
    Push-Location $BackendDir
    try { & $VenvPython (Join-Path $Root 'scripts\smoke_openai.py') } finally { Pop-Location }
}

function Task-Info {
    Assert-Backend
    Push-Location $BackendDir
    try { & $VenvPython -m app.cli info } finally { Pop-Location }
}

function Task-Help {
    Write-Host ''
    Write-Host 'AI Job Agent - development tasks' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  .\scripts\dev.ps1 setup      Create the venv and install backend + frontend deps'
    Write-Host '  .\scripts\dev.ps1 seed       Create data\jobagent.db and insert demo jobs'
    Write-Host '  .\scripts\dev.ps1 migrate    Upgrade an existing database to the latest schema'
    Write-Host '  .\scripts\dev.ps1 backend    Start FastAPI  -> http://127.0.0.1:8000'
    Write-Host '  .\scripts\dev.ps1 frontend   Start Vite     -> http://127.0.0.1:5173'
    Write-Host '  .\scripts\dev.ps1 start      Start both in separate windows'
    Write-Host '  .\scripts\dev.ps1 test       Build the extension, run pytest, build the frontend'
    Write-Host '  .\scripts\dev.ps1 extension-build      Compile the Chrome extension into extension\dist'
    Write-Host '  .\scripts\dev.ps1 smoke      One real OpenAI call (skipped without a key)'
    Write-Host '  .\scripts\dev.ps1 playwright-install   Install the Playwright browser (idempotent)'
    Write-Host '  .\scripts\dev.ps1 browser-check        Launch the visible capture browser once'
    Write-Host '  .\scripts\dev.ps1 info       Print non-secret configuration'
    Write-Host ''
    Write-Host '  Add -NoNewWindow to run backend/frontend in the current console.'
    Write-Host ''
    Write-Host '  Prefer to run things by hand? See README.md - it lists the raw commands.'
    Write-Host ''
}

switch ($Task) {
    'setup'    { Task-Setup }
    'seed'     { Task-Seed }
    'migrate'  { Task-Migrate }
    'backend'  { Task-Backend }
    'frontend' { Task-Frontend }
    'start'    { Task-Start }
    'test'     { Task-Test }
    'extension-build' { Task-ExtensionBuild }
    'smoke'    { Task-Smoke }
    'playwright-install' { Task-PlaywrightInstall }
    'browser-check'      { Task-BrowserCheck }
    'info'     { Task-Info }
    default    { Task-Help }
}
