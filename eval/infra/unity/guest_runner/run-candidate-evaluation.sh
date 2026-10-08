#!/usr/bin/env bash
set -euo pipefail

readonly candidate_device="${1:?candidate device is required}"
readonly controller_device="${2:?controller device is required}"
readonly output_device="${3:?output device is required}"
readonly visual_judge="${4:-none}"
readonly diagnostic_smoke="${5:-0}"
readonly job_root='/var/lib/gamebench/candidate-evaluation'
readonly candidate_mount='/run/gamebench/candidate-input'
readonly controller_mount='/run/gamebench/controller-input'
readonly output_mount='/run/gamebench/output'
readonly candidate_root="$job_root/candidate"
readonly controller_root="$job_root/controller"
readonly runtime_root="$candidate_root/runtime"
readonly apparmor_profile='/etc/apparmor.d/gamebench-unity-candidate'

failure() {
  local rc=$?
  printf 'candidate guest runner failed at line %s (exit %s)\n' "${BASH_LINENO[0]:-unknown}" "$rc" >&2
  exit "$rc"
}
trap failure ERR

cleanup() {
  set +e
  trap - ERR
  jobs -p | xargs -r kill
  wait || true
  if mountpoint -q "$output_mount"; then umount "$output_mount" || true; fi
  if mountpoint -q "$controller_mount"; then umount "$controller_mount" || true; fi
  if mountpoint -q "$candidate_mount"; then umount "$candidate_mount" || true; fi
  true
}
trap cleanup EXIT

test "$(id -u)" -eq 0
test "$(id -u unity-runner)" != "$(id -u gb-controller)"
for tool in mount runuser aa-exec apparmor_parser mkfs.ext4 python3 tar sha256sum; do
  command -v "$tool" >/dev/null
done
for device in "$candidate_device" "$controller_device" "$output_device"; do test -b "$device"; done

rm -rf "$job_root"
install -d -m 0711 -o root -g root "$job_root" /run/gamebench
install -d -m 0700 -o unity-runner -g unity-runner "$candidate_root" "$runtime_root"
install -d -m 0700 -o gb-controller -g gb-controller "$controller_root"
install -d -m 0700 -o root -g root "$candidate_mount" "$controller_mount" "$output_mount"

mount -t iso9660 -o ro,nosuid,nodev,noexec "$candidate_device" "$candidate_mount"
cp "$candidate_mount/submission.tar.gz" "$candidate_root/submission.tar.gz"
chown unity-runner:unity-runner "$candidate_root/submission.tar.gz"
runuser -u unity-runner -- tar --no-same-owner --no-same-permissions \
  -xzf "$candidate_root/submission.tar.gz" -C "$candidate_root"
rm "$candidate_root/submission.tar.gz"
chown -R unity-runner:unity-runner "$candidate_root"
umount "$candidate_mount"

mount -t iso9660 -o ro,nosuid,nodev,noexec "$controller_device" "$controller_mount"
cp "$controller_mount/package.tar.gz" "$controller_root/package.tar.gz"
cp "$controller_mount/evaluator.tar.gz" "$controller_root/evaluator.tar.gz"
cp "$controller_mount/environment-profile.json" "$controller_root/environment-profile.json"
tar -xzf "$controller_root/package.tar.gz" -C "$controller_root"
tar -xzf "$controller_root/evaluator.tar.gz" -C "$controller_root"
rm "$controller_root/package.tar.gz" "$controller_root/evaluator.tar.gz"
chown -R gb-controller:gb-controller "$controller_root"
chmod -R go-rwx "$controller_root"
umount "$controller_mount"

mkfs.ext4 -q -F -L GAMEBENCH_OUTPUT "$output_device"
mount -t ext4 -o rw,nosuid,nodev,noexec "$output_device" "$output_mount"
install -d -m 0700 -o gb-controller -g gb-controller "$output_mount/evaluation"

