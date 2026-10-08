#!/usr/bin/env python3


from __future__ import annotations

import json
import base64
import hashlib
import os
from pathlib import Path
import socket
import struct
import sys


def _denied(path: Path, *, list_directory: bool = False) -> bool:
    try:
        if list_directory:
            list(path.iterdir())
        else:
            path.read_bytes()
    except (FileNotFoundError, PermissionError, NotADirectoryError, OSError):
        return True
    return False


def _mount_sources() -> list[str]:
    return Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()


def _unity_evidence(root: Path) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    paths = {
        "build-result.json": root / "build/build-result.json",
        "runtime-result.json": root / "runtime/runtime-result.json",
        "blank-fixture.png": root / "runtime/blank-fixture.png",
        "editor.log": root / "logs/editor.log",
        "player.log": root / "logs/player.log",
        "xvfb.log": root / "logs/xvfb.log",
        "protocol-build-result.json": root / "protocol-build/build-result.json",
        "protocol-editor.log": root / "protocol-logs/editor.log",
        "protocol-player.log": root / "protocol-logs/player.log",
        "cat-build-result.json": root / "cat-build/build-result.json",
        "cat-editor.log": root / "cat-logs/editor.log",
        "target-editor.log": root / "target-logs/editor.log",
        "target-packages-lock.json": root / "target-project/Packages/packages-lock.json",
        "observer-build-result.json": root / "observer-build/build-result.json",
        "observer-editor.log": root / "observer-logs/editor.log",
        "observer-player.log": root / "observer-logs/player.log",
    }
    for session in (
        "positive_01", "positive_02", "positive_03", "positive_04", "positive_05",
        "no_fire_control", "auto_win", "input_ignored", "enemy_auto_disappears",
        "telemetry_fake", "witness_candidate", "witness_hidden", "visual_only",
    ):
        paths[f"cat-{session}-player.log"] = root / f"cat-logs/{session}.log"
    blobs = {name: path.read_bytes() for name, path in paths.items()}
    build = json.loads(blobs["build-result.json"].decode("utf-8"))
    runtime = json.loads(blobs["runtime-result.json"].decode("utf-8"))
    png = blobs["blank-fixture.png"]
    dimensions = struct.unpack(">II", png[16:24]) if png[:8] == b"\x89PNG\r\n\x1a\n" else (0, 0)
    if build.get("result") != "Succeeded" or int(build.get("errors", -1)) != 0:
        raise RuntimeError(f"Unity build fixture failed: {build}")
    expected_runtime = {
        "schema": "gamebench.unity-blank-fixture-runtime.v1",
        "status": "passed",
        "width": 960,
        "height": 540,
        "unity_version": "6000.3.23f1",
    }
    if runtime != expected_runtime or dimensions != (960, 540):
        raise RuntimeError(f"Unity runtime fixture failed: {runtime}, dimensions={dimensions}")
    protocol_build = json.loads(blobs["protocol-build-result.json"].decode("utf-8"))
    if protocol_build.get("result") != "Succeeded" or int(protocol_build.get("errors", -1)) != 0:
        raise RuntimeError(f"Unity protocol fixture build failed: {protocol_build}")
    cat_build = json.loads(blobs["cat-build-result.json"].decode("utf-8"))
    if cat_build.get("result") != "Succeeded" or int(cat_build.get("errors", -1)) != 0:
        raise RuntimeError(f"Unity cat-defense fixture build failed: {cat_build}")
    observer_build = json.loads(blobs["observer-build-result.json"].decode("utf-8"))
    if observer_build.get("result") != "Succeeded" or int(observer_build.get("errors", -1)) != 0:
        raise RuntimeError(f"Unity production observer fixture build failed: {observer_build}")
    target_log = blobs["target-editor.log"].decode("utf-8", errors="replace").lower()
    target_failures = (
        "error cs", "compilation failed", "an error occurred while resolving packages",
        "unable to resolve package", "no valid unity editor license",
    )
    if any(pattern in target_log for pattern in target_failures):
        raise RuntimeError("Unity target scaffold import/compile failed")
    artifacts = {
        name: {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "base64": base64.b64encode(data).decode("ascii"),
        }
        for name, data in blobs.items()
    }
    return (
        {
            "build_result": "Succeeded",
            "build_errors": 0,
            "runtime_status": "passed",
            "unity_version": "6000.3.23f1",
            "png_dimensions": "960x540",
            "png_sha256": artifacts["blank-fixture.png"]["sha256"],
            "protocol_build_result": "Succeeded",
            "protocol_build_errors": 0,
            "cat_build_result": "Succeeded",
            "cat_build_errors": 0,
            "target_scaffold_import": "Succeeded",
            "observer_build_result": "Succeeded",
            "observer_build_errors": 0,
        },
        artifacts,
    )


