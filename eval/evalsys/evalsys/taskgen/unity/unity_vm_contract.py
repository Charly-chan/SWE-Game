


from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from pathlib import PureWindowsPath
import sys
from typing import Any, Mapping


@dataclass(frozen=True)
class VMContractReport:
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": list(self.errors)}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _guest_path(value: object) -> str:
    text = str(value or "")
    if not text.startswith("/") or ".." in PurePosixPath(text).parts:
        return ""
    return text


def _host_path(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    if not (PureWindowsPath(text).is_absolute() or PurePosixPath(text).is_absolute()):
        return ""
    lowered = text.replace("/", "\\").lower()
    if lowered.startswith("\\\\wsl$\\") or "\\users\\" in lowered:
        return ""
    return text


def validate_vm_job_contract(value: Mapping[str, Any]) -> VMContractReport:


    errors: list[str] = []
    candidate = _mapping(value.get("candidate_input"))
    controller = _mapping(value.get("controller_input"))
    output = _mapping(value.get("output_disk"))
    users = _mapping(value.get("users"))
    isolation = _mapping(value.get("isolation"))
    lifecycle = _mapping(value.get("lifecycle"))

    if not value.get("environment_profile"):
        errors.append("environment_profile is required")
    for name, channel in (("candidate_input", candidate), ("controller_input", controller)):
        if not _host_path(channel.get("host_path")):
            errors.append(f"{name}.host_path must be absolute and outside user/WSL shares")
        if channel.get("read_only") is not True:
            errors.append(f"{name} must be read-only")
        if not _guest_path(channel.get("guest_mount")):
            errors.append(f"{name}.guest_mount must be an absolute normalized guest path")
    host_paths = [
        str(candidate.get("host_path") or "").lower(),
        str(controller.get("host_path") or "").lower(),
        str(output.get("host_path") or "").lower(),
    ]
    if all(host_paths) and len(set(host_paths)) != len(host_paths):
        errors.append("candidate, controller, and output disks must be separate artifacts")
    if not _guest_path(candidate.get("copy_to")):
        errors.append("candidate_input.copy_to must target VM-local ext4 scratch")
    if candidate.get("copy_to") and str(candidate.get("copy_to")).startswith("/mnt/"):
        errors.append("candidate scratch must not use /mnt host interoperability paths")
    if output.get("new_blank") is not True:
        errors.append("output disk must be newly created for this submission")
    if not _host_path(output.get("host_path")) or not _guest_path(output.get("guest_mount")):
        errors.append("output_disk requires safe absolute host_path and guest_mount")
    try:
        output_size_positive = int(output.get("size_mb") or 0) > 0
    except (TypeError, ValueError):
        output_size_positive = False
    if output.get("format") != "raw-ext4" or not output_size_positive:
        errors.append("output disk must declare a positive raw-ext4 capacity")
    guest_mounts = [
        str(candidate.get("guest_mount") or ""),
        str(controller.get("guest_mount") or ""),
        str(output.get("guest_mount") or ""),
    ]
    if all(guest_mounts) and len(set(guest_mounts)) != len(guest_mounts):
        errors.append("candidate, controller, and output guest mounts must be distinct")

    unity_user = str(users.get("unity_runner") or "")
    controller_user = str(users.get("gb_controller") or "")
    if not unity_user or not controller_user or unity_user == controller_user:
        errors.append("unity-runner and gb-controller must be distinct non-empty users")
    if users.get("distinct_uids") is not True:
        errors.append("runner users must have distinct UIDs")
    if isolation.get("host_mounts") not in ([], ()):
        errors.append("host mounts are forbidden")
    if isolation.get("candidate_network") != "none":
        errors.append("candidate network policy must be none")
    if isolation.get("controller_network") != "loopback-only":
        errors.append("controller network policy must be loopback-only")
    for key in ("mount_namespace", "hidepid", "ptrace_restricted", "cgroup_limits"):
        if isolation.get(key) is not True:
            errors.append(f"isolation.{key} must be true")
    if not isolation.get("apparmor_profile"):
        errors.append("isolation.apparmor_profile is required")
    if isolation.get("unity_api_key_present") is not False:
        errors.append("Unity process must not receive an API key")
    if isolation.get("license_state_network") != "none":
        errors.append("candidate-visible license state must have no network")
    if isolation.get("same_vm_editor_and_player") is not True:
        errors.append("Unity Editor and Player must run in the same disposable VM")
    limits = _mapping(isolation.get("limits"))
    for key in ("cpus", "memory_mb", "disk_mb", "processes", "wall_seconds"):
        try:
            positive = int(limits.get(key) or 0) > 0
        except (TypeError, ValueError):
            positive = False
        if not positive:
            errors.append(f"isolation.limits.{key} must be positive")
    if lifecycle.get("offline_artifact_read") is not True:
        errors.append("host must read artifacts only after guest shutdown")
    if lifecycle.get("destroy_clone") is not True or lifecycle.get("destroy_output_disk") is not True:
        errors.append("clone and output disk destruction must be explicit")
    if lifecycle.get("preserve_only_manifested_artifacts") is not True:
        errors.append("only manifested artifacts may survive teardown")
    return VMContractReport(tuple(dict.fromkeys(errors)))


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: unity_vm_contract.py JOB.json", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as stream:
        value = json.load(stream)
    report = validate_vm_job_contract(value)
    print(json.dumps(report.to_dict(), sort_keys=True))
    return 0 if report.ok else 3


__all__ = ["VMContractReport", "validate_vm_job_contract"]


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
