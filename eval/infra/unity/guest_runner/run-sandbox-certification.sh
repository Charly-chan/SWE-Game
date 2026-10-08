#!/usr/bin/env bash
set -euo pipefail

readonly candidate_device="${1:?candidate device is required}"
readonly controller_device="${2:?controller device is required}"
readonly output_device="${3:?output device is required}"
readonly nonce="${4:?protocol nonce is required}"
readonly job_root='/var/lib/gamebench/sandbox-certification'
readonly candidate_mount='/run/gamebench/candidate-input'
readonly controller_mount='/run/gamebench/controller-input'
readonly output_mount='/run/gamebench/output'
readonly candidate_root="$job_root/candidate"
readonly candidate_scratch="$candidate_root/project"
readonly controller_private="$job_root/controller"
readonly apparmor_profile='/etc/apparmor.d/gamebench-unity-runner'
readonly candidate_cgroup='/sys/fs/cgroup/gamebench-unity-runner'

cleanup() {
    set +e
    jobs -p | xargs -r kill
    wait || true
    mountpoint -q "$output_mount" && umount "$output_mount"
    mountpoint -q "$controller_mount" && umount "$controller_mount"
    mountpoint -q "$candidate_mount" && umount "$candidate_mount"
}
trap cleanup EXIT

test "$(id -u)" -eq 0
test "$(id -u unity-runner)" != "$(id -u gb-controller)"
for tool in mount unshare runuser apparmor_parser aa-exec mkfs.ext4 python3 timeout; do
    command -v "$tool" >/dev/null
done
for device in "$candidate_device" "$controller_device" "$output_device"; do
    test -b "$device"
done

rm -rf "$job_root"
install -d -m 0711 -o root -g root "$job_root"
install -d -m 0711 -o root -g root /run/gamebench
install -d -m 0700 -o unity-runner -g unity-runner "$candidate_root" "$candidate_scratch"
install -d -m 0700 -o gb-controller -g gb-controller "$controller_private"
install -d -m 0700 -o root -g root "$candidate_mount" "$controller_mount" "$output_mount"

mount -t iso9660 -o ro,nosuid,nodev,noexec "$candidate_device" "$candidate_mount"
cp "$candidate_mount/probe.py" "$candidate_root/candidate_probe.py"
cp "$candidate_mount/candidate-workload.sh" "$candidate_root/candidate-workload.sh"
cp "$candidate_mount/blank-fixture.tar.gz" "$candidate_root/blank-fixture.tar.gz"
cp "$candidate_mount/protocol-fixture.tar.gz" "$candidate_root/protocol-fixture.tar.gz"
cp "$candidate_mount/behavior-causality-fixture.tar.gz" \
   "$candidate_root/behavior-causality-fixture.tar.gz"
cp "$candidate_mount/target-scaffold.tar.gz" "$candidate_root/target-scaffold.tar.gz"
cp "$candidate_mount/observer-fixture.tar.gz" "$candidate_root/observer-fixture.tar.gz"
cp "$candidate_mount/unity-runtime-observer.cs" "$candidate_root/unity-runtime-observer.cs"
chown -R unity-runner:unity-runner "$candidate_root"
chmod 0500 "$candidate_root/candidate_probe.py" "$candidate_root/candidate-workload.sh"
chmod 0400 "$candidate_root/blank-fixture.tar.gz" "$candidate_root/protocol-fixture.tar.gz" \
    "$candidate_root/behavior-causality-fixture.tar.gz" "$candidate_root/target-scaffold.tar.gz" \
    "$candidate_root/observer-fixture.tar.gz" \
    "$candidate_root/unity-runtime-observer.cs"
umount "$candidate_mount"

mount -t iso9660 -o ro,nosuid,nodev,noexec "$controller_device" "$controller_mount"
cp "$controller_mount/controller.py" "$controller_private/controller.py"
cp "$controller_mount/unity_controller.py" "$controller_private/unity_controller.py"
cp "$controller_mount/hidden-policy.txt" "$controller_private/hidden-policy.txt"
cp "$controller_mount/evalsys.tar.gz" "$controller_private/evalsys.tar.gz"
install -d -m 0700 -o gb-controller -g gb-controller "$job_root/interface"
cp "$controller_mount/interface-contract.v2.json" "$job_root/interface/contract.v2.json"
tar -xzf "$controller_private/evalsys.tar.gz" -C "$controller_private"
chown -R gb-controller:gb-controller "$controller_private"
chown -R gb-controller:gb-controller "$job_root/interface"
chmod 0500 "$controller_private/controller.py"
chmod 0400 "$controller_private/unity_controller.py"
chmod 0400 "$controller_private/hidden-policy.txt"

mkfs.ext4 -q -F -L GAMEBENCH_OUTPUT "$output_device"
mount -t ext4 -o rw,nosuid,nodev,noexec "$output_device" "$output_mount"
chown gb-controller:gb-controller "$output_mount"
chmod 0700 "$output_mount"

cat >"$apparmor_profile" <<'APPARMOR'
#include <tunables/global>
profile gamebench-unity-runner flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>
  network,
  capability,
  /** rwmixlk,
  deny /run/gamebench/controller-input/** rwklmx,
  deny /run/gamebench/output/** rwklmx,
  deny /var/lib/gamebench/sandbox-certification/controller/** rwklmx,
  deny /proc/*/mem rwkl,
}
APPARMOR
apparmor_parser -r "$apparmor_profile"

sysctl -q -w kernel.yama.ptrace_scope=3
mount -o remount,hidepid=2 /proc

