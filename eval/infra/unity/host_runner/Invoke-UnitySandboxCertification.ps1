[CmdletBinding()]
param(
    [string]$InfrastructureRoot = 'D:\gamebench-unity-vm',
    [string]$QemuRoot = 'D:\tools\qemu',
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path,
    [string]$TaskPython = (Get-Command python.exe -ErrorAction Stop).Source,
    [string]$WslDistribution = 'Ubuntu-22.04',
    [int]$GuestAgentPort = 2223,
    [int]$MemoryMB = 12288,
    [int]$CpuCount = 8,
    [int]$WallSeconds = 1800,
    [string]$RuntimeRoot = (Join-Path $InfrastructureRoot 'runtime'),
    [int]$OutputDiskMB = 1024,
    [Guid]$VmUuid = '8a287f35-e5a3-4fa2-bab1-d2908c4f180d'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Qga {
    param(
        [Parameter(Mandatory)][hashtable]$Request,
        [int]$ReadTimeoutMS = 60000
    )
    $client = [Net.Sockets.TcpClient]::new('127.0.0.1', $GuestAgentPort)
    try {
        $stream = $client.GetStream()
        $stream.ReadTimeout = $ReadTimeoutMS
        $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
        $writer.AutoFlush = $true
        $reader = [IO.StreamReader]::new($stream, [Text.UTF8Encoding]::new($false))
        $writer.WriteLine(($Request | ConvertTo-Json -Compress -Depth 10))
        $line = $reader.ReadLine()
        if (-not $line) { throw 'QEMU guest-agent returned an empty response' }
        return $line | ConvertFrom-Json
    }
    finally { $client.Dispose() }
}

function Wait-Qga {
    param(
        [Parameter(Mandatory)][Diagnostics.Process]$Process,
        [Parameter(Mandatory)][string]$QemuErrorPath
    )
    $deadline = (Get-Date).AddMinutes(3)
    do {
        if ($Process.HasExited) {
            $detail = if (Test-Path -LiteralPath $QemuErrorPath) {
                Get-Content -LiteralPath $QemuErrorPath -Raw
            } else { '' }
            throw "QEMU exited before guest-agent readiness (exit $($Process.ExitCode)): $detail"
        }
        try {
            $null = Invoke-Qga -Request @{ execute = 'guest-ping' } -ReadTimeoutMS 15000
            return
        }
        catch { Start-Sleep -Seconds 2 }
    } while ((Get-Date) -lt $deadline)
    throw 'QEMU guest-agent did not become ready'
}

function Invoke-QgaExec {
    param(
        [Parameter(Mandatory)][string]$Command,
        [int]$HostWallSeconds = 0
    )
    $started = Invoke-Qga -Request @{
        execute = 'guest-exec'
        arguments = @{
            path = '/bin/bash'
            arg = @('-lc', $Command)
            'capture-output' = $true
        }
    }
    $guestPid = [int]$started.return.pid
    $deadline = if ($HostWallSeconds -gt 0) { (Get-Date).AddSeconds($HostWallSeconds) } else { $null }
    do {
        Start-Sleep -Seconds 2
        if ($deadline -and (Get-Date) -ge $deadline) {
            throw "Guest command exceeded the host wall limit of $HostWallSeconds seconds"
        }
        $status = Invoke-Qga -Request @{
            execute = 'guest-exec-status'
            arguments = @{ pid = $guestPid }
        }
    } while (-not $status.return.exited)
    $outProperty = $status.return.PSObject.Properties['out-data']
    $errProperty = $status.return.PSObject.Properties['err-data']
    $stdout = if ($outProperty -and $outProperty.Value) {
        [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([string]$outProperty.Value))
    } else { '' }
    $stderr = if ($errProperty -and $errProperty.Value) {
        [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([string]$errProperty.Value))
    } else { '' }
    [pscustomobject]@{ ExitCode = [int]$status.return.exitcode; Stdout = $stdout; Stderr = $stderr }
}

function Stop-DisposableVm {
    param([Diagnostics.Process]$Process)
    if (-not $Process -or $Process.HasExited) { return }
    try {
        $null = Invoke-Qga -Request @{
            execute = 'guest-shutdown'
            arguments = @{ mode = 'powerdown' }
        } -ReadTimeoutMS 5000
    } catch { }
    $Process.WaitForExit(60000) | Out-Null
    if (-not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force
        $Process.WaitForExit()
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory)][string]$Path)
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Set-Utf8NoBomContent {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Value)
    [IO.File]::WriteAllText($Path, $Value, [Text.UTF8Encoding]::new($false))
}

