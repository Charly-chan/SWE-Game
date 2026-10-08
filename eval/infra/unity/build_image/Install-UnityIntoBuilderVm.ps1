[CmdletBinding()]
param(
    [string]$InfrastructureRoot = 'D:\gamebench-unity-vm',
    [int]$SshPort = 2222
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-VendorMd5 {
    param([Parameter(Mandatory)][string]$Integrity)

    if (-not $Integrity.StartsWith('md5-', [StringComparison]::Ordinal)) {
        throw "Unsupported Unity integrity value: $Integrity"
    }
    return [Convert]::ToHexString(
        [Convert]::FromBase64String($Integrity.Substring(4))
    ).ToLowerInvariant()
}

function Assert-FileMd5 {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Expected
    )

    $actual = (Get-FileHash -LiteralPath $Path -Algorithm MD5).Hash.ToLowerInvariant()
    if ($actual -ne $Expected) {
        throw "Unity archive integrity mismatch for $Path`: expected $Expected, got $actual"
    }
}

$releasePath = Join-Path $InfrastructureRoot 'downloads\unity-6000.3.23f1-release.json'
$editorPath = Join-Path $InfrastructureRoot 'downloads\Unity-6000.3.23f1.tar.xz'
$modulePath = Join-Path $InfrastructureRoot 'downloads\UnitySetup-Linux-IL2CPP-Support-for-Editor-6000.3.23f1.tar.xz'
$privateKey = Join-Path $InfrastructureRoot 'keys\gb-admin-ed25519'
$knownHosts = Join-Path $InfrastructureRoot 'runtime\known_hosts'
$packageLock = Join-Path $InfrastructureRoot 'runtime\gamebench-package-lock.txt'
$guestInstaller = Join-Path $PSScriptRoot 'guest-install-unity.sh'

foreach ($required in @($releasePath, $editorPath, $modulePath, $privateKey, $knownHosts, $guestInstaller)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required file is missing: $required"
    }
}

$release = Get-Content -Raw -LiteralPath $releasePath | ConvertFrom-Json
$candidate = @($release.results) | Where-Object {
    $_.version -eq '6000.3.23f1' -and $_.shortRevision -eq '09d2ecc7fb28'
} | Select-Object -First 1
if (-not $candidate) {
    throw 'The cached Unity Releases API response does not identify 6000.3.23f1/09d2ecc7fb28'
}
$editor = @($candidate.downloads) | Where-Object {
    $_.platform -eq 'LINUX' -and $_.architecture -eq 'X86_64' -and $_.type -eq 'TAR_XZ'
} | Select-Object -First 1
$module = @($editor.modules) | Where-Object { $_.id -eq 'linux-il2cpp' } | Select-Object -First 1
if (-not $editor -or -not $module) {
    throw 'The Unity Releases API response lacks the Linux editor or linux-il2cpp module'
}

Assert-FileMd5 -Path $editorPath -Expected (Get-VendorMd5 $editor.integrity)
Assert-FileMd5 -Path $modulePath -Expected (Get-VendorMd5 $module.integrity)

$sshOptions = @(
    '-p', [string]$SshPort,
    '-i', $privateKey,
    '-o', 'BatchMode=yes',
    '-o', 'IdentitiesOnly=yes',
    '-o', 'Compression=no',
    '-o', 'StrictHostKeyChecking=yes',
    '-o', "UserKnownHostsFile=$knownHosts"
)
$scpOptions = @(
    '-P', [string]$SshPort,
    '-i', $privateKey,
    '-o', 'BatchMode=yes',
    '-o', 'IdentitiesOnly=yes',
    '-o', 'Compression=no',
    '-o', 'StrictHostKeyChecking=yes',
    '-o', "UserKnownHostsFile=$knownHosts"
)

& scp.exe @scpOptions $editorPath $modulePath $guestInstaller 'gb-admin@127.0.0.1:/var/tmp/'
if ($LASTEXITCODE -ne 0) { throw "SCP upload failed with exit code $LASTEXITCODE" }
& ssh.exe @sshOptions 'gb-admin@127.0.0.1' 'sudo bash /var/tmp/guest-install-unity.sh && sudo install -m 0644 -o gb-admin -g gb-admin /opt/unity/current/gamebench-package-lock.txt /home/gb-admin/gamebench-package-lock.txt'
if ($LASTEXITCODE -ne 0) { throw "Guest Unity installation failed with exit code $LASTEXITCODE" }
& scp.exe @scpOptions 'gb-admin@127.0.0.1:/home/gb-admin/gamebench-package-lock.txt' $packageLock
if ($LASTEXITCODE -ne 0) { throw "Package-lock download failed with exit code $LASTEXITCODE" }
& ssh.exe @sshOptions 'gb-admin@127.0.0.1' 'rm -f /home/gb-admin/gamebench-package-lock.txt /var/tmp/guest-install-unity.sh'
if ($LASTEXITCODE -ne 0) { throw "Guest temporary-file cleanup failed with exit code $LASTEXITCODE" }

$digest = (Get-FileHash -LiteralPath $packageLock -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Output "Unity 6000.3.23f1 installation complete; package lock sha256:$digest"
