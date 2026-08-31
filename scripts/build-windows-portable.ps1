<# Build an unsigned, auditable Windows x64 portable release candidate. #>

[CmdletBinding()]
param(
    [string]$Version = "0.1.0",
    [string]$OutputRoot,
    [switch]$SkipWebBuilds
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Repo 'backend'
$Frontend = Join-Path $Repo 'frontend'
$Extension = Join-Path $Repo 'extension'
$Python = Join-Path $Backend '.venv\Scripts\python.exe'
if (-not $OutputRoot) { $OutputRoot = Join-Path $Repo '.artifacts' }
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$WorkRoot = [System.IO.Path]::GetFullPath((Join-Path $Repo '.tmp\pyinstaller'))
$DistRoot = Join-Path $OutputRoot 'dist'
$Bundle = Join-Path $DistRoot 'JobAgent'

function Assert-ChildPath([string]$Path, [string]$Parent) {
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    if (-not $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside $Parent : $full"
    }
}

function Resolve-Npm {
    $command = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($candidate in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\nodejs\npm.cmd'),
        (Join-Path $env:ProgramFiles 'nodejs\npm.cmd'),
        (Join-Path ${env:ProgramFiles(x86)} 'nodejs\npm.cmd')
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    throw 'Node.js/npm 22+ is required at build time.'
}

if (-not $IsWindows -and $PSVersionTable.PSEdition -eq 'Core') {
    throw 'The Windows artifact must be built on Windows.'
}
if ($Version -notmatch '^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$') {
    throw 'Version may contain only letters, digits, dot, underscore and hyphen.'
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Backend virtual environment is missing.'
}

if (-not $SkipWebBuilds) {
    $npm = Resolve-Npm
    Push-Location $Frontend
    try { & $npm run build; if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' } }
    finally { Pop-Location }
    Push-Location $Extension
    try { & $npm run build; if ($LASTEXITCODE -ne 0) { throw 'Extension build failed.' } }
    finally { Pop-Location }
}

foreach ($path in @($WorkRoot, $DistRoot)) {
    Assert-ChildPath $path $Repo
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

& $Python -m PyInstaller --noconfirm --clean `
    --workpath $WorkRoot --distpath $DistRoot `
    (Join-Path $Repo 'packaging\jobagent.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

$ReleaseExtension = Join-Path $Bundle 'extension'
New-Item -ItemType Directory -Path $ReleaseExtension -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $Extension 'manifest.json') -Destination $ReleaseExtension
Copy-Item -LiteralPath (Join-Path $Extension 'popup.html') -Destination $ReleaseExtension
Copy-Item -LiteralPath (Join-Path $Extension 'popup.css') -Destination $ReleaseExtension
Copy-Item -LiteralPath (Join-Path $Extension 'dist') -Destination $ReleaseExtension -Recurse
Copy-Item -LiteralPath (Join-Path $Repo 'packaging\README-FIRST.txt') -Destination $Bundle
Set-Content -LiteralPath (Join-Path $Bundle 'Stop-JobAgent.cmd') -Encoding ascii -Value `
    '@echo off', '"%~dp0JobAgent.exe" --stop', 'if errorlevel 1 pause'

& $Python (Join-Path $Repo 'scripts\release_audit.py') $Bundle --write-manifest
if ($LASTEXITCODE -ne 0) { throw 'Release privacy/completeness audit failed.' }

$Zip = Join-Path $OutputRoot "JobAgent-Windows-x64-$Version.zip"
Assert-ChildPath $Zip $Repo
if (Test-Path -LiteralPath $Zip) { Remove-Item -LiteralPath $Zip -Force }
Compress-Archive -LiteralPath $Bundle -DestinationPath $Zip -CompressionLevel Optimal
$Hash = (Get-FileHash -LiteralPath $Zip -Algorithm SHA256).Hash.ToLowerInvariant()
# LF, not CRLF: `sha256sum -c` (shipped with Git for Windows, and the usual way
# anyone checks this file) cannot read a CRLF line - it looks for a file whose
# name ends in a carriage return and reports FAILED. Written with WriteAllText
# rather than Set-Content because Set-Content appends the platform newline.
$SumsLine = "$Hash  $([System.IO.Path]::GetFileName($Zip))`n"
[System.IO.File]::WriteAllText(
    (Join-Path $OutputRoot 'SHA256SUMS.txt'), $SumsLine, [System.Text.UTF8Encoding]::new($false))
Write-Host "Portable candidate: $Zip"
Write-Host "SHA256: $Hash"
