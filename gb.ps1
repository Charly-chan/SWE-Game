$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = if ($env:GB_PYTHON) { $env:GB_PYTHON } else { 'python' }
$env:PYTHONPATH = "$Repo\eval\evalsys" + $(if ($env:PYTHONPATH) { ";$env:PYTHONPATH" } else { '' })
if ($args.Count -gt 0 -and $args[0] -eq 'mode5') {
    $Rest = @($args | Select-Object -Skip 1)
    & $Python -m evalsys.taskgen.mode5.community_cli @Rest
} else {
    & $Python "$Repo\eval\evalsys\bin\bench" @args
}
exit $LASTEXITCODE
