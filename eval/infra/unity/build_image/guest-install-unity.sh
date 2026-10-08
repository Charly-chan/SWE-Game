#!/usr/bin/env bash
set -euo pipefail

readonly version='6000.3.23f1'
readonly editor_archive="${1:-/var/tmp/Unity-6000.3.23f1.tar.xz}"
readonly module_archive="${2:-/var/tmp/UnitySetup-Linux-IL2CPP-Support-for-Editor-6000.3.23f1.tar.xz}"
readonly editor_md5='063449fb3435edbfc53c28ae8f2edb56'
readonly module_md5='4ec0290a8d6096baaae31a1775693c3c'
readonly install_root="/opt/unity/${version}"

for archive in "$editor_archive" "$module_archive"; do
    if [[ ! -f "$archive" || -L "$archive" ]]; then
        echo "Required regular archive is missing: $archive" >&2
        exit 2
    fi
done

printf '%s  %s\n' "$editor_md5" "$editor_archive" "$module_md5" "$module_archive" |
    md5sum --check --strict -

install -d -m 0755 -o root -g root "$install_root"
tar --no-same-owner -xJf "$editor_archive" -C "$install_root"
tar --no-same-owner -xJf "$module_archive" -C "$install_root"
chown -R root:root "$install_root"
chmod -R a+rX "$install_root"
ln -sfn "$install_root" /opt/unity/current

observed_version="$(/opt/unity/current/Editor/Unity -version)"
if [[ "$observed_version" != "$version" ]]; then
    echo "Unity version mismatch: expected $version, got $observed_version" >&2
    exit 3
fi
test -f /opt/unity/current/Editor/Data/PlaybackEngines/LinuxStandaloneSupport/modules.asset

dpkg-query -W -f='${Package}\t${Version}\n' |
    LC_ALL=C sort >"$install_root/gamebench-package-lock.txt"
sha256sum "$install_root/gamebench-package-lock.txt"

rm -f -- "$editor_archive" "$module_archive"
printf 'Installed Unity %s with Linux IL2CPP support.\n' "$observed_version"