cat >"$apparmor_profile" <<'APPARMOR'
#include <tunables/global>
profile gamebench-unity-candidate flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>
  # Unity Package Manager enumerates interfaces through netlink even in an
  # offline guest. QEMU supplies no NIC, so allowing socket families cannot
  # create external connectivity; the host preflight separately requires only lo.
  network,
  capability,
  /** rwmixlk,
  deny /var/lib/gamebench/candidate-evaluation/controller/** rwklmx,
  deny /run/gamebench/output/** rwklmx,
  deny /proc/*/mem rwkl,
}
APPARMOR
apparmor_parser -r "$apparmor_profile"
sysctl -q -w kernel.yama.ptrace_scope=3
mount -o remount,hidepid=2 /proc

readonly candidate_uid="$(id -u unity-runner)"
readonly candidate_gid="$(id -g unity-runner)"
readonly prefix='["runuser","-u","unity-runner","--","env","-i","HOME=/home/unity-runner","PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin","LANG=C.UTF-8","TZ=UTC","aa-exec","-p","gamebench-unity-candidate","--"]'

bench_exit=0
smoke_env=()
if test "$diagnostic_smoke" = 1; then smoke_env+=(GB_UNITY_DIAGNOSTIC_SMOKE=1); fi
env -i \
  HOME=/home/gb-controller PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C.UTF-8 TZ=UTC PYTHONPATH="$controller_root/evalsys" \
  UNITY_BIN=/opt/unity/current/Editor/Unity \
  GB_UNITY_ENVIRONMENT_PROFILE="$controller_root/environment-profile.json" \
  GB_UNITY_RUNTIME_ROOT="$runtime_root" \
  GB_UNITY_SKIP_LEGACY_ROUTES=1 \
  GB_UNITY_CANDIDATE_UID="$candidate_uid" GB_UNITY_CANDIDATE_GID="$candidate_gid" \
  GB_UNITY_CANDIDATE_PROCESS_PREFIX_JSON="$prefix" \
  "${smoke_env[@]}" \
  python3 "$controller_root/evalsys/bin/bench" eval-task \
    --package "$controller_root/package" \
    --submission "$candidate_root/submission" \
    --out "$output_mount/evaluation" --engine on --visual-judge "$visual_judge" \
    >"$output_mount/bench.stdout.log" 2>"$output_mount/bench.stderr.log" || bench_exit=$?

if ! test -s "$output_mount/evaluation/report.json"; then
  printf 'bench produced no report (exit %s)\n' "$bench_exit" >&2
  sed -n '1,240p' "$output_mount/bench.stdout.log" >&2 || true
  sed -n '1,240p' "$output_mount/bench.stderr.log" >&2 || true
  exit 70
fi
cp "$output_mount/evaluation/report.json" "$output_mount/report.json"
dmesg 2>/dev/null | grep -E 'apparmor="DENIED"|audit:.*DENIED' \
  >"$output_mount/kernel-audit.log" || true
tar -czf "$output_mount/evaluation-artifacts.tar.gz" -C "$output_mount" evaluation \
  kernel-audit.log -C "$candidate_root" runtime
python3 - "$output_mount" "$bench_exit" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
artifacts = []
for name in ("report.json", "evaluation-artifacts.tar.gz", "bench.stdout.log", "bench.stderr.log", "kernel-audit.log"):
    data = (root / name).read_bytes()
    artifacts.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
(root / "artifact-manifest.json").write_text(json.dumps({
    "schema": "gamebench.unity-candidate-artifacts.v1",
    "producer": "trusted-vm-controller",
    "bench_exit": int(sys.argv[2]),
    "artifacts": artifacts,
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
sync "$output_mount"
printf 'candidate_evaluation_complete=1\n'
printf 'bench_exit=%s\n' "$bench_exit"
# The host immediately powers off this disposable guest before reading the
# output disk. Leave the mounted output intact so its filesystem is not made
# busy by systemd's captured service descriptors during the EXIT trap.
trap - EXIT ERR
exit 0
