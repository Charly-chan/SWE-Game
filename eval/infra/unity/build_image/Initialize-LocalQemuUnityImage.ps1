[CmdletBinding()]
param(
    [string]$InfrastructureRoot = 'D:\gamebench-unity-vm',
    [string]$QemuRoot = 'D:\tools\qemu',
    [int]$MemoryMB = 12288,
    [int]$CpuCount = 8,
    [int]$SshPort = 2222,
    [Guid]$VmUuid = '8a287f35-e5a3-4fa2-bab1-d2908c4f180d'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Convert-ToWslPath {
    param([Parameter(Mandatory)][string]$WindowsPath)

    $resolved = [IO.Path]::GetFullPath($WindowsPath)
    if ($resolved -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "Only drive-qualified Windows paths can be converted: $resolved"
    }
    $drive = $Matches[1].ToLowerInvariant()
    $tail = $Matches[2] -replace '\\', '/'
    return "/mnt/$drive/$tail"
}

function Assert-CommandSucceeded {
    param([Parameter(Mandatory)][string]$Description)

    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

$baseImage = Join-Path $InfrastructureRoot 'downloads\ubuntu-24.04-server-cloudimg-amd64.img'
$checksumFile = Join-Path $InfrastructureRoot 'downloads\SHA256SUMS'
$privateKey = Join-Path $InfrastructureRoot 'keys\gb-admin-ed25519'
$publicKey = "$privateKey.pub"
$overlay = Join-Path $InfrastructureRoot 'images\ubuntu2404-unity-builder.qcow2'
$seedIso = Join-Path $InfrastructureRoot 'seed\seed.iso'
$renderedUserData = Join-Path $InfrastructureRoot 'seed\user-data'
$runtime = Join-Path $InfrastructureRoot 'runtime'
$serialLog = Join-Path $runtime 'builder-serial.log'
$pidFile = Join-Path $runtime 'builder.pid'
$qemu = Join-Path $QemuRoot 'qemu-system-x86_64.exe'
$qemuImg = Join-Path $QemuRoot 'qemu-img.exe'
$templateRoot = Join-Path $PSScriptRoot 'cloud-init'

foreach ($required in @($baseImage, $checksumFile, $privateKey, $publicKey, $qemu, $qemuImg)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required file is missing: $required"
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path $overlay), (Split-Path $seedIso), $runtime | Out-Null

$checksumLine = Get-Content -LiteralPath $checksumFile |
    Where-Object { $_ -match 'ubuntu-24\.04-server-cloudimg-amd64\.img$' } |
    Select-Object -First 1
if (-not $checksumLine) {
    throw "The Ubuntu image is absent from $checksumFile"
}
$expected = ($checksumLine -split '\s+')[0].ToLowerInvariant()
$actual = (Get-FileHash -LiteralPath $baseImage -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) {
    throw "Ubuntu base image SHA-256 mismatch: expected $expected, got $actual"
}

if (-not (Test-Path -LiteralPath $overlay)) {
    & $qemuImg create -f qcow2 -F qcow2 -b $baseImage $overlay
    Assert-CommandSucceeded 'qemu-img create'
    & $qemuImg resize $overlay 64G
    Assert-CommandSucceeded 'qemu-img resize'
}

$sshPublicKey = (Get-Content -Raw -LiteralPath $publicKey).Trim()
$userDataTemplate = Get-Content -Raw -LiteralPath (Join-Path $templateRoot 'user-data.tmpl')
if (-not $userDataTemplate.Contains('@@SSH_PUBLIC_KEY@@')) {
    throw 'cloud-init user-data template does not contain the SSH key placeholder'
}
[IO.File]::WriteAllText(
    $renderedUserData,
    $userDataTemplate.Replace('@@SSH_PUBLIC_KEY@@', $sshPublicKey),
    [Text.UTF8Encoding]::new($false)
)

$wslSeed = Convert-ToWslPath $seedIso
$wslUserData = Convert-ToWslPath $renderedUserData
$wslMetaData = Convert-ToWslPath (Join-Path $templateRoot 'meta-data')
& wsl.exe -d Ubuntu-22.04 -- cloud-localds $wslSeed $wslUserData $wslMetaData
Assert-CommandSucceeded 'cloud-localds'

if (Test-Path -LiteralPath $pidFile) {
    $existingPid = (Get-Content -Raw -LiteralPath $pidFile).Trim()
    if ($existingPid -and (Get-Process -Id ([int]$existingPid) -ErrorAction SilentlyContinue)) {
        throw "Builder VM already appears to be running as PID $existingPid"
    }
}

$qemuArguments = @(
    '-name', 'gb-unity-builder',
    '-accel', 'whpx',
    '-machine', 'q35',
    '-uuid', $VmUuid.ToString(),
    '-cpu', 'max',
    '-smp', [string]$CpuCount,
    '-m', [string]$MemoryMB,
    '-drive', "file=$overlay,if=virtio,format=qcow2,cache=writeback,discard=unmap",
    '-drive', "file=$seedIso,if=virtio,format=raw,readonly=on",
    '-netdev', "user,id=net0,hostfwd=tcp:127.0.0.1:$SshPort-:22",
    '-device', 'virtio-net-pci,netdev=net0,mac=52:54:00:12:34:56',
    '-device', 'virtio-rng-pci',
    '-display', 'none',
    '-monitor', 'none',
    '-serial', "file:$serialLog",
    '-pidfile', $pidFile
)

$process = Start-Process -FilePath $qemu -ArgumentList $qemuArguments -PassThru -WindowStyle Hidden
Write-Output "Started local non-score-eligible Ubuntu image-builder VM (PID $($process.Id))."
Write-Output "SSH becomes available at gb-admin@127.0.0.1:$SshPort after cloud-init finishes."
Write-Output "Serial log: $serialLog"
