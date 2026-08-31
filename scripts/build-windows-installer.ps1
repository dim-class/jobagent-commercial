<# Build an unsigned, per-user Windows x64 installer candidate. #>

[CmdletBinding()]
param(
    [string]$Version = "0.1.0",
    [string]$OutputRoot,
    [string]$IsccPath,
    [string]$RequiredInnoVersion = "7.1.0",
    [switch]$SkipPortableBuild,
    [switch]$SkipWebBuilds
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo 'backend\.venv\Scripts\python.exe'
if (-not $OutputRoot) { $OutputRoot = Join-Path $Repo '.artifacts' }
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$Bundle = Join-Path $OutputRoot 'dist\JobAgent'
$InstallerRoot = Join-Path $OutputRoot 'installer'
$InstallerScript = Join-Path $Repo 'packaging\jobagent-installer.iss'

function Assert-ChildPath([string]$Path, [string]$Parent) {
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    if (-not $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside $Parent : $full"
    }
}

function Resolve-Iscc([string]$ExplicitPath) {
    if ($ExplicitPath) {
        $resolved = [System.IO.Path]::GetFullPath($ExplicitPath)
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "Inno Setup compiler not found: $resolved"
        }
        return $resolved
    }
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 7\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 7\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }
    throw 'Pinned Inno Setup 7.1.0 is required at build time. ISCC.exe was not found.'
}

if (-not $IsWindows -and $PSVersionTable.PSEdition -eq 'Core') {
    throw 'The Windows installer must be built on Windows.'
}
if ($Version -notmatch '^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$') {
    throw 'Version may contain only letters, digits, dot, underscore and hyphen.'
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Backend virtual environment is missing.'
}

if (-not $SkipPortableBuild) {
    $portableParams = @{
        Version = $Version
        OutputRoot = $OutputRoot
        SkipWebBuilds = $SkipWebBuilds
    }
    & (Join-Path $Repo 'scripts\build-windows-portable.ps1') @portableParams
    if ($LASTEXITCODE -ne 0) { throw 'Portable bundle build failed.' }
}

if (-not (Test-Path -LiteralPath (Join-Path $Bundle 'JobAgent.exe') -PathType Leaf)) {
    throw 'Audited portable bundle is missing. Build it before the installer.'
}
& $Python (Join-Path $Repo 'scripts\release_audit.py') $Bundle
if ($LASTEXITCODE -ne 0) { throw 'Portable payload audit failed.' }

Assert-ChildPath $InstallerRoot $Repo
if (Test-Path -LiteralPath $InstallerRoot) {
    Remove-Item -LiteralPath $InstallerRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $InstallerRoot -Force | Out-Null

$Iscc = Resolve-Iscc $IsccPath
$compilerVersion = (& $Iscc --version | Select-Object -First 1).Trim()
if ($LASTEXITCODE -ne 0 -or -not $compilerVersion) {
    throw "Could not determine the Inno Setup version from $Iscc"
}
if (-not $compilerVersion.StartsWith($RequiredInnoVersion, [System.StringComparison]::Ordinal)) {
    throw "Inno Setup $RequiredInnoVersion is required for reproducible candidate builds; found $compilerVersion at $Iscc"
}
Write-Warning 'This output is an unsigned development candidate. Confirm applicable Inno Setup commercial licensing and add Authenticode signing before production distribution.'
& $Iscc "/DAppVersion=$Version" "/DBundleDir=$Bundle" "/DOutputDir=$InstallerRoot" $InstallerScript
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }

$Installer = Join-Path $InstallerRoot "JobAgent-Setup-$Version-Windows-x64.exe"
if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw "Installer output is missing: $Installer"
}
$Hash = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
$SumsLine = "$Hash  $([System.IO.Path]::GetFileName($Installer))`n"
[System.IO.File]::WriteAllText(
    (Join-Path $OutputRoot 'INSTALLER-SHA256SUMS.txt'),
    $SumsLine,
    [System.Text.UTF8Encoding]::new($false))
$BundleManifestHash = (Get-FileHash -LiteralPath (Join-Path $Bundle 'RELEASE-MANIFEST.json') `
    -Algorithm SHA256).Hash.ToLowerInvariant()
$Signature = Get-AuthenticodeSignature -LiteralPath $Installer
[ordered]@{
    version = $Version
    installer = [System.IO.Path]::GetFileName($Installer)
    sha256 = $Hash
    payload_manifest_sha256 = $BundleManifestHash
    inno_setup_version = $compilerVersion
    authenticode_status = [string]$Signature.Status
    distribution_tier = 'development_candidate'
    commercial_distribution_ready = $false
} | ConvertTo-Json | Set-Content -LiteralPath `
    (Join-Path $OutputRoot 'INSTALLER-MANIFEST.json') -Encoding utf8

Write-Host "Installer candidate: $Installer"
Write-Host "SHA256: $Hash"
Write-Host "Authenticode: $($Signature.Status)"
