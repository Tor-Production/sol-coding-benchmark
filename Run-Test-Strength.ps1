#requires -Version 7.0
param([string]$RunId)
$ErrorActionPreference = 'Stop'
$benchFreeze = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'preparation\freeze.json') -Raw | ConvertFrom-Json
$benchStrengthArgs = @('-B', (Join-Path $PSScriptRoot 'evaluator\test_strength.py'))
if ($RunId) { $benchStrengthArgs += @('--run-id', $RunId) }
& $benchFreeze.runtime.python @benchStrengthArgs
if ($LASTEXITCODE -ne 0) { throw "Test-strength evaluation failed (exit $LASTEXITCODE)" }
