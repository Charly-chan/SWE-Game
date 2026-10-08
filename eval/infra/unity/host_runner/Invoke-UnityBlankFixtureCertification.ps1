[CmdletBinding()]
param(
    [string]$InfrastructureRoot = 'D:\gamebench-unity-vm',
    [string]$QemuRoot = 'D:\tools\qemu',
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path,
    [int]$IterationCount = 20,
    [int]$GuestAgentPort = 2223,
    [int]$MemoryMB = 12288,
    [int]$CpuCount = 8,
    [Guid]$VmUuid = '8a287f35-e5a3-4fa2-bab1-d2908c4f180d'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($IterationCount -lt 1) {
    throw 'IterationCount must be positive'
}

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

function Wait-Qga {
    $deadline = (Get-Date).AddMinutes(3)
    do {
        try {
            $null = Invoke-Qga -Request @{ execute = 'guest-ping' } -ReadTimeoutMS 15000
            return
        }
        catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)
    throw 'QEMU guest-agent did not become ready'
}

function Invoke-QgaExec {
    param([Parameter(Mandatory)][string]$Command)

    $started = Invoke-Qga -Request @{
        execute = 'guest-exec'
        arguments = @{
            path = '/bin/bash'
            arg = @('-lc', $Command)
            'capture-output' = $true
        }
    }
    $guestPid = [int]$started.return.pid
    do {
        Start-Sleep -Seconds 2
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

    return [pscustomobject]@{
        ExitCode = [int]$status.return.exitcode
        Stdout = $stdout
        Stderr = $stderr
    }
}

function Read-QgaFile {
    param([Parameter(Mandatory)][string]$Path)

    $opened = Invoke-Qga -Request @{
        execute = 'guest-file-open'
        arguments = @{ path = $Path; mode = 'rb' }
    }
    $handle = [int]$opened.return
    $memory = [IO.MemoryStream]::new()
    try {
        do {
            $read = Invoke-Qga -Request @{
                execute = 'guest-file-read'
                arguments = @{ handle = $handle; count = 32768 }
            }
            $bufferProperty = $read.return.PSObject.Properties['buf-b64']
            if ($bufferProperty -and $bufferProperty.Value) {
                $bytes = [Convert]::FromBase64String([string]$bufferProperty.Value)
                $memory.Write($bytes, 0, $bytes.Length)
            }
        } while (-not $read.return.eof)
        return $memory.ToArray()
    }
    finally {
        try {
            $null = Invoke-Qga -Request @{
                execute = 'guest-file-close'
                arguments = @{ handle = $handle }
            }
        } finally {
            $memory.Dispose()
        }
    }
}

function Stop-DisposableVm {
    param([Diagnostics.Process]$Process)

    if (-not $Process -or $Process.HasExited) { return }
    try {
        $null = Invoke-Qga -Request @{
            execute = 'guest-shutdown'
            arguments = @{ mode = 'powerdown' }
        } -ReadTimeoutMS 5000
    }
    catch { }
    $Process.WaitForExit(60000) | Out-Null
    if (-not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force
        $Process.WaitForExit()
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

$qemu = Join-Path $QemuRoot 'qemu-system-x86_64.exe'
$qemuImg = Join-Path $QemuRoot 'qemu-img.exe'
$licenseImage = Join-Path $InfrastructureRoot 'license-exchange\ubuntu2404-unity6000.3.23f1-personal-license-scrubbed.qcow2'
$runtime = Join-Path $InfrastructureRoot 'runtime'
$fixture = Join-Path $RepositoryRoot 'eval\infra\unity\fixtures\blank_protocol'
$runId = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$evidenceRoot = Join-Path $InfrastructureRoot "evidence\m5-004-blank-$runId"
$archive = Join-Path $runtime "blank-protocol-$runId.tar.gz"

foreach ($required in @($qemu, $qemuImg, $licenseImage, $fixture)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required path is missing: $required"
    }
}
if (Get-Process qemu-system-x86_64 -ErrorAction SilentlyContinue) {
    throw 'Refusing to start certification while another QEMU VM is running'
}
if (Test-Path -LiteralPath $archive) {
    throw "Refusing to overwrite fixture archive: $archive"
}

New-Item -ItemType Directory -Force -Path $runtime, $evidenceRoot | Out-Null
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $evidenceRoot '/inheritance:r' '/grant:r' "${identity}:(OI)(CI)(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to restrict evidence-directory ACL' }

& $qemuImg check $licenseImage
if ($LASTEXITCODE -ne 0) { throw 'License-state image consistency check failed' }
$licenseImageDigest = Get-FileSha256 -Path $licenseImage

& tar.exe -czf $archive -C $fixture .
if ($LASTEXITCODE -ne 0) { throw 'Failed to package the blank fixture' }
$fixtureArchiveDigest = Get-FileSha256 -Path $archive
$fixtureArchiveBase64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($archive))

$results = [Collections.Generic.List[object]]::new()
$summaryPath = Join-Path $evidenceRoot 'summary.json'
$failed = $false

try {
    for ($iteration = 1; $iteration -le $IterationCount; $iteration++) {
        $iterationName = 'iteration-{0:d2}' -f $iteration
        $iterationRoot = Join-Path $evidenceRoot $iterationName
        $overlay = Join-Path $runtime "$runId-$iterationName.qcow2"
        $serialLog = Join-Path $iterationRoot 'serial.log'
        $process = $null
        $startedAt = Get-Date
        New-Item -ItemType Directory -Path $iterationRoot | Out-Null

        try {
            if (Test-Path -LiteralPath $overlay) {
                throw "Refusing to overwrite iteration overlay: $overlay"
            }
            & $qemuImg create -f qcow2 -F qcow2 -b $licenseImage $overlay
            if ($LASTEXITCODE -ne 0) { throw 'Iteration-overlay creation failed' }

            $arguments = @(
                '-name', "gb-unity-blank-$iterationName",
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
                '-serial', "file:$serialLog"
            )
            $process = Start-Process -FilePath $qemu -ArgumentList $arguments -PassThru -WindowStyle Hidden
            Wait-Qga

            $network = Invoke-Qga -Request @{ execute = 'guest-network-get-interfaces' }
            $interfaces = @($network.return.name | Sort-Object -Unique)
            if ($interfaces.Count -ne 1 -or $interfaces[0] -ne 'lo') {
                throw "Offline hard gate failed; guest interfaces were: $($interfaces -join ', ')"
            }

            $guestScript = @'
set -euo pipefail
root=/var/lib/gamebench/blank-certification
rm -rf "$root"
install -d -m 0700 -o unity-runner -g unity-runner "$root/project" "$root/build" "$root/runtime" "$root/logs"
printf '%s' '__ARCHIVE__' | base64 -d > /tmp/blank-protocol-fixture.tar.gz
tar -xzf /tmp/blank-protocol-fixture.tar.gz -C "$root/project"
rm -f /tmp/blank-protocol-fixture.tar.gz
chown -R unity-runner:unity-runner "$root"
test "$(find /sys/class/net -mindepth 1 -maxdepth 1 -printf '%f\n')" = lo
runuser -u unity-runner -- env HOME=/home/unity-runner bash -lc '
  set -euo pipefail
  export DISPLAY=:98
  export GB_FIXTURE_BUILD_DIR=/var/lib/gamebench/blank-certification/build
  Xvfb :98 -screen 0 960x540x24 -nolisten tcp -ac > /var/lib/gamebench/blank-certification/logs/xvfb.log 2>&1 &
  xvfb_pid=$!
  trap "kill $xvfb_pid 2>/dev/null || true" EXIT
  for attempt in $(seq 1 50); do xdpyinfo -display :98 >/dev/null 2>&1 && break; sleep 0.2; done
  xdpyinfo -display :98 >/dev/null
  timeout 600 /opt/unity/current/Editor/Unity -batchmode -nographics -projectPath /var/lib/gamebench/blank-certification/project -executeMethod GameBench.Fixtures.Editor.BuildBlankFixture.BuildLinuxPlayer -quit -logFile /var/lib/gamebench/blank-certification/logs/editor.log
  test -x /var/lib/gamebench/blank-certification/build/BlankFixture.x86_64
  timeout 120 /var/lib/gamebench/blank-certification/build/BlankFixture.x86_64 -screen-fullscreen 0 -screen-width 960 -screen-height 540 -screen-refresh-rate 60 -logFile /var/lib/gamebench/blank-certification/logs/player.log --gb-output=/var/lib/gamebench/blank-certification/runtime
'
python3 - <<'PY'
import hashlib, json, pathlib, struct
root = pathlib.Path('/var/lib/gamebench/blank-certification')
build = json.loads((root / 'build/build-result.json').read_text())
runtime = json.loads((root / 'runtime/runtime-result.json').read_text())
png = (root / 'runtime/blank-fixture.png').read_bytes()
assert build['result'] == 'Succeeded' and build['errors'] == 0, build
assert runtime == {
    'schema': 'gamebench.unity-blank-fixture-runtime.v1',
    'status': 'passed',
    'width': 960,
    'height': 540,
    'unity_version': '6000.3.23f1',
}, runtime
assert png[:8] == b'\x89PNG\r\n\x1a\n'
assert struct.unpack('>II', png[16:24]) == (960, 540)
print('fixture_build_result=Succeeded')
print('fixture_build_errors=0')
print('fixture_runtime_status=passed')
print('fixture_png_dimensions=960x540')
print('fixture_png_sha256=' + hashlib.sha256(png).hexdigest())
print('fixture_png_bytes=' + str(len(png)))
PY
printf 'unity_version='; runuser -u unity-runner -- env HOME=/home/unity-runner /opt/unity/current/Editor/Unity -version
printf 'network_devices='; find /sys/class/net -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort | paste -sd, -
printf 'editor_log_sha256='; sha256sum "$root/logs/editor.log" | cut -d' ' -f1
printf 'player_log_sha256='; sha256sum "$root/logs/player.log" | cut -d' ' -f1
printf 'xvfb_log_sha256='; sha256sum "$root/logs/xvfb.log" | cut -d' ' -f1
printf 'license_error_count='; grep -Eci 'No valid Unity Editor license|Failed to activate|LICENSE SYSTEM.*ERROR|License is not active' "$root/logs/editor.log" || true
printf 'renderer='; sed -n 's/^Renderer:[[:space:]]*//p' "$root/logs/player.log" | head -n1
'@.Replace('__ARCHIVE__', $fixtureArchiveBase64)
            $encodedGuestScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($guestScript))
            $guestResult = Invoke-QgaExec -Command "printf '%s' '$encodedGuestScript' | base64 -d | bash"
            if ($guestResult.ExitCode -ne 0) {
                throw "Guest fixture failed with exit code $($guestResult.ExitCode): $($guestResult.Stderr)"
            }

            $evidenceText = (($guestResult.Stdout -split "`r?`n") |
                Where-Object { $_ -match '^[a-z][a-z0-9_]*=' }) -join "`n"
            $values = $evidenceText | ConvertFrom-StringData
            $requiredValues = @{
                fixture_build_result = 'Succeeded'
                fixture_build_errors = '0'
                fixture_runtime_status = 'passed'
                fixture_png_dimensions = '960x540'
                unity_version = '6000.3.23f1'
                network_devices = 'lo'
                license_error_count = '0'
            }
            foreach ($entry in $requiredValues.GetEnumerator()) {
                if ($values[$entry.Key] -ne $entry.Value) {
                    throw "Guest evidence mismatch for $($entry.Key): $($values[$entry.Key])"
                }
            }

            $buildPath = Join-Path $iterationRoot 'build-result.json'
            $runtimePath = Join-Path $iterationRoot 'runtime-result.json'
            $pngPath = Join-Path $iterationRoot 'blank-fixture.png'
            [IO.File]::WriteAllBytes($buildPath, (Read-QgaFile -Path '/var/lib/gamebench/blank-certification/build/build-result.json'))
            [IO.File]::WriteAllBytes($runtimePath, (Read-QgaFile -Path '/var/lib/gamebench/blank-certification/runtime/runtime-result.json'))
            [IO.File]::WriteAllBytes($pngPath, (Read-QgaFile -Path '/var/lib/gamebench/blank-certification/runtime/blank-fixture.png'))
            if ((Get-FileSha256 -Path $pngPath) -ne $values.fixture_png_sha256) {
                throw 'Exported PNG digest did not match guest evidence'
            }

            $result = [pscustomobject]@{
                iteration = $iteration
                status = 'passed'
                fresh_overlay = $true
                qemu_network_argument = '-nic none'
                guest_interfaces = $interfaces
                unity_version = $values.unity_version
                renderer = $values.renderer
                build_result = $values.fixture_build_result
                build_errors = [int]$values.fixture_build_errors
                runtime_status = $values.fixture_runtime_status
                png_dimensions = $values.fixture_png_dimensions
                png_bytes = [int]$values.fixture_png_bytes
                png_sha256 = $values.fixture_png_sha256
                editor_log_sha256 = $values.editor_log_sha256
                player_log_sha256 = $values.player_log_sha256
                xvfb_log_sha256 = $values.xvfb_log_sha256
                license_error_count = [int]$values.license_error_count
                duration_seconds = [Math]::Round(((Get-Date) - $startedAt).TotalSeconds, 3)
            }
            $results.Add($result)
            $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $iterationRoot 'result.json') -Encoding utf8NoBOM
            Write-Output "[$iteration/$IterationCount] passed in $($result.duration_seconds)s; png=$($result.png_sha256)"
        }
        catch {
            $failed = $true
            $failure = [pscustomobject]@{
                iteration = $iteration
                status = 'failed'
                fresh_overlay = $true
                qemu_network_argument = '-nic none'
                error = $_.Exception.Message
                duration_seconds = [Math]::Round(((Get-Date) - $startedAt).TotalSeconds, 3)
            }
            $results.Add($failure)
            $failure | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $iterationRoot 'result.json') -Encoding utf8NoBOM
            Write-Warning "[$iteration/$IterationCount] failed: $($_.Exception.Message)"
        }
        finally {
            Stop-DisposableVm -Process $process
            if (Test-Path -LiteralPath $overlay) {
                $resolvedRuntime = (Resolve-Path -LiteralPath $runtime).Path
                $resolvedOverlay = (Resolve-Path -LiteralPath $overlay).Path
                if (-not $resolvedOverlay.StartsWith($resolvedRuntime + '\', [StringComparison]::OrdinalIgnoreCase)) {
                    throw "Refusing to delete overlay outside runtime: $resolvedOverlay"
                }
                Remove-Item -LiteralPath $resolvedOverlay
            }
        }

        [pscustomobject]@{
            schema = 'gamebench.unity-blank-certification.v1'
            status = if ($failed) { 'failed' } elseif ($results.Count -eq $IterationCount) { 'passed' } else { 'running' }
            score_eligible = $false
            captured_at = (Get-Date).ToUniversalTime().ToString('o')
            iteration_count_requested = $IterationCount
            iteration_count_passed = @($results | Where-Object status -eq 'passed').Count
            fixture_archive_sha256 = $fixtureArchiveDigest
            license_state_image_sha256 = $licenseImageDigest
            qemu_network_argument = '-nic none'
            control_channel = 'QEMU guest-agent over host loopback only'
            artifact_channel = 'QEMU guest-agent (preliminary; M5-005 output-disk lifecycle not certified)'
            results = $results
            remaining_gates = @(
                'Protocol fixture has not passed',
                'cat_defense positive/negative fixtures have not passed',
                'M5-005 input/controller/output disk lifecycle and threat-model certification has not passed',
                'QEMU/WHPX remains a local candidate backend'
            )
        } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding utf8NoBOM

        if ($failed) { break }
    }
}
finally {
    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive
    }
}

if ($failed) {
    throw "Blank fixture certification failed; evidence: $summaryPath"
}

Write-Output "Blank fixture certification passed; evidence: $summaryPath"
