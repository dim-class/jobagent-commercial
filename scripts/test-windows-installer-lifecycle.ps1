<# Exercise the unsigned installer only on an ephemeral GitHub Windows runner. #>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$FirstInstaller,
    [Parameter(Mandatory = $true)]
    [string]$SecondInstaller
)

$ErrorActionPreference = 'Stop'
$AppId = '{A97560A6-9D77-4A54-A338-AC2C19D4789D}'
$UninstallRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Assert-ChildPath([string]$Path, [string]$Parent, [string]$Label) {
    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    if (-not $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label is outside its runner-owned root: $full"
    }
}

function Get-JobAgentUninstallRecord {
    if (-not (Test-Path -LiteralPath $UninstallRoot)) { return @() }
    return @(
        Get-ChildItem -LiteralPath $UninstallRoot -ErrorAction Stop |
            ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath } |
            Where-Object { $_.DisplayName -eq 'JobAgent' }
    )
}

function Invoke-Checked([string]$FilePath, [string[]]$ArgumentList, [string]$Label) {
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList `
        -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "$Label failed with exit code $($process.ExitCode)."
    }
}

function Get-FreePort {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

function Test-PortFree([int]$Port) {
    $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    try {
        $probe.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        $probe.Stop()
    }
}

function Wait-Healthy([int]$Port) {
    $lastError = $null
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" `
                -TimeoutSec 2 -ErrorAction Stop
            if ($health.status -eq 'ok' -and $health.database -eq 'ok') {
                return $health
            }
        }
        catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Installed JobAgent did not become healthy. Last error: $lastError"
}

function Stop-JobAgent([string]$Executable, [string]$DataRoot) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return }
    & $Executable --data-dir $DataRoot --stop | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "JobAgent guarded stop failed with exit code $LASTEXITCODE."
    }
}

