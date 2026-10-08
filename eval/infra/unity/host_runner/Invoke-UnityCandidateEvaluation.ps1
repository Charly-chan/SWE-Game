[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Package,
    [Parameter(Mandatory)][string]$Submission,
    [Parameter(Mandatory)][string]$Out,
    [ValidateSet('none','local','vlm')][string]$VisualJudge = 'none',
    [string]$RegistryVersion = '2026-09-20.mode5-mdva-domain1',
    [string]$InfrastructureRoot = 'D:\gamebench-unity-vm',
    [string]$QemuRoot = 'D:\tools\qemu',
    [string]$RepositoryRoot = '',
    [string]$TaskPython = (Get-Command python.exe -ErrorAction Stop).Source,
    [string]$WslDistribution = 'Ubuntu-22.04',
    [int]$GuestAgentPort = 2223,
    [int]$MemoryMB = 12288,
    [int]$CpuCount = 8,
    [ValidateRange(300,7200)][int]$WallSeconds = 3600,
    [switch]$DiagnosticSmoke,
    [Guid]$VmUuid = '8a287f35-e5a3-4fa2-bab1-d2908c4f180d'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($RegistryVersion -ne '2026-09-20.mode5-mdva-domain1') { throw 'Mode 5 registry is frozen to 2026-09-20.mode5-mdva-domain1' }
if (-not $RepositoryRoot) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
}

function Invoke-Qga {
    param([Parameter(Mandatory)][hashtable]$Request, [int]$ReadTimeoutMS = 60000)
    $client = [Net.Sockets.TcpClient]::new('127.0.0.1', $GuestAgentPort)
    try {
        $stream = $client.GetStream(); $stream.ReadTimeout = $ReadTimeoutMS
        $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false)); $writer.AutoFlush = $true
        $reader = [IO.StreamReader]::new($stream, [Text.UTF8Encoding]::new($false))
        $writer.WriteLine(($Request | ConvertTo-Json -Compress -Depth 10))
        $line = $reader.ReadLine()
        if (-not $line) { throw 'QEMU guest-agent returned an empty response' }
        $line | ConvertFrom-Json
    } finally { $client.Dispose() }
}

function Wait-Qga {
    param([Diagnostics.Process]$Process, [string]$QemuErrorPath)
    $deadline = (Get-Date).AddMinutes(3)
    do {
        if ($Process.HasExited) { throw "QEMU exited before guest readiness (exit $($Process.ExitCode))" }
        try { $null = Invoke-Qga -Request @{execute='guest-ping'} -ReadTimeoutMS 15000; return }
        catch { Start-Sleep -Seconds 2 }
    } while ((Get-Date) -lt $deadline)
    throw "QEMU guest-agent did not become ready; see $QemuErrorPath"
}

function Invoke-QgaExec {
    param([string]$Command, [int]$HostWallSeconds)
    $started = Invoke-Qga -Request @{execute='guest-exec';arguments=@{path='/bin/bash';arg=@('-lc',$Command);'capture-output'=$true}}
    $guestPid = [int]$started.return.pid; $deadline = (Get-Date).AddSeconds($HostWallSeconds)
    do {
        Start-Sleep -Seconds 2
        if ((Get-Date) -ge $deadline) { throw "Guest command exceeded host wall limit ($HostWallSeconds seconds)" }
        $status = Invoke-Qga -Request @{execute='guest-exec-status';arguments=@{pid=$guestPid}}
    } while (-not $status.return.exited)
    $outProperty=$status.return.PSObject.Properties['out-data']
    $errProperty=$status.return.PSObject.Properties['err-data']
    $out = if ($outProperty -and $outProperty.Value) {[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([string]$outProperty.Value))} else {''}
    $err = if ($errProperty -and $errProperty.Value) {[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([string]$errProperty.Value))} else {''}
    [pscustomobject]@{ExitCode=[int]$status.return.exitcode; Stdout=$out; Stderr=$err}
}

function Stop-DisposableVm([Diagnostics.Process]$Process) {
    if (-not $Process -or $Process.HasExited) { return }
    try {$null=Invoke-Qga -Request @{execute='guest-shutdown';arguments=@{mode='powerdown'}} -ReadTimeoutMS 5000} catch {}
    $Process.WaitForExit(60000) | Out-Null
    if (-not $Process.HasExited) { Stop-Process -Id $Process.Id -Force; $Process.WaitForExit() }
}