def main() -> int:
    controller_pid = int(os.environ["GB_CONTROLLER_PID"])
    controller_port = int(os.environ["GB_CONTROLLER_PORT"])
    nonce = os.environ["GB_PROTOCOL_NONCE"]
    unity_summary, artifacts = _unity_evidence(Path(os.environ["GB_UNITY_FIXTURE_ROOT"]))

    interfaces = sorted(path.name for path in Path("/sys/class/net").iterdir())
    mounts = _mount_sources()
    forbidden_mount_fragments = (
        "/run/gamebench/controller-input",
        "/run/gamebench/output",
        "/mnt/c",
        "/mnt/d",
        "drvfs",
        "9p",
        "virtiofs",
    )

    process_mem_denied = _denied(Path(f"/proc/{controller_pid}/mem"))
    controller_process_hidden = not Path(f"/proc/{controller_pid}").exists()

    with socket.create_connection(("127.0.0.1", controller_port), timeout=5.0) as client:


        client.settimeout(60.0)
        stream = client.makefile("rwb", buffering=0)
        hello = json.loads(stream.readline().decode("utf-8"))
        loopback_ok = hello == {
            "schema": "gamebench.sandbox-protocol.v1",
            "nonce": nonce,
            "message": "ready",
        }

        external_network_denied = False
        external_error = ""
        external = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        external.settimeout(1.0)
        try:
            external.connect(("1.1.1.1", 443))
        except OSError as exc:
            external_network_denied = True
            external_error = type(exc).__name__
        finally:
            external.close()

        result = {
            "schema": "gamebench.unity-sandbox-candidate-probe.v1",
            "uid": os.getuid(),
            "gid": os.getgid(),
            "interfaces": interfaces,
            "network_loopback_only": interfaces == ["lo"] and external_network_denied,
            "external_network_error": external_error,
            "loopback_controller_reachable": loopback_ok,
            "controller_mount_hidden": not any(
                "/run/gamebench/controller-input" in line for line in mounts
            ),
            "output_mount_hidden": not any(
                "/run/gamebench/output" in line for line in mounts
            ),
            "host_mounts_absent": not any(
                fragment in line
                for line in mounts
                for fragment in forbidden_mount_fragments[2:]
            ),
            "controller_private_denied": _denied(
                Path("/var/lib/gamebench/controller"), list_directory=True
            ),
            "final_report_denied": _denied(Path("/run/gamebench/output/report.json")),
            "controller_process_hidden": controller_process_hidden,
            "controller_process_memory_denied": process_mem_denied,
            "home": os.environ.get("HOME"),
            "unity_api_key_absent": not bool(os.environ.get("UNITY_API_KEY")),
            "candidate_input_unmounted": not any(
                "/run/gamebench/candidate-input" in line for line in mounts
            ),
            "unity_fixture": unity_summary,
            "_artifacts": artifacts,
        }
        stream.write((json.dumps(result, sort_keys=True) + "\n").encode("utf-8"))
        acknowledgement = json.loads(stream.readline().decode("utf-8"))
        if acknowledgement != {"accepted": True}:
            raise RuntimeError(f"unexpected controller acknowledgement: {acknowledgement}")

    required_true = (
        "network_loopback_only",
        "loopback_controller_reachable",
        "controller_mount_hidden",
        "output_mount_hidden",
        "host_mounts_absent",
        "controller_private_denied",
        "final_report_denied",
        "controller_process_hidden",
        "controller_process_memory_denied",
        "unity_api_key_absent",
        "candidate_input_unmounted",
    )
    failed = [name for name in required_true if result.get(name) is not True]
    public_result = {key: value for key, value in result.items() if key != "_artifacts"}
    print(json.dumps(public_result, sort_keys=True))
    if failed:
        print("failed probes: " + ", ".join(failed), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
