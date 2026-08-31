param([switch]$Headed)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot 'backend\.venv\Scripts\python.exe'
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$node = if ($nodeCommand) { $nodeCommand.Source } else {
    Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
}
if (-not (Test-Path -LiteralPath $python)) { throw 'Backend virtualenv is missing. Run the existing project setup first.' }
if (-not (Test-Path -LiteralPath $node)) { throw 'Node.js is missing. No dependencies were installed automatically.' }
$artifactRoot = Join-Path $repoRoot ('.tmp\extension-e2e-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $artifactRoot | Out-Null
$oldUtf8 = $env:PYTHONUTF8
$oldHeaded = $env:JOBAGENT_E2E_HEADED
$oldE2e = $env:JOBAGENT_MV3_E2E
try {
    $env:PYTHONUTF8 = '1'
    $env:JOBAGENT_E2E_HEADED = if ($Headed) { '1' } else { '0' }
    $env:JOBAGENT_MV3_E2E = '1'
    Push-Location (Join-Path $repoRoot 'extension')
    try {
        & $node node_modules/typescript/bin/tsc -p tsconfig.json
        if ($LASTEXITCODE -ne 0) { throw 'Extension build failed.' }
        $testFiles = @(Get-ChildItem -LiteralPath tests -Filter '*.test.cjs' -File | ForEach-Object FullName)
        $unitOutput = & $node --test --test-reporter=spec @testFiles 2>&1
        if ($LASTEXITCODE -ne 0) { $unitOutput | Write-Output; throw 'Extension regression tests failed.' }
        $unitOutput | Select-Object -Last 8 | Write-Output
    } finally { Pop-Location }
    Push-Location (Join-Path $repoRoot 'backend')
    try {
        & $python -m pytest tests/test_extension_e2e.py tests/test_salary_dom.py tests/test_salary_ocr.py `
            -o addopts= -q --tb=short -p no:cacheprovider `
            "--basetemp=$(Join-Path $artifactRoot 'pytest')" "--junitxml=$(Join-Path $artifactRoot 'results.xml')"
        if ($LASTEXITCODE -ne 0) { throw "Acceptance failed. Evidence: $artifactRoot" }
        [xml]$results = Get-Content -Raw -LiteralPath (Join-Path $artifactRoot 'results.xml')
        $suites = @($results.testsuites.testsuite)
        $skipped = ($suites | ForEach-Object { [int]$_.skipped } | Measure-Object -Sum).Sum
        $count = ($suites | ForEach-Object { [int]$_.tests } | Measure-Object -Sum).Sum
        if ($skipped -gt 0 -or $count -lt 16) {
            throw "Acceptance incomplete: tests=$count skipped=$skipped. Evidence: $artifactRoot"
        }
    } finally { Pop-Location }
    Write-Output "Fixture-only MV3 acceptance PASS. Evidence: $artifactRoot"
    Write-Output 'This does not certify the normal Chrome/BOSS session or Windows desktop focus.'
} finally {
    $env:PYTHONUTF8 = $oldUtf8
    $env:JOBAGENT_E2E_HEADED = $oldHeaded
    $env:JOBAGENT_MV3_E2E = $oldE2e
}