function ConvertTo-WslDPath([string]$Path) {
    $resolved=[IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith('D:\',[StringComparison]::OrdinalIgnoreCase)) { throw "WSL disk helper accepts only D: paths: $resolved" }
    '/mnt/d/' + $resolved.Substring(3).Replace('\','/')
}

function Assert-Under([string]$Path,[string]$Root) {
    $p=[IO.Path]::GetFullPath($Path); $r=[IO.Path]::GetFullPath($Root).TrimEnd('\')
    if (-not $p.StartsWith($r+'\',[StringComparison]::OrdinalIgnoreCase)) { throw "Refusing cleanup outside runtime root: $p" }
}

function Get-Sha([string]$Path) {
    $stream=[IO.File]::OpenRead($Path)
    try {
        $sha=[Security.Cryptography.SHA256]::Create()
        try { ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
        finally { $sha.Dispose() }
    } finally { $stream.Dispose() }
}

$packagePath=(Resolve-Path -LiteralPath $Package).Path
$submissionPath=(Resolve-Path -LiteralPath $Submission).Path
$outPath=[IO.Path]::GetFullPath($Out)
if (-not (Test-Path -LiteralPath (Join-Path $packagePath 'manifest.json') -PathType Leaf)) { throw 'Package lacks manifest.json' }
if (-not (Test-Path -LiteralPath $submissionPath -PathType Container)) { throw 'Submission must be a directory' }
if (Test-Path -LiteralPath $outPath) { throw "Output must not already exist: $outPath" }
if (Get-Process qemu-system-x86_64 -ErrorAction SilentlyContinue) { throw 'Another QEMU VM is already running' }
$candidateLinks=@(Get-ChildItem -LiteralPath $submissionPath -Force -Recurse | Where-Object {$_.Attributes -band [IO.FileAttributes]::ReparsePoint})
if ($candidateLinks.Count) { throw "Submission contains a link/reparse point and cannot be normalized safely: $($candidateLinks[0].FullName)" }

$qemu=Join-Path $QemuRoot 'qemu-system-x86_64.exe'; $qemuImg=Join-Path $QemuRoot 'qemu-img.exe'
$licenseImage=Join-Path $InfrastructureRoot 'license-exchange\ubuntu2404-unity6000.3.23f1-personal-license-scrubbed.qcow2'
$profilePath=Join-Path $RepositoryRoot 'eval\infra\unity\profiles\ubuntu2404-unity6000.3.23f1.json'
$environmentEvidencePath=Join-Path $RepositoryRoot 'eval\infra\unity\evidence\m5-004-environment-certification.json'
$guestRunner=Join-Path $RepositoryRoot 'eval\infra\unity\guest_runner\run-candidate-evaluation.sh'
$benchPath=Join-Path $RepositoryRoot 'eval\evalsys\bin\bench'
$evalsysPath=Join-Path $RepositoryRoot 'eval\evalsys\evalsys'
$recordToolsPath=Join-Path $RepositoryRoot 'eval\tools\record'
$interfacePath=Join-Path $RepositoryRoot 'eval\interface'
foreach ($required in @($qemu,$qemuImg,$licenseImage,$profilePath,$environmentEvidencePath,$guestRunner,$benchPath,$evalsysPath,$recordToolsPath,$interfacePath,$TaskPython)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required path missing: $required" }
}
$profile=Get-Content -LiteralPath $profilePath -Raw -Encoding UTF8 | ConvertFrom-Json
$environmentEvidence=Get-Content -LiteralPath $environmentEvidencePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($profile.environment_class -ne 'linux-vm-certified' -or -not $profile.score_eligible -or $profile.certification_status -ne 'certified') { throw 'Unity environment profile is not certified' }
$expectedLicenseDigest=[string]$environmentEvidence.frozen_environment.license_state_image_sha256
if ((Get-Sha $licenseImage) -ne $expectedLicenseDigest) { throw 'License-state image digest differs from certified environment evidence' }

$runtimeRoot=Join-Path $InfrastructureRoot 'runtime'; $runId=(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$runRoot=Join-Path $runtimeRoot "candidate-$runId"; $candidateStage=Join-Path $runRoot 'candidate-stage'
$controllerStage=Join-Path $runRoot 'controller-stage'; $evaluatorStage=Join-Path $runRoot 'evaluator-stage'
$candidateIso=Join-Path $runRoot 'candidate.iso'; $controllerIso=Join-Path $runRoot 'controller.iso'
$overlay=Join-Path $runRoot 'overlay.qcow2'; $outputDisk=Join-Path $runRoot 'output.raw'
$serialLog=Join-Path $runRoot 'serial.log'; $qemuLog=Join-Path $runRoot 'qemu-stderr.log'; $process=$null
New-Item -ItemType Directory -Path $runRoot,$candidateStage,$controllerStage,$evaluatorStage | Out-Null

try {
    # Normalize archive top-level without mutating the source.
    $candidatePack=Join-Path $runRoot 'candidate-pack\submission'; New-Item -ItemType Directory -Path $candidatePack | Out-Null
    Copy-Item -Path (Join-Path $submissionPath '*') -Destination $candidatePack -Recurse -Force
    & tar.exe -czf (Join-Path $candidateStage 'submission.tar.gz') -C (Split-Path $candidatePack) submission
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create normalized submission archive' }

    $packagePack=Join-Path $runRoot 'package-pack\package'; New-Item -ItemType Directory -Path $packagePack | Out-Null
    Copy-Item -Path (Join-Path $packagePath '*') -Destination $packagePack -Recurse -Force
    & tar.exe -czf (Join-Path $controllerStage 'package.tar.gz') -C (Split-Path $packagePack) package
    New-Item -ItemType Directory -Path (Join-Path $evaluatorStage 'evalsys\bin'),(Join-Path $evaluatorStage 'evalsys\evalsys') | Out-Null
    Copy-Item -LiteralPath $benchPath -Destination (Join-Path $evaluatorStage 'evalsys\bin\bench')
    Copy-Item -Path (Join-Path $evalsysPath '*') -Destination (Join-Path $evaluatorStage 'evalsys\evalsys') -Recurse -Force
    New-Item -ItemType Directory -Path (Join-Path $evaluatorStage 'interface'),(Join-Path $evaluatorStage 'tools\record') | Out-Null
    Copy-Item -Path (Join-Path $interfacePath '*') -Destination (Join-Path $evaluatorStage 'interface') -Recurse -Force
    Copy-Item -Path (Join-Path $recordToolsPath '*') -Destination (Join-Path $evaluatorStage 'tools\record') -Recurse -Force
    if (-not (Test-Path -LiteralPath (Join-Path $evaluatorStage 'interface\contract.v2.json'))) { throw 'Evaluator staging omitted interface contract' }
    & tar.exe -czf (Join-Path $controllerStage 'evaluator.tar.gz') -C $evaluatorStage .
    Copy-Item -LiteralPath $profilePath -Destination (Join-Path $controllerStage 'environment-profile.json')
    Copy-Item -LiteralPath $guestRunner -Destination (Join-Path $controllerStage 'run-candidate-evaluation.sh')

    & wsl.exe -d $WslDistribution -u root -- bash -lc 'umount /mnt/d 2>/dev/null || true; install -d /mnt/d; mount -t drvfs D: /mnt/d'
    if ($LASTEXITCODE -ne 0) { throw 'Failed to expose D: to WSL disk helper' }
    & wsl.exe -d $WslDistribution -u root -- genisoimage -quiet -R -J -V GBCANDIDATE -o (ConvertTo-WslDPath $candidateIso) (ConvertTo-WslDPath $candidateStage)
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create candidate ISO' }
    & wsl.exe -d $WslDistribution -u root -- genisoimage -quiet -R -J -V GBCONTROLLER -o (ConvertTo-WslDPath $controllerIso) (ConvertTo-WslDPath $controllerStage)
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create controller ISO' }
    & $qemuImg create -f qcow2 -F qcow2 -b $licenseImage $overlay
    & $qemuImg create -f raw $outputDisk 2G
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create disposable disks' }

    $arguments=@('-name',"gb-unity-candidate-$runId",'-accel','whpx','-machine','q35','-uuid',$VmUuid.ToString(),'-cpu','max','-smp',[string]$CpuCount,'-m',[string]$MemoryMB,
      '-drive',"file=$overlay,if=none,id=os,format=qcow2,cache=writeback,discard=unmap",'-device','virtio-blk-pci,drive=os,serial=GBROOT,bootindex=1',
      '-drive',"file=$candidateIso,if=none,id=candidate,format=raw,readonly=on",'-device','virtio-blk-pci,drive=candidate,serial=GBCANDIDATE',
      '-drive',"file=$controllerIso,if=none,id=controller,format=raw,readonly=on",'-device','virtio-blk-pci,drive=controller,serial=GBCONTROLLER',
      '-drive',"file=$outputDisk,if=none,id=output,format=raw,cache=writeback",'-device','virtio-blk-pci,drive=output,serial=GBOUTPUT','-nic','none',
      '-device','virtio-serial-pci','-chardev',"socket,id=qga0,host=127.0.0.1,port=$GuestAgentPort,server=on,wait=off",'-device','virtserialport,chardev=qga0,name=org.qemu.guest_agent.0',
      '-display','none','-monitor','none','-serial',"file:$serialLog")
    $process=Start-Process -FilePath $qemu -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardError $qemuLog
    Wait-Qga $process $qemuLog
    $network=Invoke-Qga -Request @{execute='guest-network-get-interfaces'}
    $interfaces=@($network.return | ForEach-Object {[string]$_.name} | Sort-Object -Unique)
    if ($interfaces.Count -ne 1 -or $interfaces[0] -ne 'lo') { throw "Offline gate failed: $($interfaces -join ', ')" }

    $command=@"
set -euo pipefail
c=`$(blkid -L GBCANDIDATE); h=`$(blkid -L GBCONTROLLER); o=/dev/disk/by-id/virtio-GBOUTPUT
install -d -m 0700 /run/gamebench/controller-bootstrap
mount -t iso9660 -o ro,nosuid,nodev,noexec "`$h" /run/gamebench/controller-bootstrap
cp /run/gamebench/controller-bootstrap/run-candidate-evaluation.sh /root/run-candidate-evaluation.sh
chmod 0500 /root/run-candidate-evaluation.sh
umount /run/gamebench/controller-bootstrap
set +e
systemd-run --quiet --wait --collect --pipe --unit=gamebench-candidate-$runId \
  --property=Delegate=yes --property=MemoryMax=$([Math]::Max(2048,$MemoryMB-1024))M \
  --property=TasksMax=2048 --property=CPUQuota=$($CpuCount*100)% \
  # The guest is permanently offline.  VLM judging happens after verified
  # artifact extraction on the host, where evaluator credentials are allowed.
  bash /root/run-candidate-evaluation.sh "`$c" "`$h" "`$o" 'none' '$([int]$DiagnosticSmoke.IsPresent)'
rc=`$?
set -e
if test "`$rc" -ne 0; then
  printf 'candidate_runner_exit=%s\n' "`$rc"
  journalctl --no-pager -u gamebench-candidate-$runId -n 200 || true
  exit "`$rc"
fi
"@
    $guest=Invoke-QgaExec $command $WallSeconds
    if ($guest.ExitCode -ne 0 -or $guest.Stdout -notmatch '(?m)^candidate_evaluation_complete=1$') { throw "Guest evaluation failed (exit $($guest.ExitCode)). stdout=$($guest.Stdout) stderr=$($guest.Stderr)" }
    Stop-DisposableVm $process; $process=$null

    New-Item -ItemType Directory -Path $outPath | Out-Null
    foreach ($name in @('report.json','artifact-manifest.json','evaluation-artifacts.tar.gz','bench.stdout.log','bench.stderr.log','kernel-audit.log')) {
        $destination=Join-Path $outPath $name
        $extract="umount /mnt/d 2>/dev/null || true; install -d /mnt/d; mount -t drvfs D: /mnt/d; debugfs -R 'dump -p /$name $(ConvertTo-WslDPath $destination)' '$(ConvertTo-WslDPath $outputDisk)'"
        & wsl.exe -d $WslDistribution -u root -- bash -lc $extract
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $destination)) { throw "Offline extraction failed: $name" }
    }
    $manifest=Get-Content -LiteralPath (Join-Path $outPath 'artifact-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.producer -ne 'trusted-vm-controller') { throw 'Artifact producer attestation is invalid' }
    foreach ($artifact in $manifest.artifacts) {
        $path=Join-Path $outPath ([string]$artifact.path)
        if ((Get-Sha $path) -ne [string]$artifact.sha256 -or (Get-Item $path).Length -ne [long]$artifact.bytes) { throw "Artifact verification failed: $path" }
    }
    if ($VisualJudge -eq 'vlm') {
        $objectiveReport=Join-Path $outPath 'objective-report.json'
        Copy-Item -LiteralPath (Join-Path $outPath 'report.json') -Destination $objectiveReport
        $hostVisualOut=Join-Path $outPath 'host-visual'
        & $TaskPython $benchPath finalize-mode5 --report $objectiveReport --package $packagePath `
            --artifacts (Join-Path $outPath 'evaluation-artifacts.tar.gz') --out $hostVisualOut
        if ($LASTEXITCODE -ne 0) {
            if (Test-Path -LiteralPath (Join-Path $hostVisualOut 'production_status.json')) {
                Copy-Item -LiteralPath (Join-Path $hostVisualOut 'production_status.json') -Destination (Join-Path $outPath 'production_status.json') -Force
            }
            throw "Host Mode 5 visual finalization failed (exit $LASTEXITCODE)"
        }
        foreach ($name in @('report.json','card.json','production_status.json')) {
            Copy-Item -LiteralPath (Join-Path $hostVisualOut $name) -Destination (Join-Path $outPath $name) -Force
        }
    }
    Write-Output "Unity candidate evaluation complete: $(Join-Path $outPath 'report.json')"
} finally {
    Stop-DisposableVm $process
    if (Test-Path -LiteralPath $runRoot) { Assert-Under $runRoot $runtimeRoot; Remove-Item -LiteralPath $runRoot -Recurse -Force }
}
