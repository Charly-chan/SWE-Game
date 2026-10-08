[CmdletBinding()]
param(
    [string]$InfrastructureRoot = 'D:\gamebench-unity-vm',
    [int]$SshPort = 2222
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$privateKey = Join-Path $InfrastructureRoot 'keys\gb-admin-ed25519'
$knownHosts = Join-Path $InfrastructureRoot 'runtime\known_hosts'
$exchange = Join-Path $InfrastructureRoot 'license-exchange'
$requestName = 'Unity_v6000.3.23f1.alf'
New-Item -ItemType Directory -Force -Path $exchange | Out-Null

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

$remote = @'
set -euo pipefail
install -d -m 0700 -o unity-runner -g unity-runner /var/lib/gamebench/license-request
sudo -u unity-runner bash -lc 'cd /var/lib/gamebench/license-request && timeout 180 /opt/unity/current/Editor/Unity -batchmode -nographics -createManualActivationFile -logFile activation.log'
install -m 0600 -o gb-admin -g gb-admin /var/lib/gamebench/license-request/Unity_v6000.3.23f1.alf /home/gb-admin/Unity_v6000.3.23f1.alf
'@
$encodedRemote = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remote))
& ssh.exe @sshOptions 'gb-admin@127.0.0.1' "printf '%s' '$encodedRemote' | base64 -d | sudo bash"
if ($LASTEXITCODE -ne 0) { throw "Unity license-request generation failed with exit code $LASTEXITCODE" }
& scp.exe @scpOptions "gb-admin@127.0.0.1:/home/gb-admin/$requestName" (Join-Path $exchange $requestName)
if ($LASTEXITCODE -ne 0) { throw "License-request download failed with exit code $LASTEXITCODE" }
& ssh.exe @sshOptions 'gb-admin@127.0.0.1' "rm -f /home/gb-admin/$requestName"

$request = Join-Path $exchange $requestName
$digest = (Get-FileHash -LiteralPath $request -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Output "Created $request (sha256:$digest)."
Write-Output 'Upload this request through the official Unity manual-activation portal; do not commit the returned .ulf file.'