if ($env:GITHUB_ACTIONS -ne 'true' -or [string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
    throw 'Refusing to run: this destructive lifecycle test is GitHub Actions runner-only.'
}
if ($PSVersionTable.Platform -and $PSVersionTable.Platform -ne 'Win32NT') {
    throw 'The installer lifecycle test requires Windows.'
}

$FirstInstaller = (Resolve-Path -LiteralPath $FirstInstaller).Path
$SecondInstaller = (Resolve-Path -LiteralPath $SecondInstaller).Path
$LocalAppData = [Environment]::GetFolderPath('LocalApplicationData')
$InstallRoot = Join-Path $LocalAppData 'Programs\JobAgent'
$DataRoot = Join-Path $LocalAppData 'JobAgent'
$WorkRoot = Join-Path ([IO.Path]::GetFullPath($env:RUNNER_TEMP)) "jobagent-installer-lifecycle-$PID"
$Executable = Join-Path $InstallRoot 'JobAgent.exe'
$Uninstaller = Join-Path $InstallRoot 'unins000.exe'

Assert-ChildPath $InstallRoot $LocalAppData 'Install root'
Assert-ChildPath $DataRoot $LocalAppData 'Data root'
Assert-ChildPath $WorkRoot $env:RUNNER_TEMP 'Work root'
Assert-True (-not (Test-Path -LiteralPath $InstallRoot)) 'Refusing pre-existing JobAgent installation.'
Assert-True (-not (Test-Path -LiteralPath $DataRoot)) 'Refusing pre-existing JobAgent user data.'
Assert-True ((Get-JobAgentUninstallRecord).Count -eq 0) 'Refusing pre-existing JobAgent uninstall registration.'

New-Item -ItemType Directory -Path $WorkRoot -Force | Out-Null
$Port = Get-FreePort
$OriginalPath = $env:PATH
$Runtime = $null
$Succeeded = $false

try {
    Invoke-Checked $FirstInstaller @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER',
        "/LOG=`"$(Join-Path $WorkRoot 'install-first.log')`""
    ) 'First-version install'
    Assert-True (Test-Path -LiteralPath $Executable -PathType Leaf) 'Installed JobAgent.exe is missing.'
    Assert-True (Test-Path -LiteralPath $Uninstaller -PathType Leaf) 'Installed uninstaller is missing.'

    $records = Get-JobAgentUninstallRecord
    Assert-True ($records.Count -eq 1) 'Expected exactly one JobAgent uninstall registration.'
    Assert-True ($records[0].PSChildName.StartsWith($AppId, [StringComparison]::OrdinalIgnoreCase)) `
        'Uninstall registration does not use the stable AppId.'

    # The frozen product must not resolve Python or Node from PATH at runtime.
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    Assert-True ($null -eq (Get-Command python.exe -ErrorAction SilentlyContinue)) `
        'Sanitized runtime PATH still resolves Python.'
    Assert-True ($null -eq (Get-Command node.exe -ErrorAction SilentlyContinue)) `
        'Sanitized runtime PATH still resolves Node.'

    $doctorOutput = & $Executable --data-dir $DataRoot --port $Port --doctor 2>&1
    $doctorOutput | Set-Content -LiteralPath (Join-Path $WorkRoot 'doctor.log') -Encoding utf8
    Assert-True ($LASTEXITCODE -eq 0) 'Installed frozen EXE doctor failed.'
    Assert-True (($doctorOutput -join "`n") -match 'JobAgent doctor: PASS') `
        'Installed frozen EXE doctor did not report PASS.'

    $Runtime = Start-Process -FilePath $Executable -ArgumentList @(
        '--data-dir', "`"$DataRoot`"", '--port', "$Port", '--no-open'
    ) -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $WorkRoot 'runtime-first.out.log') `
        -RedirectStandardError (Join-Path $WorkRoot 'runtime-first.err.log')
    $health = Wait-Healthy $Port
    Assert-True ($health.status -eq 'ok' -and $health.database -eq 'ok') `
        'Health payload is not fully healthy.'
    $rootResponse = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -TimeoutSec 5
    Assert-True ($rootResponse.StatusCode -eq 200) 'Compiled frontend did not return HTTP 200.'
    Assert-True ($rootResponse.Content -match '<(html|!doctype)') 'Compiled frontend response is not HTML.'

    Stop-JobAgent $Executable $DataRoot
    $Runtime.WaitForExit(15000) | Out-Null
    Assert-True $Runtime.HasExited 'First installed runtime did not stop.'
    $Runtime = $null
    Assert-True (Test-PortFree $Port) 'Port remained occupied after guarded stop.'

    $Database = Join-Path $DataRoot 'jobagent.db'
    $Sentinel = Join-Path $DataRoot 'installer-lifecycle-sentinel.txt'
    Assert-True (Test-Path -LiteralPath $Database -PathType Leaf) 'Runtime database was not created.'
    Set-Content -LiteralPath $Sentinel -Value 'preserve-me' -Encoding utf8
    $DatabaseHashBefore = (Get-FileHash -LiteralPath $Database -Algorithm SHA256).Hash

    Invoke-Checked $SecondInstaller @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER',
        "/LOG=`"$(Join-Path $WorkRoot 'install-second.log')`""
    ) 'Same-AppId upgrade'
    $records = Get-JobAgentUninstallRecord
    Assert-True ($records.Count -eq 1) 'Upgrade duplicated or removed uninstall registration.'
    Assert-True (Test-Path -LiteralPath $Sentinel -PathType Leaf) 'Upgrade removed the data sentinel.'
    Assert-True (((Get-Content -LiteralPath $Sentinel -Raw).Trim()) -eq 'preserve-me') `
        'Upgrade changed the data sentinel.'
    Assert-True ((Get-FileHash -LiteralPath $Database -Algorithm SHA256).Hash -eq $DatabaseHashBefore) `
        'Upgrade changed the existing database before startup.'

    $Runtime = Start-Process -FilePath $Executable -ArgumentList @(
        '--data-dir', "`"$DataRoot`"", '--port', "$Port", '--no-open'
    ) -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $WorkRoot 'runtime-second.out.log') `
        -RedirectStandardError (Join-Path $WorkRoot 'runtime-second.err.log')
    $null = Wait-Healthy $Port
    Stop-JobAgent $Executable $DataRoot
    $Runtime.WaitForExit(15000) | Out-Null
    Assert-True $Runtime.HasExited 'Upgraded runtime did not stop.'
    $Runtime = $null

    $DatabaseHashBeforeUninstall = (Get-FileHash -LiteralPath $Database -Algorithm SHA256).Hash
    Invoke-Checked $Uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') `
        'Silent uninstall'
    Assert-True (-not (Test-Path -LiteralPath $InstallRoot)) 'Silent uninstall left the program directory.'
    Assert-True ((Get-JobAgentUninstallRecord).Count -eq 0) 'Silent uninstall left registration behind.'
    Assert-True (Test-Path -LiteralPath $DataRoot -PathType Container) 'Silent uninstall deleted user data.'
    Assert-True (Test-Path -LiteralPath $Sentinel -PathType Leaf) 'Silent uninstall deleted the sentinel.'
    Assert-True ((Get-FileHash -LiteralPath $Database -Algorithm SHA256).Hash -eq $DatabaseHashBeforeUninstall) `
        'Silent uninstall changed the database.'
    Assert-True (Test-PortFree $Port) 'Silent uninstall left the runtime port occupied.'

    $Succeeded = $true
    Write-Host 'PASS: install, frozen runtime, same-AppId upgrade, data preservation, and silent uninstall.'
}
finally {
    $env:PATH = $OriginalPath
    if ($Runtime -and -not $Runtime.HasExited) {
        try { Stop-JobAgent $Executable $DataRoot } catch { Write-Warning $_ }
        if (-not $Runtime.WaitForExit(15000)) { $Runtime.Kill() }
    }
    if (Test-Path -LiteralPath $Uninstaller -PathType Leaf) {
        try {
            Invoke-Checked $Uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') `
                'Cleanup uninstall'
        }
        catch { Write-Warning $_ }
    }
    # These exact targets were absent before the test and are owned by the
    # disposable runner. Never broaden either deletion target.
    if (Test-Path -LiteralPath $InstallRoot) {
        Remove-Item -LiteralPath $InstallRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $DataRoot) {
        Remove-Item -LiteralPath $DataRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $WorkRoot) {
        Remove-Item -LiteralPath $WorkRoot -Recurse -Force
    }
}

if (-not $Succeeded) { throw 'Installer lifecycle acceptance did not complete.' }