mkdir "$candidate_cgroup"
printf '800000 100000\n' >"$candidate_cgroup/cpu.max"
printf '%s\n' "$((12 * 1024 * 1024 * 1024))" >"$candidate_cgroup/memory.max"
printf '512\n' >"$candidate_cgroup/pids.max"

# Prove the kernel enforces memory, process and wall limits in disposable test
# cgroups. These use deliberately small caps and never contain Unity itself.
resource_memory='/sys/fs/cgroup/gamebench-resource-memory-negative'
mkdir "$resource_memory"
printf '%s\n' "$((32 * 1024 * 1024))" >"$resource_memory/memory.max"
set +e
bash -c "echo \$\$ > '$resource_memory/cgroup.procs'; exec runuser -u unity-runner -- python3 -c 'bytearray(256 * 1024 * 1024)'" >/dev/null 2>&1
memory_exit=$?
set -e
test "$memory_exit" -ne 0
rmdir "$resource_memory"

resource_pids='/sys/fs/cgroup/gamebench-resource-pids-negative'
mkdir "$resource_pids"
printf '8\n' >"$resource_pids/pids.max"
set +e
bash -c "echo \$\$ > '$resource_pids/cgroup.procs'; exec runuser -u unity-runner -- python3 -c 'import subprocess, time; p=[]; [p.append(subprocess.Popen([\"sleep\", \"2\"])) for _ in range(64)]; time.sleep(3)'" >/dev/null 2>&1
pids_exit=$?
set -e
test "$pids_exit" -ne 0
sleep 3
rmdir "$resource_pids"

set +e
timeout 1 runuser -u unity-runner -- sleep 5
wall_exit=$?
set -e
test "$wall_exit" -eq 124

ready_file="$controller_private/ready.json"
runuser -u gb-controller -- env HOME=/home/gb-controller \
    python3 -u "$controller_private/controller.py" \
    --hidden "$controller_private/hidden-policy.txt" \
    --ready "$ready_file" \
    --output "$output_mount" \
    --nonce "$nonce" &
controller_wrapper_pid=$!

for _ in $(seq 1 100); do
    test -s "$ready_file" && break
    sleep 0.1
done
test -s "$ready_file"
controller_pid="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pid"])' "$ready_file")"
controller_port="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["port"])' "$ready_file")"
protocol_controller_port="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["protocol_port"])' "$ready_file")"
observer_controller_port="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["observer_port"])' "$ready_file")"
cat_controller_ports="$(python3 -c 'import json,sys; print(",".join(str(value) for value in json.load(open(sys.argv[1]))["cat_ports"]))' "$ready_file")"

candidate_command=$(cat <<EOF
mount --make-rprivate /
umount -l '$controller_mount'
umount -l '$output_mount'
mount -t proc -o hidepid=2 proc /proc
echo \$\$ > '$candidate_cgroup/cgroup.procs'
exec runuser -u unity-runner -- env -i \
  HOME=/home/unity-runner \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C.UTF-8 TZ=UTC \
  GB_CONTROLLER_PID='$controller_pid' \
  GB_CONTROLLER_PORT='$controller_port' \
  GB_PROTOCOL_CONTROLLER_PORT='$protocol_controller_port' \
  GB_OBSERVER_CONTROLLER_PORT='$observer_controller_port' \
  GB_CAT_CONTROLLER_PORTS='$cat_controller_ports' \
  GB_PROTOCOL_NONCE='$nonce' \
  aa-exec -p gamebench-unity-runner -- bash '$candidate_root/candidate-workload.sh'
EOF
)
set +e
unshare --mount --pid --fork bash -euo pipefail -c "$candidate_command"
candidate_exit=$?
if test "$candidate_exit" -ne 0; then
    for _ in $(seq 1 20); do
        kill -0 "$controller_wrapper_pid" 2>/dev/null || break
        sleep 0.1
    done
    kill "$controller_wrapper_pid" 2>/dev/null || true
fi
wait "$controller_wrapper_pid"
controller_exit=$?
set -e
printf 'candidate_exit=%s\n' "$candidate_exit"
printf 'controller_exit=%s\n' "$controller_exit"
test "$candidate_exit" -eq 0
test "$controller_exit" -eq 0

test -s "$output_mount/report.json"
test -s "$output_mount/artifact-manifest.json"
runuser -u gb-controller -- python3 - "$output_mount/report.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1], encoding='utf-8'))
assert report['schema'] == 'gamebench.unity-m5-005-sandbox-report.v1'
assert report['status'] == 'passed', report
assert report['hidden_value_disclosed'] is False, report
PY

sync "$output_mount"
printf 'sandbox_status=passed\n'
printf 'network_devices='; find /sys/class/net -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort | paste -sd, -
printf 'unity_runner_uid=%s\n' "$(id -u unity-runner)"
printf 'controller_uid=%s\n' "$(id -u gb-controller)"
printf 'ptrace_scope=%s\n' "$(cat /proc/sys/kernel/yama/ptrace_scope)"
printf 'candidate_cpu_max=%s\n' "$(tr ' ' '/' <"$candidate_cgroup/cpu.max")"
printf 'candidate_memory_max=%s\n' "$(cat "$candidate_cgroup/memory.max")"
printf 'candidate_pids_max=%s\n' "$(cat "$candidate_cgroup/pids.max")"
printf 'memory_negative_exit=%s\n' "$memory_exit"
printf 'pids_negative_exit=%s\n' "$pids_exit"
printf 'wall_negative_exit=%s\n' "$wall_exit"
