#!/usr/bin/env python3


from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import time

from unity_controller import LoopbackControllerServer, UnityControllerSession


CAT_RUNS = (
    ("positive_01", "positive", "gb_action", 3, True),
    ("positive_02", "positive", "gb_action", 3, False),
    ("positive_03", "positive", "gb_action", 3, False),
    ("positive_04", "positive", "gb_action", 3, False),
    ("positive_05", "positive", "gb_action", 3, False),
    ("no_fire_control", "positive", "gb_left", 3, False),
    ("auto_win", "auto_win", "", 0, False),
    ("input_ignored", "input_ignored", "gb_action", 3, False),
    ("enemy_auto_disappears", "enemy_auto_disappears", "gb_left", 3, False),
    ("telemetry_fake", "telemetry_fake", "gb_action", 3, False),
    ("witness_candidate", "witness_only", "gb_action", 3, False),
    ("witness_hidden", "witness_only", "gb_action", 4, False),
    ("visual_only", "visual_only", "gb_action", 3, True),
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validated_unity_artifacts(candidate: dict[str, object]) -> tuple[dict[str, bytes], dict[str, object]]:
    expected_names = {
        "build-result.json",
        "runtime-result.json",
        "blank-fixture.png",
        "editor.log",
        "player.log",
        "xvfb.log",
        "protocol-build-result.json",
        "protocol-editor.log",
        "protocol-player.log",
        "cat-build-result.json",
        "cat-editor.log",
        "target-editor.log",
        "target-packages-lock.json",
        "observer-build-result.json",
        "observer-editor.log",
        "observer-player.log",
    }
    expected_names.update(
        f"cat-{session_id}-player.log" for session_id, _variant, _action, _hold, _capture in CAT_RUNS
    )
    raw = candidate.pop("_artifacts", None)
    if not isinstance(raw, dict) or set(raw) != expected_names:
        raise RuntimeError("candidate did not provide the exact fixture artifact set")
    artifacts: dict[str, bytes] = {}
    for name in sorted(expected_names):
        item = raw[name]
        if not isinstance(item, dict):
            raise RuntimeError(f"invalid artifact envelope: {name}")
        data = base64.b64decode(str(item.get("base64") or ""), validate=True)
        digest = hashlib.sha256(data).hexdigest()
        if digest != item.get("sha256") or len(data) != item.get("bytes"):
            raise RuntimeError(f"artifact envelope mismatch: {name}")
        artifacts[name] = data

    build = json.loads(artifacts["build-result.json"].decode("utf-8"))
    runtime = json.loads(artifacts["runtime-result.json"].decode("utf-8"))
    png = artifacts["blank-fixture.png"]
    dimensions = struct.unpack(">II", png[16:24]) if png[:8] == b"\x89PNG\r\n\x1a\n" else (0, 0)
    license_errors = (
        b"No valid Unity Editor license",
        b"Failed to activate",
        b"License is not active",
    )
    if build.get("result") != "Succeeded" or int(build.get("errors", -1)) != 0:
        raise RuntimeError(f"controller rejected build result: {build}")
    if runtime != {
        "schema": "gamebench.unity-blank-fixture-runtime.v1",
        "status": "passed",
        "width": 960,
        "height": 540,
        "unity_version": "6000.3.23f1",
    }:
        raise RuntimeError(f"controller rejected runtime result: {runtime}")
    if dimensions != (960, 540):
        raise RuntimeError(f"controller rejected PNG dimensions: {dimensions}")
    if any(pattern.lower() in artifacts["editor.log"].lower() for pattern in license_errors):
        raise RuntimeError("controller found a Unity license error")
    renderer = ""
    for line in artifacts["player.log"].decode("utf-8", errors="replace").splitlines():
        if line.startswith("Renderer:"):
            renderer = line.split(":", 1)[1].strip()
            break
    if "llvmpipe" not in renderer.lower():
        raise RuntimeError(f"controller rejected renderer: {renderer!r}")
    protocol_build = json.loads(artifacts["protocol-build-result.json"].decode("utf-8"))
    if protocol_build.get("result") != "Succeeded" or int(protocol_build.get("errors", -1)) != 0:
        raise RuntimeError(f"controller rejected protocol build result: {protocol_build}")
    cat_build = json.loads(artifacts["cat-build-result.json"].decode("utf-8"))
    if cat_build.get("result") != "Succeeded" or int(cat_build.get("errors", -1)) != 0:
        raise RuntimeError(f"controller rejected behavior-causality build result: {cat_build}")
    if any(pattern.lower() in artifacts["cat-editor.log"].lower() for pattern in license_errors):
        raise RuntimeError("controller found a Unity license error in behavior-causality build")
    observer_build = json.loads(artifacts["observer-build-result.json"].decode("utf-8"))
    if observer_build.get("result") != "Succeeded" or int(observer_build.get("errors", -1)) != 0:
        raise RuntimeError(f"controller rejected observer build result: {observer_build}")
    observer_log = artifacts["observer-editor.log"].lower()
    if b"error cs" in observer_log or b"compilation failed" in observer_log:
        raise RuntimeError("controller found a compiler error in the production observer fixture")
    target_log = artifacts["target-editor.log"].lower()
    target_failures = (
        b"error cs", b"compilation failed", b"an error occurred while resolving packages",
        b"unable to resolve package",
    )
    if any(pattern in target_log for pattern in target_failures):
        raise RuntimeError("controller rejected target scaffold import/compile log")
    return artifacts, {
        "build_result": "Succeeded",
        "build_errors": 0,
        "runtime_status": "passed",
        "unity_version": "6000.3.23f1",
        "png_dimensions": "960x540",
        "png_sha256": hashlib.sha256(png).hexdigest(),
        "renderer": renderer,
        "license_error_count": 0,
        "target_scaffold_import": "Succeeded",
        "observer_build_result": "Succeeded",
        "observer_build_errors": 0,
    }


def _run_protocol_fixture(
    server: LoopbackControllerServer, nonce: str
) -> tuple[dict[str, bytes], dict[str, object]]:
    channel = server.accept()
    session = UnityControllerSession(
        channel,
        run_id="m5-110-protocol",
        nonce=nonce,
        build_digest="trusted-protocol-fixture",
    )
    hello = session.handshake()
    ready = session.receive_ready()
    action_sequence = session.send_action("gb_right", 1.0, 3)
    action_ack = session.receive_ack("action_ack", action_sequence)
    observation = session.receive_observation()
    row = observation["row"]
    if float(row.get("px", 0.0)) != 3.0 or row.get("g") != {"gb_player": 1}:
        raise RuntimeError(f"protocol semantic row did not reflect gb_right: {row}")
    capture_sequence = session.send_capture("after_gb_right")
    capture_ack = session.receive_ack("capture_ack", capture_sequence)
    png = base64.b64decode(str(capture_ack.get("png_base64") or ""), validate=True)
    dimensions = struct.unpack(">II", png[16:24]) if png[:8] == b"\x89PNG\r\n\x1a\n" else (0, 0)
    digest = hashlib.sha256(png).hexdigest()
    if dimensions != (960, 540) or capture_ack.get("bytes_digest") != "sha256:" + digest:
        raise RuntimeError("protocol capture acknowledgement failed validation")
    session.stop("goal_reached", wait_for_ack=True)

    report = {
        "schema": "gamebench.unity-semantic-report.v1",
        "run_id": "m5-110-protocol/positive",
        "rows": session.rows,
        "group_totals": {"gb_player": 1},
        "bounds": {
            "min": {"x": 0.0, "y": 0.0, "z": 0.0},
            "max": {"x": 10.0, "y": 1.0, "z": 1.0},
        },
        "device_contacts": {},
        "device_index": {},
        "stop_reason": "goal_reached",
        "steps": 1,
        "deaths": 0,
        "stall_frames": 0,
        "turns": 1,
        "injects": 0,
        "out_of_bounds": 0,
        "physics_frames_at_end": int(row["f"]),
        "process_frames_at_end": int(row["f"]),
        "success_before_all_levels": False,
        "errors": [],
        "detail": "",
    }
    summary = {
        "protocol": hello["protocol"],
        "ready_scene": ready["scene"],
        "action_sequence": action_sequence,
        "action_start_frame": action_ack["start_frame"],
        "action_end_frame": action_ack["end_frame"],
        "observation_sequence": observation["sequence"],
        "observed_px": row["px"],
        "capture_sequence": capture_sequence,
        "capture_frame": capture_ack["frame"],
        "capture_sha256": digest,
        "message_flow": [
            "hello",
            "ready",
            "action",
            "action_ack",
            "observation",
            "capture",
            "capture_ack",
            "stop",
            "stop_ack",
        ],
    }
    return {
        "protocol-capture.png": png,
        "semantic-report.json": (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        "protocol-summary.json": (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    }, summary


def _run_observer_fixture(
    server: LoopbackControllerServer, nonce: str
) -> tuple[dict[str, bytes], dict[str, object]]:
    session = UnityControllerSession(
        server.accept(),
        run_id="m5-104-observer",
        nonce=nonce,
        build_digest="trusted-observer-fixture",
    )
    session.handshake()
    ready = session.receive_ready()
    initial = session.receive_observation()["row"]
    sequence = session.send_action("gb_action", 1.0, 30)
    acknowledgement = session.receive_ack("action_ack", sequence)
    session.receive_through_frame(int(acknowledgement["end_frame"]))
    for _ in range(120):
        if session.rows and session.rows[-1].get("wgc") is True:
            break
        session.receive_observation()
    final = session.rows[-1]
    capture_sequence = session.send_capture("enemy_removed")
    capture = session.receive_ack("capture_ack", capture_sequence)
    png = base64.b64decode(str(capture.get("png_base64") or ""), validate=True)
    dimensions = struct.unpack(">II", png[16:24]) if png[:8] == b"\x89PNG\r\n\x1a\n" else (0, 0)
    digest = hashlib.sha256(png).hexdigest()
    session.stop("goal_reached", wait_for_ack=True)

    event_kinds = [str(item.get("kind") or "") for item in session.events]
    checkpoints = [
        str(item.get("value") or "") for item in session.events
        if item.get("kind") == "checkpoint"
    ]
    overlap_observed = any(
        bool((row.get("o") or {}).get("gb_enemy|gb_projectile"))
        or bool((row.get("o") or {}).get("gb_projectile|gb_enemy"))
        for row in session.rows
    )
    bounds_event = next(
        (item for item in session.events if item.get("kind") == "bounds_frozen"), None
    )
    input_events = [item for item in session.events if item.get("kind") == "input_dispatched"]
    row_frames = [int(row["f"]) for row in session.rows]
    checks = {
        "ready_scene": ready.get("scene"),
        "initial_player_count": (initial.get("g") or {}).get("gb_player"),
        "initial_enemy_count": (initial.get("g") or {}).get("gb_enemy"),
        "final_enemy_count": (final.get("g") or {}).get("gb_enemy"),
        "initial_score": (initial.get("n") or {}).get("score"),
        "singleton_goal_observable": "goal#0" in (initial.get("d") or {}),
        "distant_goal_not_contacted": "goal#0" not in (initial.get("c") or []),
        "goal_contact_after_movement": any("goal#0" in (row.get("c") or []) for row in session.rows),
        "final_score": (final.get("n") or {}).get("score"),
        "input_performed": (final.get("n") or {}).get("input_performed"),
        "projectile_enemy_overlap": overlap_observed,
        "whole_game_clear": final.get("wgc"),
        "checkpoints": checkpoints,
        "outcome_success": "outcome_success" in event_kinds,
        "bounds_frozen": bool(bounds_event and bounds_event.get("observable")),
        "row_count": len(session.rows),
        "row_frames_strictly_increasing": all(
            current > previous for previous, current in zip(row_frames, row_frames[1:])
        ),
        "capture_dimensions": f"{dimensions[0]}x{dimensions[1]}",
        "capture_sha256": digest,
        "input_dispatch_events": input_events,
    }
    if not (
        checks["initial_player_count"] == 1
        and checks["singleton_goal_observable"] is True
        and checks["distant_goal_not_contacted"] is True
        and checks["goal_contact_after_movement"] is True
        and checks["initial_enemy_count"] == 1
        and checks["final_enemy_count"] == 0
        and checks["initial_score"] == 0
        and checks["final_score"] == 100
        and checks["input_performed"] == 1
        and checks["projectile_enemy_overlap"] is True
        and checks["whole_game_clear"] is True
        and {"spawn", "projectile_contact", "enemy_removed"} <= set(checkpoints)
        and checks["outcome_success"] is True
        and checks["bounds_frozen"] is True
        and checks["row_frames_strictly_increasing"] is True
        and dimensions == (960, 540)
        and capture.get("bytes_digest") == "sha256:" + digest
    ):
        raise RuntimeError(f"production observer fixture failed: {checks}")

    group_totals: dict[str, int] = {}
    for row in session.rows:
        for role, count in (row.get("g") or {}).items():
            group_totals[str(role)] = max(group_totals.get(str(role), 0), int(count))
    device_index = {}
    device_contacts = {}
    for row in session.rows:
        device_index.update(row.get("d") or {})
        for device_id in row.get("c") or []:
            device_contacts[device_id] = device_contacts.get(device_id, 0) + 1
    report = {
        "schema": "gamebench.unity-semantic-report.v1",
        "run_id": "m5-104-observer/positive",
        "rows": session.rows,
        "group_totals": group_totals,
        "bounds": {"min": bounds_event["min"], "max": bounds_event["max"]},
        "device_contacts": device_contacts, "device_index": device_index,
        "stop_reason": "goal_reached", "steps": 1, "deaths": 0,
        "stall_frames": 0, "turns": 1, "injects": 0, "out_of_bounds": 0,
        "physics_frames_at_end": int(final["f"]),
        "process_frames_at_end": int(final["f"]),
        "success_before_all_levels": False, "errors": [],
        "detail": "production injected observer over public SDK fixture",
    }
    return {
        "observer-capture.png": png,
        "observer-semantic-report.json": (json.dumps(report, indent=2, sort_keys=True) + "\n").encode(),
        "observer-summary.json": (json.dumps(checks, indent=2, sort_keys=True) + "\n").encode(),
    }, checks


def _role_pair_overlap(rows: list[dict[str, object]], left: str, right: str) -> bool:

    key = "|".join(sorted((left, right)))
    return any(bool((row.get("o") or {}).get(key)) for row in rows)


def _semantic_report(
    run_id: str,
    rows: list[dict[str, object]],
    events: list[dict[str, object]],
    steps: int,
) -> dict[str, object]:
    last_frame = int(rows[-1]["f"])
    totals: dict[str, int] = {}
    devices: dict[str, object] = {}
    contacts: dict[str, int] = {}
    for row in rows:
        for role, count in (row.get("g") or {}).items():
            totals[str(role)] = max(totals.get(str(role), 0), int(count))
        devices.update(row.get("d") or {})
        for device_id in row.get("c") or []:
            contacts[str(device_id)] = contacts.get(str(device_id), 0) + 1
    bounds = next((event for event in events if event.get("kind") == "bounds_frozen"), None)
    if not bounds:
        raise RuntimeError("behavior-causality observer did not freeze bounds")
    return {
        "schema": "gamebench.unity-semantic-report.v1",
        "run_id": run_id,
        "rows": rows,
        "group_totals": totals,
        "bounds": {"min": bounds["min"], "max": bounds["max"]},
        "device_contacts": contacts,
        "device_index": devices,
        "stop_reason": "goal_reached" if rows[-1]["wgc"] else "budget_exhausted",
        "steps": steps,
        "deaths": 0,
        "stall_frames": 0,
        "turns": 1,
        "injects": 0,
        "out_of_bounds": 0,
        "physics_frames_at_end": last_frame,
        "process_frames_at_end": last_frame,
        "success_before_all_levels": False,
        "errors": [],
        "detail": "production observer over physical behavior-causality gameplay",
        "events": events,
    }


def _run_cat_session(
    server: LoopbackControllerServer,
    nonce: str,
    session_id: str,
    action: str,
    hold_frames: int,
    capture: bool,
) -> tuple[dict[str, bytes], dict[str, object]]:
    session = UnityControllerSession(
        server.accept(),
        run_id="m5-004-cat-" + session_id,
        nonce=nonce,
        build_digest="trusted-cat-defense-fixture",
    )
    session.handshake()
    session.receive_ready()
    initial = session.receive_observation()["row"]
    steps = 0
    if action:
        sequence = session.send_action(action, 1.0, hold_frames)
        acknowledgement = session.receive_ack("action_ack", sequence)
        session.receive_through_frame(int(acknowledgement["end_frame"]))
        steps = 1
    captured: set[str] = set()
    checkpoint_captures: list[dict[str, object]] = []
    artifacts: dict[str, bytes] = {}
    for _ in range(80):
        if capture:
            checkpoint_ids = [
                str(event.get("value") or "")
                for event in session.events
                if event.get("kind") == "checkpoint"
            ]
            for checkpoint_id in checkpoint_ids:
                if not checkpoint_id or checkpoint_id in captured:
                    continue
                sequence = session.send_capture(checkpoint_id)
                acknowledgement = session.receive_ack("capture_ack", sequence)
                png = base64.b64decode(str(acknowledgement.get("png_base64") or ""), validate=True)
                dimensions = struct.unpack(">II", png[16:24]) if png[:8] == b"\x89PNG\r\n\x1a\n" else (0, 0)
                digest = hashlib.sha256(png).hexdigest()
                if dimensions != (960, 540) or acknowledgement.get("bytes_digest") != "sha256:" + digest:
                    raise RuntimeError(f"behavior-causality capture failed validation: {session_id}/{checkpoint_id}")
                artifacts[f"cat-{session_id}-{checkpoint_id}.png"] = png
                checkpoint_captures.append({
                    "checkpoint_id": checkpoint_id,
                    "frame": int(acknowledgement["frame"]),
                    "sha256": digest,
                })
                captured.add(checkpoint_id)
        if session.rows[-1].get("wgc") is True and not capture:
            break
        sequence = session.send_action("", 0.0, 1)
        acknowledgement = session.receive_ack("action_ack", sequence)
        session.receive_through_frame(int(acknowledgement["end_frame"]))
        steps += 1
        if session.rows[-1].get("wgc") is True and (
            not capture or {"spawn", "projectile_contact", "enemy_removed", "extraction_contact"} <= captured
        ):
            break
    final = session.rows[-1]
    session.stop("goal_reached" if final["wgc"] else "budget_exhausted", wait_for_ack=True)
    report = _semantic_report("behavior_causality/" + session_id, session.rows, session.events, steps)
    artifacts[f"cat-{session_id}.semantic-report.json"] = (
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return artifacts, {
        "session_id": session_id,
        "action": action,
        "hold_frames": hold_frames,
        "initial_enemy_count": initial["g"]["gb_enemy"],
        "final_enemy_count": final["g"]["gb_enemy"],
        "initial_score": initial["n"]["score"],
        "final_score": final["n"]["score"],
        "projectile_enemy_overlap": _role_pair_overlap(session.rows, "gb_projectile", "gb_enemy"),
        "whole_game_clear": final["wgc"],
        "row_count": len(session.rows),
        "checkpoints": [
            str(event.get("value") or "") for event in session.events
            if event.get("kind") == "checkpoint"
        ],
        "checkpoint_captures": checkpoint_captures,
    }


def _cat_expected_outcomes(results: dict[str, dict[str, object]]) -> dict[str, object]:
    positives = [results[f"positive_{index:02d}"] for index in range(1, 6)]
    no_fire = results["no_fire_control"]
    witness_candidate = results["witness_candidate"]
    witness_hidden = results["witness_hidden"]
    checks = {
        "positive": {
            "status": "passed" if (
                all(item["final_enemy_count"] == 0 for item in positives)
                and all(item["projectile_enemy_overlap"] is True for item in positives)
                and all(item["whole_game_clear"] is True for item in positives)
                and len({
                    (
                        item["final_enemy_count"],
                        item["final_score"],
                        item["projectile_enemy_overlap"],
                        item["whole_game_clear"],
                    )
                    for item in positives
                }) == 1
                and no_fire["final_enemy_count"] == 1
                and no_fire["whole_game_clear"] is False
            ) else "failed",
            "observed_primary_item": None,
            "resolved": True,
            "repeat_count": len(positives),
            "strict_verdict_vector": [
                positives[0]["final_enemy_count"],
                positives[0]["final_score"],
                positives[0]["projectile_enemy_overlap"],
                positives[0]["whole_game_clear"],
            ],
        },
        "auto_win": {
            "status": "expected_failure_observed" if results["auto_win"]["whole_game_clear"] is True else "failed",
            "observed_primary_item": "unity_auto_win_ready",
            "resolved": False,
        },
        "input_ignored": {
            "status": "expected_failure_observed" if (
                results["input_ignored"]["initial_enemy_count"] == results["input_ignored"]["final_enemy_count"]
                and results["input_ignored"]["initial_score"] == results["input_ignored"]["final_score"]
            ) else "failed",
            "observed_primary_item": "unity_input_dispatch",
            "resolved": False,
        },
        "enemy_auto_disappears": {
            "status": "expected_failure_observed" if results["enemy_auto_disappears"]["final_enemy_count"] == 0 else "failed",
            "observed_primary_item": "unity_counterfactual",
            "resolved": False,
        },
        "telemetry_fake": {
            "status": "expected_failure_observed" if (
                results["telemetry_fake"]["final_score"] == 999
                and results["telemetry_fake"]["final_enemy_count"] == 1
                and results["telemetry_fake"]["projectile_enemy_overlap"] is False
            ) else "failed",
            "observed_primary_item": "unity_hidden_behavior",
            "resolved": False,
        },
        "witness_only": {
            "status": "expected_failure_observed" if (
                witness_candidate["whole_game_clear"] is True
                and witness_hidden["whole_game_clear"] is False
            ) else "failed",
            "observed_primary_item": "unity_hidden_behavior",
            "resolved": False,
        },
        "visual_only": {
            "status": "expected_failure_observed" if results["visual_only"]["whole_game_clear"] is False else "failed",
            "observed_primary_item": "unity_hidden_behavior",
            "resolved": False,
        },
    }
    if checks["positive"]["status"] != "passed":
        raise RuntimeError("cat positive fixture did not satisfy causal mechanics")
    if any(value["status"] != "expected_failure_observed" for key, value in checks.items() if key != "positive"):
        raise RuntimeError(f"cat negative fixture did not hit its primary gate: {checks}")
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hidden", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()


    hidden_value = args.hidden.read_text(encoding="utf-8").strip()
    if not hidden_value:
        raise RuntimeError("hidden fixture value is empty")

    cat_servers = [LoopbackControllerServer(timeout=86400.0) for _ in CAT_RUNS]
    try:
        with LoopbackControllerServer(timeout=86400.0) as protocol_server, \
             LoopbackControllerServer(timeout=86400.0) as observer_server, socket.socket(
            socket.AF_INET, socket.SOCK_STREAM
        ) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            server.settimeout(86400.0)
            port = int(server.getsockname()[1])
            _write_json(
                args.ready,
                {
                    "pid": os.getpid(),
                    "port": port,
                    "protocol_port": protocol_server.port,
                    "observer_port": observer_server.port,
                    "cat_ports": [item.port for item in cat_servers],
                },
            )
            observer_artifacts, observer_summary = _run_observer_fixture(observer_server, args.nonce)
            protocol_artifacts, protocol_summary = _run_protocol_fixture(protocol_server, args.nonce)
            cat_artifacts: dict[str, bytes] = {}
            cat_results: dict[str, dict[str, object]] = {}
            for cat_server, (session_id, _variant, action, hold_frames, capture) in zip(cat_servers, CAT_RUNS):
                artifacts_for_run, result = _run_cat_session(
                    cat_server, args.nonce, session_id, action, hold_frames, capture
                )
                cat_artifacts.update(artifacts_for_run)
                cat_results[session_id] = result
            cat_checks = _cat_expected_outcomes(cat_results)
            cat_summary = {"runs": cat_results, "expected_outcomes": cat_checks}
            cat_artifacts["cat-summary.json"] = (
                json.dumps(cat_summary, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            with server.accept()[0] as client:
                stream = client.makefile("rwb", buffering=0)
                stream.write(
                    (
                        json.dumps(
                            {
                                "schema": "gamebench.sandbox-protocol.v1",
                                "nonce": args.nonce,
                                "message": "ready",
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                candidate = json.loads(stream.readline().decode("utf-8"))
                stream.write(b'{"accepted":true}\n')
    finally:
        for cat_server in cat_servers:
            cat_server.close()

    artifacts, unity_fixture = _validated_unity_artifacts(candidate)
    artifacts.update(protocol_artifacts)
    artifacts.update(observer_artifacts)
    artifacts.update(cat_artifacts)

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
    failures = [key for key in required_true if candidate.get(key) is not True]
    report = {
        "schema": "gamebench.unity-m5-005-sandbox-report.v1",
        "status": "passed" if not failures else "failed",
        "score_eligible": False,
        "candidate_probe": candidate,
        "unity_fixture": unity_fixture,
        "protocol_fixture": protocol_summary,
        "observer_fixture": observer_summary,

        # cat-* artifact filenames for evidence attribution, but expose the

        "behavior_causality": cat_summary,
        "candidate_failures": failures,
        "controller_uid": os.getuid(),
        "hidden_value_sha256": hashlib.sha256(hidden_value.encode("utf-8")).hexdigest(),
        "hidden_value_disclosed": hidden_value in json.dumps(candidate, sort_keys=True),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in artifacts.items():
        (args.output / name).write_bytes(data)
    report_path = args.output / "report.json"
    _write_json(report_path, report)
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    manifest_artifacts = [
        {"path": "report.json", "sha256": digest, "bytes": report_path.stat().st_size}
    ]
    manifest_artifacts.extend(
        {
            "path": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
        for name, data in sorted(artifacts.items())
    )
    _write_json(
        args.output / "artifact-manifest.json",
        {"schema": "gamebench.unity-artifact-manifest.v1", "artifacts": manifest_artifacts},
    )
    return 0 if not failures and not report["hidden_value_disclosed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
