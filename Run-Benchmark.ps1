#requires -Version 7.0
param(
    [ValidateSet('Prepare','Preflight','Status','Run','RunAll','Grade','Report')]
    [string]$Action = 'Status',
    [string]$RunId,
    [ValidateSet('low','medium','high','xhigh','max','ultra')]
    [string]$Effort,
    [switch]$Execute
)
$ErrorActionPreference = 'Stop'
$benchNode = (Get-Command node -ErrorAction Stop).Source
$benchArguments = @((Join-Path $PSScriptRoot 'harness\cli.mjs'), $Action.ToLowerInvariant())
if ($RunId) { $benchArguments += @('--run-id', $RunId) }
if ($Effort) { $benchArguments += @('--effort', $Effort.ToLowerInvariant()) }
if ($Execute) { $benchArguments += '--execute' }
$benchOldCodex = $env:BENCH_CODEX_EXE
$benchOldPython = $env:BENCH_PYTHON_EXE
$benchOldShell = $env:BENCH_PWSH_EXE
try {
    # Status/Report and a missing Execute gate work without Codex installed.
    $benchNeedsRuntime = $Action -in @('Prepare','Preflight') -or ($Action -in @('Run','RunAll') -and $Execute)
    if ($benchNeedsRuntime) {
        $benchFreezePath = Join-Path $PSScriptRoot 'preparation\freeze.json'
        $benchPinnedRuntime = $null
        if (Test-Path -LiteralPath $benchFreezePath) {
            $benchPinnedRuntime = (Get-Content -LiteralPath $benchFreezePath -Raw | ConvertFrom-Json).runtime
        }
        $env:BENCH_CODEX_EXE = if ($benchOldCodex) { $benchOldCodex } elseif ($benchPinnedRuntime.codex -and (Test-Path -LiteralPath $benchPinnedRuntime.codex)) { $benchPinnedRuntime.codex } else { (Get-Command codex -ErrorAction Stop).Source }
        $env:BENCH_PYTHON_EXE = if ($benchOldPython) { $benchOldPython } elseif ($benchPinnedRuntime.python -and (Test-Path -LiteralPath $benchPinnedRuntime.python)) { $benchPinnedRuntime.python } else { (Get-Command python -ErrorAction Stop).Source }
        $env:BENCH_PWSH_EXE = if ($benchOldShell) { $benchOldShell } else { (Get-Process -Id $PID).Path }
    }
    & $benchNode @benchArguments
    if ($LASTEXITCODE -ne 0) { throw "Benchmark action failed (exit $LASTEXITCODE)" }
} finally {
    $env:BENCH_CODEX_EXE = $benchOldCodex
    $env:BENCH_PYTHON_EXE = $benchOldPython
    $env:BENCH_PWSH_EXE = $benchOldShell
}
