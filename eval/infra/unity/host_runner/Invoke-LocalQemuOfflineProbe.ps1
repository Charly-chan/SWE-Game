[CmdletBinding()]
param(
    [string]$InfrastructureRoot = 'D:\gamebench-unity-vm',
    [string]$QemuRoot = 'D:\tools\qemu',
    [int]$GuestAgentPort = 2223,
    [int]$MemoryMB = 12288,
    [int]$CpuCount = 8,
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
    finally {
        $client.Dispose()
    }
}

function Invoke-QgaExec {
    param([Parameter(Mandatory)][string]$Command)

    $started = Invoke-Qga @{
        execute = 'guest-exec'
        arguments = @{
            path = '/bin/bash'
            arg = @('-lc', $Command)
            'capture-output' = $true
        }
    }
    $guestPid = [int]$started.return.pid
    do {
        Start-Sleep -Milliseconds 500
        $status = Invoke-Qga @{
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
    if ([int]$status.return.exitcode -ne 0) {
        throw "Guest probe failed with exit code $($status.return.exitcode): $stderr"
    }
    return $stdout
}

$qemu = Join-Path $QemuRoot 'qemu-system-x86_64.exe'
$qemuImg = Join-Path $QemuRoot 'qemu-img.exe'
$baseImage = Join-Path $InfrastructureRoot 'images\ubuntu2404-unity6000.3.23f1-candidate.qcow2'
$runtime = Join-Path $InfrastructureRoot 'runtime'
$overlay = Join-Path $runtime 'offline-probe.qcow2'
$serialLog = Join-Path $runtime 'offline-probe-serial.log'
$pidFile = Join-Path $runtime 'offline-probe.pid'
$evidence = Join-Path $runtime 'offline-probe-evidence.json'

foreach ($required in @($qemu, $qemuImg, $baseImage)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required file is missing: $required"
    }
}
if (Test-Path -LiteralPath $overlay) {
    throw "Refusing to overwrite an existing probe overlay: $overlay"
}
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

& $qemuImg check $baseImage
if ($LASTEXITCODE -ne 0) { throw 'Base-image consistency check failed' }
$baseDigest = (Get-FileHash -LiteralPath $baseImage -Algorithm SHA256).Hash.ToLowerInvariant()
& $qemuImg create -f qcow2 -F qcow2 -b $baseImage $overlay
if ($LASTEXITCODE -ne 0) { throw 'Probe-overlay creation failed' }

$process = $null
try {
    $arguments = @(
        '-name', 'gb-unity-offline-probe',
        '-accel', 'whpx',
        '-machine', 'q35',
        '-uuid', $VmUuid.ToString(),
        '-cpu', 'max',
        '-smp', [string]$CpuCount,
        '-m', [string]$MemoryMB,
        '-drive', "file=$overlay,if=virtio,format=qcow2,cache=writeback,discard=unmap",
        '-nic', 'none',
        '-device', 'virtio-serial-pci',
        '-chardev', "socket,id=qga0,host=127.0.0.1,port=$GuestAgentPort,server=on,wait=off",
        '-device', 'virtserialport,chardev=qga0,name=org.qemu.guest_agent.0',
        '-display', 'none',
        '-monitor', 'none',
        '-serial', "file:$serialLog",
        '-pidfile', $pidFile
    )
    $process = Start-Process -FilePath $qemu -ArgumentList $arguments -PassThru -WindowStyle Hidden

    $deadline = (Get-Date).AddMinutes(3)
    $ready = $false
    do {
        try {
            $null = Invoke-Qga -Request @{ execute = 'guest-ping' } -ReadTimeoutMS 15000
            $ready = $true
        }
        catch {
            Start-Sleep -Seconds 2
        }
    } while (-not $ready -and (Get-Date) -lt $deadline)
    if (-not $ready) { throw 'QEMU guest-agent did not become ready' }

    $network = Invoke-Qga -Request @{ execute = 'guest-network-get-interfaces' }
    $interfaces = @($network.return.name | Sort-Object -Unique)
    if ($interfaces.Count -ne 1 -or $interfaces[0] -ne 'lo') {
        throw "Offline hard gate failed; guest interfaces were: $($interfaces -join ', ')"
    }

    $command = @'
set -euo pipefail
uname -a
printf 'network_devices='
find /sys/class/net -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort | paste -sd, -
ip -brief address
ip route
printf 'unity_version='
runuser -u unity-runner -- env HOME=/home/unity-runner /opt/unity/current/Editor/Unity -version
id unity-runner
id gb-controller
if runuser -u unity-runner -- sudo -n true 2>/dev/null; then
    echo unity_runner_sudo=unexpected
    exit 9
else
    echo unity_runner_sudo=denied
fi
printf 'apparmor='; systemctl is-active apparmor
printf 'auditd='; systemctl is-active auditd
count="$(find /etc /home /root /var /opt -xdev -type f \( -iname '*.alf' -o -iname '*.ulf' -o -iname '*Licensing*.log' \) -print | wc -l)"
echo "license_artifact_count=$count"
test "$count" -eq 0
'@
    $output = Invoke-QgaExec -Command $command
    if ($output -notmatch '(?m)^network_devices=lo$' -or
        $output -notmatch '(?m)^unity_version=6000\.3\.23f1$' -or
        $output -notmatch '(?m)^license_artifact_count=0$') {
        throw "Offline guest evidence failed validation:`n$output"
    }

    [pscustomobject]@{
        schema = 'gamebench.unity-offline-probe.v1'
        probe = 'qemu-whpx-explicit-nic-none-license-clean'
        score_eligible = $false
        base_image_digest = "sha256:$baseDigest"
        qemu_version = '11.1.0'
        qemu_network_argument = '-nic none'
        qga_interfaces = $interfaces
        guest_exit_code = 0
        captured_at = (Get-Date).ToUniversalTime().ToString('o')
        output = $output
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $evidence -Encoding utf8NoBOM
    Write-Output "Offline probe passed; evidence: $evidence"
}
finally {
    if ($process -and -not $process.HasExited) {
        try {
            $null = Invoke-Qga -Request @{
                execute = 'guest-shutdown'
                arguments = @{ mode = 'powerdown' }
            } -ReadTimeoutMS 5000
        } catch { }
        $process.WaitForExit(60000) | Out-Null
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
            $process.WaitForExit()
        }
    }
    if (Test-Path -LiteralPath $overlay) {
        $resolvedRuntime = (Resolve-Path -LiteralPath $runtime).Path
        $resolvedOverlay = (Resolve-Path -LiteralPath $overlay).Path
        if (-not $resolvedOverlay.StartsWith($resolvedRuntime + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to delete probe overlay outside runtime: $resolvedOverlay"
        }
        Remove-Item -LiteralPath $resolvedOverlay
    }
}
