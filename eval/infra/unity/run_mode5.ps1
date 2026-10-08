[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Package,
    [Parameter(Mandatory)][string]$Submission,
    [Parameter(Mandatory)][string]$Out,
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path,
    [string]$InfrastructureRoot = 'D:\gamebench-unity-vm',
    [string]$QemuRoot = 'D:\tools\qemu',
    [string]$TaskPython = (Get-Command python.exe -ErrorAction Stop).Source
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$bench = Join-Path $RepositoryRoot 'eval\evalsys\bin\bench'
$runner = Join-Path $RepositoryRoot 'eval\infra\unity\host_runner\Invoke-UnityCandidateEvaluation.ps1'
foreach ($required in @($Package, $Submission, $bench, $runner, $TaskPython)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required path missing: $required" }
}
if (-not (Test-Path -LiteralPath $InfrastructureRoot -PathType Container)) {
    throw "Unity infrastructure root is missing: $InfrastructureRoot"
}
if (-not $env:GAMEBENCH_VLM_KEY_ENV) { $env:GAMEBENCH_VLM_KEY_ENV = 'OPENAI_API_KEY' }
if (-not $env:GAMEBENCH_VLM_PROVIDER) { $env:GAMEBENCH_VLM_PROVIDER = 'responses' }
if (-not $env:GAMEBENCH_VLM_MODEL) { $env:GAMEBENCH_VLM_MODEL = 'gpt-5.6' }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner `
    -Package (Resolve-Path -LiteralPath $Package).Path `
    -Submission (Resolve-Path -LiteralPath $Submission).Path `
    -Out ([IO.Path]::GetFullPath($Out)) `
    -VisualJudge vlm `
    -InfrastructureRoot $InfrastructureRoot `
    -QemuRoot $QemuRoot `
    -RepositoryRoot $RepositoryRoot `
    -TaskPython $TaskPython `
    -RegistryVersion '2026-09-20.mode5-mdva-domain1'
exit $LASTEXITCODE