function ConvertTo-WslDPath {
    param([Parameter(Mandatory)][string]$Path)
    $resolved = [IO.Path]::GetFullPath($Path)
    if ($resolved -notmatch '^[A-Za-z]:\\') {
        throw "WSL helper accepts only explicit local drive paths: $resolved"
    }
    '/mnt/' + $resolved.Substring(0, 1).ToLowerInvariant() + '/' + $resolved.Substring(3).Replace('\', '/')
}

function Get-WslMountCommand {
    param([Parameter(Mandatory)][string[]]$Path)
    $letters = [Collections.Generic.SortedSet[string]]::new()
    foreach ($item in $Path) {
        $null = $letters.Add([IO.Path]::GetFullPath($item).Substring(0, 1).ToLowerInvariant())
    }
    ($letters | ForEach-Object {
        "umount /mnt/$_ 2>/dev/null || true; install -d /mnt/$_; mount -t drvfs $($_.ToUpperInvariant()): /mnt/$_"
    }) -join '; '
}

function Assert-UnderRuntime {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$RuntimeRoot)
    $resolvedPath = [IO.Path]::GetFullPath($Path)
    $resolvedRoot = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\')
    if (-not $resolvedPath.StartsWith($resolvedRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing destructive cleanup outside runtime root: $resolvedPath"
    }
}

if ($WallSeconds -lt 60) { throw 'WallSeconds must be at least 60' }
if (Get-Process qemu-system-x86_64 -ErrorAction SilentlyContinue) {
    throw 'Refusing to start certification while another QEMU VM is running'
}

$qemu = Join-Path $QemuRoot 'qemu-system-x86_64.exe'
$qemuImg = Join-Path $QemuRoot 'qemu-img.exe'
$licenseImage = Join-Path $InfrastructureRoot 'license-exchange\ubuntu2404-unity6000.3.23f1-personal-license-scrubbed.qcow2'
$fixtureRoot = Join-Path $RepositoryRoot 'eval\infra\unity\fixtures\sandbox_probe'
$blankFixtureRoot = Join-Path $RepositoryRoot 'eval\infra\unity\fixtures\blank_protocol'
$protocolFixtureRoot = Join-Path $RepositoryRoot 'eval\infra\unity\fixtures\protocol_conformance'
$causalityFixtureRoot = Join-Path $RepositoryRoot 'eval\infra\unity\fixtures\behavior_causality'
$observerFixtureRoot = Join-Path $RepositoryRoot 'eval\infra\unity\fixtures\observer_conformance'
$evalsysRoot = Join-Path $RepositoryRoot 'eval\evalsys\evalsys'
$environmentProfile = Join-Path $RepositoryRoot 'eval\infra\unity\profiles\ubuntu2404-unity6000.3.23f1.json'
$interfaceContract = Join-Path $RepositoryRoot 'eval\interface\contract.v2.json'
$causalityEvidenceValidator = Join-Path $causalityFixtureRoot 'validate_evidence.py'
$targetScaffoldRoot = Join-Path $RepositoryRoot 'eval\evalsys\evalsys\taskgen\unity\target_unity'
$unityControllerModule = Join-Path $RepositoryRoot 'eval\evalsys\evalsys\taskgen\unity\unity_controller.py'
$unityRuntimeObserver = Join-Path $RepositoryRoot 'eval\evalsys\evalsys\taskgen\unity\unity_runtime_probe.cs'
$guestRunner = Join-Path $RepositoryRoot 'eval\infra\unity\guest_runner\run-sandbox-certification.sh'
$runtimeRoot = $RuntimeRoot
$runId = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$runRoot = Join-Path $runtimeRoot "m5-005-$runId"
$evidenceRoot = Join-Path $InfrastructureRoot "evidence\m5-005-sandbox-$runId"
$candidateStage = Join-Path $runRoot 'candidate-input'
$controllerStage = Join-Path $runRoot 'controller-input'
$candidateIso = Join-Path $runRoot 'candidate-input.iso'
$controllerIso = Join-Path $runRoot 'controller-input.iso'
$outputDisk = Join-Path $runRoot 'output.raw'
$overlay = Join-Path $runRoot 'vm-overlay.qcow2'
$serialLog = Join-Path $evidenceRoot 'serial.log'
$qemuErrorLog = Join-Path $evidenceRoot 'qemu-stderr.log'
$reportPath = Join-Path $evidenceRoot 'report.json'
$manifestPath = Join-Path $evidenceRoot 'artifact-manifest.json'
$summaryPath = Join-Path $evidenceRoot 'summary.json'
$jobContractPath = Join-Path $runRoot 'vm-job.json'
$jobContractValidator = Join-Path $RepositoryRoot 'eval\evalsys\evalsys\taskgen\unity\unity_vm_contract.py'
$nonce = [Guid]::NewGuid().ToString('N')
$hiddenValue = [Guid]::NewGuid().ToString('N')
$process = $null
$startedAt = Get-Date

foreach ($required in @(
    $qemu, $qemuImg, $licenseImage, $fixtureRoot, $blankFixtureRoot, $protocolFixtureRoot, $causalityFixtureRoot,
    $observerFixtureRoot, $evalsysRoot,
    $environmentProfile, $interfaceContract, $unityRuntimeObserver,
    $causalityEvidenceValidator, $targetScaffoldRoot,
    $unityControllerModule, $guestRunner, $TaskPython, $jobContractValidator,
    (Join-Path $fixtureRoot 'candidate_probe.py'),
    (Join-Path $fixtureRoot 'candidate-workload.sh'),
    (Join-Path $fixtureRoot 'controller.py')
)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required path is missing: $required" }
}

New-Item -ItemType Directory -Path $runRoot, $evidenceRoot, $candidateStage, $controllerStage | Out-Null
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $runRoot '/inheritance:r' '/grant:r' "${identity}:(OI)(CI)(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to restrict runtime-directory ACL' }
& icacls.exe $evidenceRoot '/inheritance:r' '/grant:r' "${identity}:(OI)(CI)(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to restrict evidence-directory ACL' }

try {
    Copy-Item -LiteralPath (Join-Path $fixtureRoot 'candidate_probe.py') -Destination (Join-Path $candidateStage 'probe.py')
    Copy-Item -LiteralPath (Join-Path $fixtureRoot 'candidate-workload.sh') -Destination (Join-Path $candidateStage 'candidate-workload.sh')
    & tar.exe -czf (Join-Path $candidateStage 'blank-fixture.tar.gz') -C $blankFixtureRoot .
    if ($LASTEXITCODE -ne 0) { throw 'Failed to package blank Unity fixture into candidate input' }
    & tar.exe -czf (Join-Path $candidateStage 'protocol-fixture.tar.gz') -C $protocolFixtureRoot .
    if ($LASTEXITCODE -ne 0) { throw 'Failed to package protocol Unity fixture into candidate input' }
    & tar.exe -czf (Join-Path $candidateStage 'behavior-causality-fixture.tar.gz') -C $causalityFixtureRoot .
    if ($LASTEXITCODE -ne 0) { throw 'Failed to package behavior-causality Unity fixture into candidate input' }
    & tar.exe -czf (Join-Path $candidateStage 'target-scaffold.tar.gz') -C $targetScaffoldRoot .
    if ($LASTEXITCODE -ne 0) { throw 'Failed to package target Unity scaffold into candidate input' }
    & tar.exe -czf (Join-Path $candidateStage 'observer-fixture.tar.gz') -C $observerFixtureRoot .
    if ($LASTEXITCODE -ne 0) { throw 'Failed to package observer Unity fixture into candidate input' }
    & tar.exe -czf (Join-Path $controllerStage 'evalsys.tar.gz') -C (Split-Path $evalsysRoot -Parent) 'evalsys'
    if ($LASTEXITCODE -ne 0) { throw 'Failed to package private evaluator modules' }
    Copy-Item -LiteralPath $interfaceContract -Destination (Join-Path $controllerStage 'interface-contract.v2.json')
    Copy-Item -LiteralPath $unityRuntimeObserver -Destination (Join-Path $candidateStage 'unity-runtime-observer.cs')
    Copy-Item -LiteralPath (Join-Path $fixtureRoot 'controller.py') -Destination (Join-Path $controllerStage 'controller.py')
    Copy-Item -LiteralPath $unityControllerModule -Destination (Join-Path $controllerStage 'unity_controller.py')
    Copy-Item -LiteralPath $guestRunner -Destination (Join-Path $controllerStage 'run-sandbox-certification.sh')
    Set-Utf8NoBomContent -Path (Join-Path $controllerStage 'hidden-policy.txt') -Value ($hiddenValue + "`n")

    $jobContract = [pscustomobject]@{
        environment_profile = 'profiles/ubuntu2404-unity6000.3.23f1.json'
        candidate_input = @{
            host_path = [IO.Path]::GetFullPath($candidateIso)
            guest_mount = '/run/gamebench/candidate-input'
            copy_to = '/var/lib/gamebench/sandbox-certification/candidate'
            read_only = $true
        }
        controller_input = @{
            host_path = [IO.Path]::GetFullPath($controllerIso)
            guest_mount = '/run/gamebench/controller-input'
            read_only = $true
        }
        output_disk = @{
            host_path = [IO.Path]::GetFullPath($outputDisk)
            guest_mount = '/run/gamebench/output'
            new_blank = $true
            format = 'raw-ext4'
            size_mb = $OutputDiskMB
        }
        users = @{
            unity_runner = 'unity-runner'
            gb_controller = 'gb-controller'
            distinct_uids = $true
        }
        isolation = @{
            host_mounts = @()
            candidate_network = 'none'
            controller_network = 'loopback-only'
            mount_namespace = $true
            hidepid = $true
            ptrace_restricted = $true
            cgroup_limits = $true
            apparmor_profile = 'gamebench-unity-runner'
            unity_api_key_present = $false
            license_state_network = 'none'
            same_vm_editor_and_player = $true
            limits = @{
                cpus = $CpuCount
                memory_mb = $MemoryMB
                disk_mb = 65536
                processes = 512
                wall_seconds = $WallSeconds
            }
        }
        lifecycle = @{
            offline_artifact_read = $true
            destroy_clone = $true
            destroy_output_disk = $true
            preserve_only_manifested_artifacts = $true
        }
    } | ConvertTo-Json -Depth 8
    Set-Utf8NoBomContent -Path $jobContractPath -Value ($jobContract + "`n")
    & $TaskPython $jobContractValidator $jobContractPath
    if ($LASTEXITCODE -ne 0) { throw 'VM job failed the repository fail-closed contract validator' }

    $wslMount = Get-WslMountCommand -Path @($runRoot, $evidenceRoot)
    & wsl.exe -d $WslDistribution -u root -- bash -lc $wslMount
    if ($LASTEXITCODE -ne 0) { throw 'Failed to make the runtime volumes available to the host-side WSL disk tool' }
    $candidateStageWsl = ConvertTo-WslDPath $candidateStage
    $controllerStageWsl = ConvertTo-WslDPath $controllerStage
    $candidateIsoWsl = ConvertTo-WslDPath $candidateIso
    $controllerIsoWsl = ConvertTo-WslDPath $controllerIso
    & wsl.exe -d $WslDistribution -u root -- genisoimage -quiet -R -J -V GBCANDIDATE -o $candidateIsoWsl $candidateStageWsl
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create candidate input ISO' }
    & wsl.exe -d $WslDistribution -u root -- genisoimage -quiet -R -J -V GBCONTROLLER -o $controllerIsoWsl $controllerStageWsl
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create controller input ISO' }

    & $qemuImg check $licenseImage
    if ($LASTEXITCODE -ne 0) { throw 'License-state image consistency check failed' }
    & $qemuImg create -f qcow2 -F qcow2 -b $licenseImage $overlay
    if ($LASTEXITCODE -ne 0) { throw 'Disposable overlay creation failed' }
    & $qemuImg create -f raw $outputDisk "${OutputDiskMB}M"
    if ($LASTEXITCODE -ne 0) { throw 'Blank output-disk creation failed' }

    $arguments = @(
        '-name', "gb-unity-sandbox-$runId",
        '-accel', 'whpx',
        '-machine', 'q35',
        '-uuid', $VmUuid.ToString(),
        '-cpu', 'max',
        '-smp', [string]$CpuCount,
        '-m', [string]$MemoryMB,
        '-drive', "file=$overlay,if=none,id=os,format=qcow2,cache=writeback,discard=unmap",
        '-device', 'virtio-blk-pci,drive=os,serial=GBROOT,bootindex=1',
        '-drive', "file=$candidateIso,if=none,id=candidate,format=raw,readonly=on",
        '-device', 'virtio-blk-pci,drive=candidate,serial=GBCANDIDATE',
        '-drive', "file=$controllerIso,if=none,id=controller,format=raw,readonly=on",
        '-device', 'virtio-blk-pci,drive=controller,serial=GBCONTROLLER',
        '-drive', "file=$outputDisk,if=none,id=output,format=raw,cache=writeback",
        '-device', 'virtio-blk-pci,drive=output,serial=GBOUTPUT',
        '-nic', 'none',
        '-device', 'virtio-serial-pci',
        '-chardev', "socket,id=qga0,host=127.0.0.1,port=$GuestAgentPort,server=on,wait=off",
        '-device', 'virtserialport,chardev=qga0,name=org.qemu.guest_agent.0',
        '-display', 'none',
        '-monitor', 'none',
        '-serial', "file:$serialLog"
    )
    $process = Start-Process -FilePath $qemu -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardError $qemuErrorLog
    Wait-Qga -Process $process -QemuErrorPath $qemuErrorLog

    $network = Invoke-Qga -Request @{ execute = 'guest-network-get-interfaces' }
    $interfaces = @(
        $network.return |
            ForEach-Object {
                $nameProperty = $_.PSObject.Properties['name']
                if ($nameProperty -and $nameProperty.Value) { [string]$nameProperty.Value }
            } |
            Sort-Object -Unique
    )
    if ($interfaces.Count -ne 1 -or $interfaces[0] -ne 'lo') {
        throw "Offline hard gate failed; guest interfaces were: $($interfaces -join ', ')"
    }

    $guestCommand = @"
set -euo pipefail
candidate_device="`$(blkid -L GBCANDIDATE)"
controller_device="`$(blkid -L GBCONTROLLER)"
output_device="/dev/disk/by-id/virtio-GBOUTPUT"
test -b "`$candidate_device"
test -b "`$controller_device"
test -b "`$output_device"
install -d -m 0700 /run/gamebench/controller-bootstrap
mount -t iso9660 -o ro,nosuid,nodev,noexec "`$controller_device" /run/gamebench/controller-bootstrap
cp /run/gamebench/controller-bootstrap/run-sandbox-certification.sh /root/run-sandbox-certification.sh
chmod 0500 /root/run-sandbox-certification.sh
umount /run/gamebench/controller-bootstrap
set +e
systemd-run --quiet --wait --collect --pipe \
  --unit=gamebench-sandbox-certification \
  --property=Delegate=yes \
  bash /root/run-sandbox-certification.sh "`$candidate_device" "`$controller_device" "`$output_device" '$nonce'
runner_exit=`$?
set -e
if test "`$runner_exit" -ne 0; then
  printf 'sandbox_runner_exit=%s\n' "`$runner_exit"
  for log in \
    /var/lib/gamebench/sandbox-certification/candidate/logs/editor.log \
    /var/lib/gamebench/sandbox-certification/candidate/logs/player.log \
    /var/lib/gamebench/sandbox-certification/candidate/target-logs/editor.log \
    /var/lib/gamebench/sandbox-certification/candidate/observer-logs/editor.log \
    /var/lib/gamebench/sandbox-certification/candidate/observer-logs/player.log \
    /var/lib/gamebench/sandbox-certification/candidate/protocol-logs/editor.log \
    /var/lib/gamebench/sandbox-certification/candidate/protocol-logs/player.log; do
    if test -f "`$log"; then
      printf '%s\n' "--- `$(basename "`$log") tail ---"
      tail -n 120 "`$log"
    fi
  done
  exit "`$runner_exit"
fi
rm -f /root/run-sandbox-certification.sh
"@
    $guestResult = Invoke-QgaExec -Command $guestCommand -HostWallSeconds $WallSeconds
    $guestFailure = ''
    if ($guestResult.ExitCode -ne 0) {
        $guestFailure = "Guest sandbox certification failed with exit code $($guestResult.ExitCode). stdout=$($guestResult.Stdout) stderr=$($guestResult.Stderr)"
    }
    elseif ($guestResult.Stdout -notmatch '(?m)^sandbox_status=passed$') {
        $guestFailure = "Guest did not emit the sandbox pass marker: $($guestResult.Stdout)"
    }

    Stop-DisposableVm -Process $process
    $process = $null

    # A gate that fails after the controller has already written its artifacts
    # still leaves measured evidence on the output disk, and `finally` destroys
    # that disk. Rescue it into `partial/` before failing: a run that produced
    # 165 calibrated cells and then tripped an unrelated fixture used to cost
    # the whole measurement, not just the verdict. Nothing here is promoted --
    # the run still throws, no summary is written, and no marker says passed.
    if ($guestFailure) {
        $partialRoot = Join-Path $evidenceRoot 'partial'
        New-Item -ItemType Directory -Path $partialRoot -Force | Out-Null
        $outputDiskWsl = ConvertTo-WslDPath $outputDisk
        $listing = & wsl.exe -d $WslDistribution -u root -- bash -lc "$wslMount; debugfs -R 'ls -l /' '$outputDiskWsl' 2>/dev/null"
        foreach ($name in ([regex]::Matches(($listing -join "`n"), '(?m)\s(\S+\.(?:json|zip|log))\s*$') | ForEach-Object { $_.Groups[1].Value })) {
            $destination = Join-Path $partialRoot $name
            $destinationWsl = ConvertTo-WslDPath $destination
            & wsl.exe -d $WslDistribution -u root -- bash -lc "$wslMount; debugfs -R 'dump -p /$name $destinationWsl' '$outputDiskWsl'" | Out-Null
        }
        $rescued = @(Get-ChildItem -LiteralPath $partialRoot -File -ErrorAction SilentlyContinue)
        Write-Warning ("Rescued {0} artifact(s) from the failed run into {1}" -f $rescued.Count, $partialRoot)
        throw $guestFailure
    }

    # Offline artifact read: the output disk is never inspected while the VM is running.
    $outputDiskWsl = ConvertTo-WslDPath $outputDisk
    $reportPathWsl = ConvertTo-WslDPath $reportPath
    $manifestPathWsl = ConvertTo-WslDPath $manifestPath
    $reportExtract = "$wslMount; debugfs -R 'dump -p /report.json $reportPathWsl' '$outputDiskWsl'"
    & wsl.exe -d $WslDistribution -u root -- bash -lc $reportExtract
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $reportPath)) { throw 'Offline report extraction failed' }
    $manifestExtract = "$wslMount; debugfs -R 'dump -p /artifact-manifest.json $manifestPathWsl' '$outputDiskWsl'"
    & wsl.exe -d $WslDistribution -u root -- bash -lc $manifestExtract
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $manifestPath)) { throw 'Offline manifest extraction failed' }

    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($report.status -ne 'passed' -or $report.hidden_value_disclosed) {
        throw 'Offline report did not satisfy sandbox gates'
    }
    $manifestedReport = @($manifest.artifacts | Where-Object path -eq 'report.json')
    if ($manifestedReport.Count -ne 1) { throw 'Artifact manifest must contain exactly one report.json entry' }
    if ((Get-FileSha256 $reportPath) -ne $manifestedReport[0].sha256) {
        throw 'Offline report digest does not match the controller-authored manifest'
    }
    foreach ($artifact in @($manifest.artifacts | Where-Object path -ne 'report.json')) {
        $relative = [string]$artifact.path
        if ($relative -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$') {
            throw "Unsafe artifact path in controller manifest: $relative"
        }
        $destination = Join-Path $evidenceRoot $relative
        $destinationWsl = ConvertTo-WslDPath $destination
        $extract = "$wslMount; debugfs -R 'dump -p /$relative $destinationWsl' '$outputDiskWsl'"
        & wsl.exe -d $WslDistribution -u root -- bash -lc $extract
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $destination)) {
            throw "Offline artifact extraction failed: $relative"
        }
        if ((Get-FileSha256 $destination) -ne [string]$artifact.sha256) {
            throw "Offline artifact digest mismatch: $relative"
        }
        if ((Get-Item -LiteralPath $destination).Length -ne [long]$artifact.bytes) {
            throw "Offline artifact length mismatch: $relative"
        }
    }

    $semanticValidationJson = (& $TaskPython $causalityEvidenceValidator $evidenceRoot) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw 'Exported behavior-causality evidence failed shared-reader validation' }
    $semanticValidation = $semanticValidationJson | ConvertFrom-Json
    if ($semanticValidation.status -ne 'passed') {
        throw 'Exported behavior-causality evidence validation did not return passed status'
    }
    $targetLockSource = Join-Path $targetScaffoldRoot 'Packages\packages-lock.json'
    $targetLockExport = Join-Path $evidenceRoot 'target-packages-lock.json'
    if ((Get-FileSha256 $targetLockSource) -ne (Get-FileSha256 $targetLockExport)) {
        throw 'Unity import mutated the frozen target-scaffold package lock'
    }

    $values = (($guestResult.Stdout -split "`r?`n") |
        Where-Object { $_ -match '^[a-z][a-z0-9_]*=' }) -join "`n" | ConvertFrom-StringData
    $summary = [pscustomobject]@{
        schema = 'gamebench.unity-m5-005-certification.v1'
        status = 'sandbox_subgate_passed'
        score_eligible = $false
        captured_at = (Get-Date).ToUniversalTime().ToString('o')
        run_id = $runId
        profile_id = 'ubuntu2404-unity6000.3.23f1-candidate'
        backend = 'qemu-11.1.0-whpx-local-owner-approved'
        owner_backend_accepted = $true
        fresh_overlay = $true
        separate_read_only_candidate_input = $true
        separate_read_only_controller_input = $true
        new_output_disk = $true
        offline_artifact_read = $true
        destroyed_after_offline_read = $true
        qemu_network_argument = '-nic none'
        guest_interfaces = $interfaces
        candidate_mount_namespace = $true
        candidate_pid_namespace = $true
        apparmor_profile = 'gamebench-unity-runner'
        ptrace_scope = [int]$values.ptrace_scope
        candidate_limits = @{
            cpus = $CpuCount
            cpu_max = $values.candidate_cpu_max
            memory_bytes = [long]$values.candidate_memory_max
            pids = [int]$values.candidate_pids_max
            root_disk_mb = 65536
            wall_seconds = $WallSeconds
        }
        resource_negative_tests = @{
            memory_limit_enforced = ([int]$values.memory_negative_exit -ne 0)
            process_limit_enforced = ([int]$values.pids_negative_exit -ne 0)
            wall_limit_enforced = ([int]$values.wall_negative_exit -eq 124)
        }
        candidate_probe = $report.candidate_probe
        unity_fixture = $report.unity_fixture
        protocol_fixture = $report.protocol_fixture
        observer_fixture = $report.observer_fixture
        behavior_causality_fixture = $report.behavior_causality
        behavior_causality_shared_reader_validation = $semanticValidation
        target_scaffold = @{
            import_and_compile = 'Succeeded'
            package_lock_unchanged = $true
            package_lock_sha256 = Get-FileSha256 $targetLockSource
        }
        controller_uid = [int]$report.controller_uid
        unity_runner_uid = [int]$values.unity_runner_uid
        controller_authored_report_sha256 = Get-FileSha256 $reportPath
        candidate_input_iso_sha256 = Get-FileSha256 $candidateIso
        controller_input_iso_sha256 = Get-FileSha256 $controllerIso
        license_state_image_sha256 = Get-FileSha256 $licenseImage
        duration_seconds = [Math]::Round(((Get-Date) - $startedAt).TotalSeconds, 3)
        remaining_gates = @(
            'Unity Personal entitlement revalidation remains an operational lifecycle risk'
        )
    } | ConvertTo-Json -Depth 10
    Set-Utf8NoBomContent -Path $summaryPath -Value ($summary + "`n")

    Write-Output "Sandbox certification passed; evidence: $summaryPath"
}
finally {
    Stop-DisposableVm -Process $process
    foreach ($path in @($overlay, $outputDisk, $candidateIso, $controllerIso, $candidateStage, $controllerStage, $jobContractPath)) {
        if (Test-Path -LiteralPath $path) {
            Assert-UnderRuntime -Path $path -RuntimeRoot $runtimeRoot
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
    if ((Test-Path -LiteralPath $runRoot) -and -not (Get-ChildItem -LiteralPath $runRoot -Force)) {
        Assert-UnderRuntime -Path $runRoot -RuntimeRoot $runtimeRoot
        Remove-Item -LiteralPath $runRoot
    }
}
