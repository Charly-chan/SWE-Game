[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$LicenseFile,
    [string]$InfrastructureRoot = 'D:\gamebench-unity-vm',
    [int]$SshPort = 2222
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$resolvedLicense = (Resolve-Path -LiteralPath $LicenseFile).Path
if ([IO.Path]::GetExtension($resolvedLicense) -ne '.ulf') {
    throw 'Unity license input must have the .ulf extension'
}
$privateKey = Join-Path $InfrastructureRoot 'keys\gb-admin-ed25519'
$knownHosts = Join-Path $InfrastructureRoot 'runtime\known_hosts'
foreach ($required in @($resolvedLicense, $privateKey, $knownHosts)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required file is missing: $required"
    }
}

$sshOptions = @(
    '-p', [string]$SshPort,
    '-i', $privateKey,
    '-o', 'BatchMode=yes',
    '-o', 'IdentitiesOnly=yes',
    '-o', 'StrictHostKeyChecking=yes',
    '-o', "UserKnownHostsFile=$knownHosts"
)
$scpOptions = @(
    '-P', [string]$SshPort,
    '-i', $privateKey,
    '-o', 'BatchMode=yes',
    '-o', 'IdentitiesOnly=yes',
    '-o', 'StrictHostKeyChecking=yes',
    '-o', "UserKnownHostsFile=$knownHosts"
)

& scp.exe @scpOptions $resolvedLicense 'gb-admin@127.0.0.1:/home/gb-admin/incoming-unity-license.ulf'
if ($LASTEXITCODE -ne 0) { throw "License upload failed with exit code $LASTEXITCODE" }

$remote = @'
set -euo pipefail
if findmnt -rn -o LABEL | grep -Fxq GAMEBENCH_CANDIDATE; then
    echo 'Refusing license import while a candidate disk is attached.' >&2
    exit 20
fi
install -d -m 0700 -o unity-runner -g unity-runner /var/lib/gamebench/license-request
install -m 0600 -o unity-runner -g unity-runner /home/gb-admin/incoming-unity-license.ulf /var/lib/gamebench/license-request/incoming.ulf
rm -f -- /home/gb-admin/incoming-unity-license.ulf
runuser -u unity-runner -- env HOME=/home/unity-runner /opt/unity/current/Editor/Unity -batchmode -nographics -manualLicenseFile /var/lib/gamebench/license-request/incoming.ulf -logFile /var/lib/gamebench/license-request/import.log
rm -f -- /var/lib/gamebench/license-request/incoming.ulf
'@
$encodedRemote = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remote))
try {
    & ssh.exe @sshOptions 'gb-admin@127.0.0.1' "printf '%s' '$encodedRemote' | base64 -d | sudo bash"
    if ($LASTEXITCODE -ne 0) { throw "License import failed with exit code $LASTEXITCODE" }
}
finally {
    & ssh.exe @sshOptions 'gb-admin@127.0.0.1' 'rm -f -- /home/gb-admin/incoming-unity-license.ulf; sudo rm -f -- /var/lib/gamebench/license-request/incoming.ulf' 2>$null
}

Write-Output 'License imported into the disposable clone.'
Write-Output 'Power it off, remove its NIC and license medium, then attach candidate input before execution.'
