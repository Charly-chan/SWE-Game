


from __future__ import annotations

import json
import base64
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from ...routes.agent import Op, mash_ops
from ..modes import UNITY_ACTIONS
from ..package import write_json
from .unity_controller import (
    PROTOCOL as CONTROLLER_PROTOCOL,
    LoopbackControllerServer,
    UnityControllerProtocolError,
    UnityControllerSession,
)
from .unity_sandbox_process import (
    candidate_process_command,
    grant_candidate_access,
    terminate_candidate_process_tree,
)
from .unity_interface import UnityInterfaceManifest
from .unity_observer import UnitySemanticReportBuilder
from .unity_behavior import (
    UnityCounterfactual,
    UnityHiddenBehaviorScenario,
)
from .unity_policy import (
    ClosedLoopPolicy,
    PolicyAction,
    load_declarative_policies,
    run_closed_loop_policy,
)
from .unity_environment import (
    UnityEnvironmentProfile,
    coerce_environment_profile,
)


RuntimeStatus = Literal["pass", "fail", "inconclusive"]
PROTOCOL = CONTROLLER_PROTOCOL
CONTROLLER_CONFIG_SCHEMA = "gamebench.unity-controller-config.v1"
AUTO_WIN_MIN_FRAMES = 60
AUTO_WIN_MAX_FRAMES = 600
DEFAULT_TIME_SCALE = 8.0
COUNTERFACTUAL_GRACE_FRAMES = 600

_DISPLAY_INFRASTRUCTURE = (
    "failed to open display",
    "unable to open x display",
    "no protocol specified",
    "xvfb-run: error",
    "error opening terminal",
)


@dataclass(frozen=True)
class UnityProbeRun:
    run_id: str
    kind: str
    status: RuntimeStatus
    detail: str
    command: tuple[str, ...]
    returncode: int | None
    plan_path: str
    result_path: str
    log_path: str
    reached: bool | None = None
    frame_paths: tuple[str, ...] = ()
    video_path: str | None = None
    reading: Mapping[str, Any] = field(default_factory=dict)

    @property
    def won(self) -> bool:
        return self.reached is True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        reading = dict(self.reading)
        rows = list(reading.pop("rows", ()) or ())
        events = list(reading.pop("events", ()) or ())
        errors = list(reading.pop("errors", ()) or ())
        behavior = reading.get("behavior_result")
        if isinstance(behavior, Mapping):
            behavior = dict(behavior)
            behavior_rows = list(behavior.pop("rows", ()) or ())
            behavior_actions = list(behavior.pop("actions", ()) or ())
            behavior["row_count"] = len(behavior_rows)
            behavior["action_count"] = len(behavior_actions)
            reading["behavior_result"] = behavior
        reading["row_count"] = len(rows)
        reading["event_count"] = len(events)
        reading["error_count"] = len(errors)
        reading["error_examples"] = errors[:8]
        reading["first_frame"] = rows[0].get("f") if rows else None
        reading["last_frame"] = rows[-1].get("f") if rows else None
        event_counts: dict[str, int] = {}
        for event in events:
            kind = str(event.get("kind") or "unknown") if isinstance(event, Mapping) else "invalid"
            event_counts[kind] = event_counts.get(kind, 0) + 1
        reading["event_counts"] = event_counts
        reading["artifact_path"] = self.result_path
        data["reading"] = reading
        return data


@dataclass(frozen=True)
class UnityRuntimeSuite:
    status: RuntimeStatus
    detail: str
    witness: UnityProbeRun
    matched_null: UnityProbeRun
    mash: UnityProbeRun | None
    hidden_routes: tuple[UnityProbeRun, ...] = ()
    hidden_behaviors: tuple[UnityProbeRun, ...] = ()
    counterfactuals: tuple[UnityProbeRun, ...] = ()
    attribution: str = "infrastructure"
    environment: Mapping[str, Any] = field(default_factory=dict)
    auto_win: UnityProbeRun | None = None

    @property
    def frame_paths(self) -> tuple[str, ...]:
        return self.witness.frame_paths + tuple(
            path for run in self.hidden_behaviors for path in run.frame_paths
        )

    @property
    def video_path(self) -> str | None:
        return self.witness.video_path

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "witness": self.witness.to_dict(),
            "matched_null": self.matched_null.to_dict(),
            "mash": self.mash.to_dict() if self.mash is not None else None,
            "hidden_routes": [run.to_dict() for run in self.hidden_routes],
            "hidden_behaviors": [run.to_dict() for run in self.hidden_behaviors],
            "counterfactuals": [run.to_dict() for run in self.counterfactuals],
            "attribution": self.attribution,
            "environment": dict(self.environment),
            "auto_win": self.auto_win.to_dict() if self.auto_win is not None else None,
        }


def run_unity_runtime_suite(
    executable: str | Path,
    manifest: UnityInterfaceManifest,
    ops: Sequence[Op],
    out_dir: str | Path,
    *,
    hidden_route_path: str | Path | None = None,
    hidden_behavior_dir: str | Path | None = None,
    timeout: int = 240,
    xvfb_bin: str | Path | None = None,
    ffmpeg_bin: str | Path | None = None,
    environment_profile: UnityEnvironmentProfile | Mapping[str, Any] | None = None,
    trusted_fixture: bool = False,
    diagnostic_smoke: bool = False,
    visual_capture_requested: bool = False,
) -> UnityRuntimeSuite:
    """Run the fixed M5 intervention suite against one freshly-built player."""
    player = Path(executable).resolve()
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    grant_candidate_access((root, player.parent))
    profile = coerce_environment_profile(environment_profile)
    if not profile.ready_for_untrusted_execution and not (
        trusted_fixture and profile.environment_class == "local-linux-uncertified"
    ):
        return _infrastructure_refusal_suite(player, root, profile)
    xvfb = _resolve_tool(xvfb_bin, "xvfb-run")
    if xvfb is None:
        refused = UnityEnvironmentProfile(
            **{
                **profile.to_dict(),
                "detail": (
                    "graphical runtime requires the Community Xvfb/GPU profile; "
                    "headless -nographics fallback is forbidden"
                ),
            }
        )
        return _infrastructure_refusal_suite(player, root, refused)
    horizon = max(1, sum(max(1, int(op.frames)) for op in ops))

    requested_scale = _requested_time_scale()
    semantic_scale = 1.0 if diagnostic_smoke else requested_scale
    clock_evidence: dict[str, Any] = {
        "requested_scale": requested_scale,
        "selected_scale": semantic_scale,
        "status": "disabled" if semantic_scale <= 1.0 else "evaluator_forced",
        "policy": (
            "evaluator-owned injected probe reasserts Time.timeScale every "
            "Update, FixedUpdate, LateUpdate, and scene load; calibrated "
            "semantic runs do not fall back to realtime"
        ),
    }

    witness_ops = _diagnostic_smoke_ops(ops) if diagnostic_smoke else ops
    witness = _run_probe(
        player,
        manifest,
        witness_ops,
        root / "witness",
        run_id="submitted_witness",
        kind="witness",
        start_level=0,
        target_scenes=(_ending_scene(manifest, "success"),),
        capture=True,
        timeout=timeout,
        xvfb_bin=xvfb,
        ffmpeg_bin=ffmpeg_bin,
        time_scale=1.0 if visual_capture_requested else semantic_scale,
        record_full_video=visual_capture_requested,
    )
    if diagnostic_smoke:
        skipped = _diagnostic_skipped_run(root, "matched_null", "matched_null")
        return UnityRuntimeSuite(
            status="inconclusive",
            detail=(
                "diagnostic VM smoke completed one build and one Player run; "
                "score-bearing counterfactuals were intentionally not executed"
            ),
            witness=witness,
            matched_null=skipped,
            mash=None,
            hidden_routes=(),
            hidden_behaviors=(),
            counterfactuals=(),
            attribution="infrastructure",
            environment={**profile.to_dict(), "diagnostic_smoke": True},
            auto_win=None,
        )
    # A broken submitted tape does not prove that an independent evaluator
    # scenario is broken. Run each cold-start scenario on its own evidence.
    matched_null = _run_probe(
        player,
        manifest,
        _matched_null_ops(ops),
        root / "matched_null",
        run_id="matched_null",
        kind="matched_null",
        start_level=0,
        target_scenes=(_ending_scene(manifest, "success"),),
        capture=True,
        timeout=timeout,
        xvfb_bin=xvfb,
        ffmpeg_bin=ffmpeg_bin,
        time_scale=semantic_scale,
    )
    auto_win_horizon = min(AUTO_WIN_MAX_FRAMES, max(AUTO_WIN_MIN_FRAMES, horizon))
    # The matched-null run is a strictly stronger no-input observation than the
    # old short auto-win duplicate: it starts from the same cold scene and lasts
    # for the complete witness horizon. Reuse it when it produced a reading.
    if matched_null.status == "pass":
        auto_win = replace(
            matched_null,
            run_id="auto_win_ready",
            kind="auto_win",
            detail="reused complete matched-null horizon as auto-win control",
        )
        # Keep the reused control's artifact contract discoverable under its
        # own run id. Older callers and audits expect auto_win/plan.json even
        # when no second Player launch is needed.
        auto_win_root = root / "auto_win"
        auto_win_root.mkdir(parents=True, exist_ok=True)
        if Path(matched_null.plan_path).is_file():
            reused_plan = json.loads(Path(matched_null.plan_path).read_text(encoding="utf-8"))
            reused_plan["run_id"] = "auto_win_ready"
            write_json(auto_win_root / "plan.json", reused_plan)
    else:
        auto_win = _run_probe(
            player,
            manifest,
            [Op(op="wait", frames=auto_win_horizon)],
            root / "auto_win",
            run_id="auto_win_ready",
            kind="auto_win",
            start_level=0,
            target_scenes=(_ending_scene(manifest, "success"),),
            capture=False,
            timeout=timeout,
            xvfb_bin=xvfb,
            ffmpeg_bin=ffmpeg_bin,
            time_scale=semantic_scale,
        )
    mash: UnityProbeRun | None = None
    mash_tape = mash_ops(
        manifest.extended_actions,
        auto_win_horizon,
        manifest.analog_axes,
    )
    if mash_tape:
        mash = _run_probe(
            player,
            manifest,
            mash_tape,
            root / "mash",
            run_id="extended_mash",
            kind="mash",
            start_level=0,
            target_scenes=(_ending_scene(manifest, "success"),),
            capture=False,
            timeout=timeout,
            xvfb_bin=xvfb,
            ffmpeg_bin=ffmpeg_bin,
            time_scale=semantic_scale,
        )

    hidden: list[UnityProbeRun] = []
    for route in _hidden_routes(hidden_route_path):
        level_index = int((route.get("start") or {}).get("level") or 0)
        route_ops = _ops_from_route(route)
        if not route_ops or not (0 <= level_index < len(manifest.levels)):
            continue
        target = _route_target_scenes(manifest, level_index, route)
        hidden.append(
            _run_probe(
                player,
                manifest,
                route_ops,
                root / "hidden" / _safe_id(str(route.get("route_id") or "route")),
                run_id=str(route.get("route_id") or f"hidden_{len(hidden)}"),
                kind="hidden_route",
                start_level=level_index,
                target_scenes=target,
                capture=False,
                timeout=timeout,
                xvfb_bin=xvfb,
                ffmpeg_bin=ffmpeg_bin,
                time_scale=semantic_scale,
            )
        )

    hidden_behaviors: list[UnityProbeRun] = []
    counterfactual_runs: list[UnityProbeRun] = []
    behavior_inputs = _select_hidden_behavior_inputs(
        _hidden_behavior_inputs(hidden_behavior_dir)
    )
    for scenario, policy_path in behavior_inputs:
        _, policies = load_declarative_policies(policy_path)
        policy = policies[scenario.policy]
        hidden_behaviors.append(
            _run_probe(
                player,
                manifest,
                (),
                root / "behavior" / _safe_id(scenario.id),
                run_id=scenario.id,
                kind="hidden_behavior",
                start_level=scenario.start_level,
                target_scenes=(),
                # Sample trusted checkpoints, not a judge-owned video.
                capture=True,
                timeout=timeout,
                xvfb_bin=xvfb,
                ffmpeg_bin=ffmpeg_bin,
                scenario=scenario,
                policy=policy,
                time_scale=semantic_scale,
            )
        )
        for counterfactual in scenario.counterfactuals:
            _, fresh_policies = load_declarative_policies(policy_path)
            bounded_scenario = replace(
                scenario,
                budget_frames=_counterfactual_budget(
                    scenario, counterfactual, hidden_behaviors[-1]
                ),
            )
            counterfactual_runs.append(
                _run_probe(
                    player,
                    manifest,
                    (),
                    root / "counterfactual" / _safe_id(
                        f"{scenario.id}-{counterfactual.id}"
                    ),
                    run_id=f"{scenario.id}/{counterfactual.id}",
                    kind="counterfactual",
                    start_level=scenario.start_level,
                    target_scenes=(),
                    capture=False,
                    timeout=timeout,
                    xvfb_bin=xvfb,
                    ffmpeg_bin=ffmpeg_bin,
                    scenario=bounded_scenario,
                    policy=fresh_policies[scenario.policy],
                    counterfactual=counterfactual,
                    time_scale=semantic_scale,
                )
            )

    runs = (
        witness, matched_null, auto_win, *((mash,) if mash is not None else ()),
        *hidden, *hidden_behaviors,
        *counterfactual_runs,
    )
    if any(run.status == "fail" for run in runs):
        status: RuntimeStatus = "fail"
        detail = "one or more candidate runtime executions failed"
    elif any(run.status == "inconclusive" for run in runs):
        status = "inconclusive"
        detail = "one or more evaluator runtime executions were inconclusive"
    else:
        status = "pass"
        detail = "all evaluator-owned Unity runtime interventions completed"
    return UnityRuntimeSuite(
        status,
        detail,
        witness,
        matched_null,
        mash,
        tuple(hidden),
        tuple(hidden_behaviors),
        tuple(counterfactual_runs),
        attribution="submission" if status == "fail" else "infrastructure",
        environment={**profile.to_dict(), "clock_acceleration": clock_evidence},
        auto_win=auto_win,
    )






def _diagnostic_skipped_run(root: Path, run_id: str, kind: str) -> UnityProbeRun:
    run_root = root / run_id
    return UnityProbeRun(
        run_id=run_id,
        kind=kind,
        status="inconclusive",
        detail="not executed by non-scoring diagnostic VM smoke",
        command=(),
        returncode=None,
        plan_path=str(run_root / "plan.json"),
        result_path=str(run_root / "result.json"),
        log_path=str(run_root / "player.log"),
        reading={"attribution": "infrastructure", "diagnostic_smoke": True},
    )


def _infrastructure_refusal_suite(
    player: Path,
    root: Path,
    profile: UnityEnvironmentProfile,
) -> UnityRuntimeSuite:
    detail = profile.detail or "Unity runtime environment is not ready"

    def refused(run_id: str, kind: str) -> UnityProbeRun:
        run_root = root / run_id
        return UnityProbeRun(
            run_id=run_id,
            kind=kind,
            status="inconclusive",
            detail=detail,
            command=(),
            returncode=None,
            plan_path=str(run_root / "plan.json"),
            result_path=str(run_root / "result.json"),
            log_path=str(run_root / "player.log"),
            reached=None,
            reading={"attribution": "infrastructure", "player": str(player)},
        )

    return UnityRuntimeSuite(
        status="inconclusive",
        detail=detail,
        witness=refused("submitted_witness", "witness"),
        matched_null=refused("matched_null", "matched_null"),
        mash=None,
        hidden_routes=(),
        hidden_behaviors=(),
        counterfactuals=(),
        attribution="infrastructure",
        environment=profile.to_dict(),
        auto_win=None,
    )


def _scenario_provenance(
    scenario: UnityHiddenBehaviorScenario | None,
    counterfactual: UnityCounterfactual | None,
) -> dict[str, Any]:
    """Keep evaluator-owned scenario identity even if candidate input fails."""

    if scenario is None:
        return {}
    return {
        "evidence_basis": list(scenario.evidence_basis),
        "policy_id": scenario.policy,
        "graded_milestones": [
            milestone.name for milestone in scenario.goal.milestones
        ] + ([
            milestone.name for milestone in scenario.source_goal.milestones
        ] if scenario.source_goal is not None else []),
        "counterfactual_id": counterfactual.id if counterfactual is not None else "",
        "counterfactual_expect_goal": (
            counterfactual.expect_goal if counterfactual is not None else None
        ),
    }


def _run_probe(
    executable: Path,
    manifest: UnityInterfaceManifest,
    ops: Sequence[Op],
    out_dir: Path,
    *,
    run_id: str,
    kind: str,
    start_level: int,
    target_scenes: Sequence[str],
    capture: bool,
    timeout: int,
    xvfb_bin: str | Path | None,
    ffmpeg_bin: str | Path | None,
    scenario: UnityHiddenBehaviorScenario | None = None,
    policy: ClosedLoopPolicy | None = None,
    counterfactual: UnityCounterfactual | None = None,
    time_scale: float = 1.0,
    record_full_video: bool = False,
) -> UnityProbeRun:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    capture_dir = out_dir / "capture"
    capture_dir.mkdir()
    result_path = out_dir / "result.json"
    log_path = out_dir / "player.log"
    plan_path = out_dir / "plan.json"
    normalized_ops = list(ops)
    effective_timeout = _probe_wall_timeout(
        normalized_ops,
        scenario=scenario,
        floor_seconds=timeout,
    )
    config = _plan_payload(
        manifest,
        run_id=run_id,
        start_level=start_level,
    )
    write_json(plan_path, config)
    scenario_provenance = _scenario_provenance(scenario, counterfactual)
    # The trusted controller owns hidden policy state, but the reduced player
    # identity must be able to create its Unity log and Xvfb artifacts in this
    # candidate-only run directory.
    grant_candidate_access((out_dir,))

    xvfb = _resolve_tool(xvfb_bin, "xvfb-run")
    if xvfb is None:
        return UnityProbeRun(
            run_id,
            kind,
            "inconclusive",
            "graphical runtime has no Xvfb/GPU profile; -nographics fallback is forbidden",
            (),
            None,
            str(plan_path),
            str(result_path),
            str(log_path),
            None,
        )
    nonce = secrets.token_hex(32)
    try:
        build_digest = _sha256_file(executable)
    except OSError as exc:
        return UnityProbeRun(
            run_id, kind, "inconclusive", f"evaluator could not read player: {exc}",
            (), None, str(plan_path), str(result_path), str(log_path), None,
        )
    supported = manifest.supported_actions or tuple(manifest.actions)
    roles = tuple(sorted(manifest.declared_roles | {"gb_player"}))
    numeric = tuple(sorted(manifest.declared_numeric_slots))
    with LoopbackControllerServer(timeout=effective_timeout) as server:
        base = [
            str(executable),
            "-screen-fullscreen", "0",
            "-screen-width", "960",
            "-screen-height", "540",
            "-logFile", str(log_path),
            f"--gb-controller-port={server.port}",
            f"--gb-run-id={run_id}",
            f"--gb-nonce={nonce}",
            f"--gb-build-digest={build_digest}",
            f"--gb-start-scene={manifest.levels[start_level].scene}",
            f"--gb-level-count={len(manifest.levels)}",
            "--gb-required-roles=" + ",".join(roles),
            "--gb-numeric-slots=" + ",".join(numeric),
            "--gb-supported-actions=" + ",".join(supported),
            "--gb-supported-axes=" + ",".join(item.id for item in manifest.analog_axes),
            f"--gb-time-scale={float(time_scale):.6g}",
            "--gb-observation-stride=" + str(
                1 if record_full_video else int(os.environ.get("GB_UNITY_OBSERVATION_STRIDE", "8"))
            ),
        ]
        full_video = out_dir / "candidate_capture_full.mp4"
        recorder = _resolve_tool(ffmpeg_bin, "ffmpeg") if record_full_video else None
        if recorder is not None:
            capture_script = (
                'video="$1"; recorder="$2"; shift 2; '
                '"$recorder" -nostdin -y -loglevel error -f x11grab '
                '-draw_mouse 0 -framerate 15 -video_size 960x540 '
                '-i "${DISPLAY}.0" -c:v libx264 -preset ultrafast '
                '-pix_fmt yuv420p "$video" & recorder_pid=$!; '
                '"$@"; rc=$?; kill -INT "$recorder_pid" 2>/dev/null || true; '
                'wait "$recorder_pid" 2>/dev/null || true; exit "$rc"'
            )
            launch = [
                str(xvfb), "-a", "--server-args=-screen 0 960x540x24",
                "/bin/bash", "-c", capture_script, "gb-unity-capture",
                str(full_video), str(recorder), *base,
            ]
        else:
            launch = [str(xvfb), "-a", *base]
        command = list(candidate_process_command(launch))
        wall_started = time.monotonic()
        launcher_path = out_dir / "launcher.log"
        try:
            launcher_handle = launcher_path.open("w", encoding="utf-8", errors="replace")
            process = subprocess.Popen(
                command,
                text=True,
                stdout=launcher_handle,
                stderr=subprocess.STDOUT,
                env=os.environ.copy(),
                start_new_session=os.name == "posix",
            )
            launcher_handle.close()
        except OSError as exc:
            if "launcher_handle" in locals():
                launcher_handle.close()
            return UnityProbeRun(
                run_id, kind, "inconclusive", f"evaluator could not execute player: {exc}",
                tuple(command), None, str(plan_path), str(result_path), str(log_path), None,
            )

        session: UnityControllerSession | None = None
        controller_error = ""
        capture_paths: list[str] = []
        active_started: float | None = None
        try:
            session = UnityControllerSession(
                server.accept(), run_id=run_id, nonce=nonce, build_digest=build_digest
            )
            session.handshake()
            session.receive_ready()
            initial_row = session.receive_observation()["row"]
            # Process/Xvfb/scene startup is nearly constant and dominates a
            # short clock sentinel.  Measure acceleration only after the probe
            # is ready and the evaluator begins advancing simulation.
            active_started = time.monotonic()
            steps = 0
            behavior_result = None
            captured_checkpoint_ids: set[str] = set()
            visual_captures: list[dict[str, Any]] = []

            def capture_checkpoint(checkpoint_id: str) -> None:
                if not capture or checkpoint_id in captured_checkpoint_ids or len(captured_checkpoint_ids) >= 32:
                    return
                state = dict(session.rows[-1]) if session.rows else {}
                sequence = session.send_capture(checkpoint_id, visual_audit=True)
                capture_ack = session.receive_ack("capture_ack", sequence)
                png = base64.b64decode(str(capture_ack.get("png_base64") or ""), validate=True)
                digest = "sha256:" + hashlib.sha256(png).hexdigest()
                if capture_ack.get("bytes_digest") != digest or not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise UnityControllerProtocolError("capture acknowledgement digest or PNG signature mismatch")
                frame_path = capture_dir / (
                    f"{_safe_id(checkpoint_id)}_{int(capture_ack.get('frame', 0)):08d}.png"
                )
                frame_path.write_bytes(png)
                capture_paths.append(str(frame_path.resolve()))
                captured_checkpoint_ids.add(checkpoint_id)
                # Older trusted fixtures may provide the PNG capture without the
                # optional visual probe record. Preserve the capture and runtime
                # result; visual correspondence simply has no measurement for it.
                visual = capture_ack.get("visual")
                if isinstance(visual, dict) and visual.get("schema") == "gamebench.mode5.visual-capture.v1":
                    visual_captures.append({"record": visual, "state": state,
                                            "frame_path": str(frame_path.resolve()),
                                            "checkpoint_id": checkpoint_id, "run_id": run_id,
                                            "start_level": start_level})
                    write_json(capture_dir / (frame_path.stem + ".visual.json"), visual_captures[-1])
            if capture:
                capture_checkpoint("run_start")
            if scenario is not None:
                if policy is None:
                    raise UnityControllerProtocolError(
                        "hidden behavior run has no evaluator-owned policy"
                    )

                def transformed(action: PolicyAction) -> PolicyAction:
                    applied = action
                    if counterfactual is not None:
                        if counterfactual.remove == action.canonical_action:
                            applied = PolicyAction(
                                "", 0.0, action.hold_frames, action.actions, action.axes
                            )
                        elif counterfactual.replace == action.canonical_action:
                            applied = PolicyAction(
                                counterfactual.with_action,
                                action.value,
                                action.hold_frames,
                                action.actions,
                                action.axes,
                            )
                        elif counterfactual.neutralize_axis:
                            applied = PolicyAction(
                                action.canonical_action,
                                action.value,
                                action.hold_frames,
                                action.actions,
                                tuple(
                                    (axis_id, counterfactual.with_value if axis_id == counterfactual.neutralize_axis else axis_value)
                                    for axis_id, axis_value in action.axes
                                ),
                            )
                    return applied

                def dispatch(action: PolicyAction) -> Sequence[Mapping[str, Any]]:
                    first_row = len(session.rows)
                    applied = transformed(action)
                    sequence = session.send_action(
                        applied.canonical_action, applied.value, applied.hold_frames,
                        actions=applied.actions, axes=applied.axes,
                    )
                    acknowledgement = session.receive_ack("action_ack", sequence)
                    session.receive_through_frame(
                        int(acknowledgement["end_frame"])
                    )
                    return session.rows[first_row:]

                def dispatch_batch(actions: Sequence[PolicyAction]) -> Sequence[Mapping[str, Any]]:
                    first_row = len(session.rows)
                    sequence = session.send_action_batch(
                        [transformed(action).to_dict() for action in actions]
                    )
                    acknowledgement = session.receive_ack("action_batch_ack", sequence)
                    session.receive_through_frame(int(acknowledgement["end_frame"]))
                    return session.rows[first_row:]

                frozen_bounds = next((event for event in session.events
                                      if event.get("kind") == "bounds_frozen"
                                      and event.get("observable") is True), {})
                policy_origin = dict(frozen_bounds.get("min") or {})
                policy_maximum = dict(frozen_bounds.get("max") or {})
                policy_extent = {
                    axis: float(policy_maximum[axis]) - float(policy_origin[axis])
                    for axis in ("x", "y", "z")
                    if axis in policy_maximum and axis in policy_origin
                }
                for checkpoint in scenario.capture_checkpoints:
                    if checkpoint.kind == "run_start":
                        capture_checkpoint(checkpoint.id)

                def on_checkpoint(logical_id: str, _row: Mapping[str, Any]) -> None:
                    predicate_by_logical_id = {
                        item.name: item.predicate for item in scenario.goal.milestones
                    }
                    if scenario.source_goal is not None:
                        predicate_by_logical_id.update({
                            item.name: item.predicate
                            for item in scenario.source_goal.milestones
                        })
                    logical_predicate = (
                        scenario.goal.predicate
                        if logical_id == "goal"
                        else predicate_by_logical_id.get(logical_id, "")
                    )
                    ids = [
                        item.id for item in scenario.capture_checkpoints
                        if item.id == logical_id
                        or (logical_predicate and item.predicate == logical_predicate)
                        or (logical_id == "goal" and item.kind == "outcome_success")
                    ]
                    for checkpoint_id in ids:
                        capture_checkpoint(checkpoint_id)

                behavior_result = run_closed_loop_policy(
                    scenario,
                    policy,
                    initial_row,
                    dispatch,
                    dispatch_batch=dispatch_batch,
                    group_totals={
                        str(key): int(value)
                        for key, value in dict(initial_row.get("g") or {}).items()
                    },
                    origin=policy_origin,
                    extent=policy_extent,
                    on_checkpoint=on_checkpoint,
                )
                steps = len(behavior_result.actions)
                reached = behavior_result.goal_reached
            else:
                visual_stride = max(1, (len(normalized_ops) + 15) // 16)
                for op in normalized_ops:
                    for actions, axes, frames in _command_segments(op, manifest):
                        scalar = actions[0] if len(actions) == 1 else ""
                        sequence = session.send_action(
                            scalar, 1.0 if scalar else 0.0, frames,
                            actions=actions, axes=axes,
                        )
                        acknowledgement = session.receive_ack("action_ack", sequence)
                        session.receive_through_frame(int(acknowledgement["end_frame"]))
                        steps += 1
                    if capture and steps % visual_stride == 0:
                        capture_checkpoint(f"step_{steps}")
            # A fixed post-settle window catches delayed outcomes without sending
            # the hidden target or expected verdict to the player.
            sequence = session.send_action("", 0.0, 18)
            acknowledgement = session.receive_ack("action_ack", sequence)
            session.receive_through_frame(int(acknowledgement["end_frame"]))
            steps += 1
            if capture and scenario is None:
                capture_checkpoint("run_end")
            if scenario is None:
                reached = _stream_reached(session.rows, target_scenes)
            stop_reason = "goal_reached" if reached else "budget_exhausted"
            session.stop(stop_reason, wait_for_ack=True)
            builder = UnitySemanticReportBuilder(run_id, declared_level_count=len(manifest.levels))
            builder.steps = steps
            builder.add_session(session.rows, session.events)
            reading = builder.build(
                stop_reason=stop_reason,
                detail="GBTelemetry numeric values are candidate signals; marker census and overlap are evaluator observations",
            )
            reading["_active_wall_seconds"] = round(
                max(0.0, time.monotonic() - active_started), 6
            )
            reading["visual_captures"] = visual_captures
            if behavior_result is not None:
                reading["behavior_result"] = behavior_result.to_dict()
                reading.update(scenario_provenance)
            write_json(result_path, reading)
        except (OSError, ValueError, KeyError, UnityControllerProtocolError) as exc:
            controller_error = str(exc)
            if session is not None:
                try:
                    session.stop("driver_error", wait_for_ack=True)
                except (OSError, UnityControllerProtocolError):
                    pass
            reading = dict(scenario_provenance)
            reached = None
        finally:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                terminate_candidate_process_tree(process)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    terminate_candidate_process_tree(process, force=True)
                    process.wait()
            terminate_candidate_process_tree(process, force=True)
            if isinstance(reading, dict):
                reading["_wall_seconds"] = round(max(0.0, time.monotonic() - wall_started), 6)

    combined = "\n".join(part for part in (_read_text(launcher_path), _read_text(log_path)) if part)
    lowered = combined.lower()
    if any(marker in lowered for marker in _DISPLAY_INFRASTRUCTURE):
        return UnityProbeRun(
            run_id, kind, "inconclusive", "evaluator display infrastructure failed",
            tuple(command), process.returncode, str(plan_path), str(result_path), str(log_path), None,
        )
    if controller_error:
        # A launched Player that never answers the control channel is a
        # candidate protocol failure, not an evaluator infrastructure gap.
        status: RuntimeStatus = "fail"
        detail = "controller protocol failed: " + controller_error
        return UnityProbeRun(
            run_id, kind, status, detail, tuple(command), process.returncode,
            str(plan_path), str(result_path), str(log_path), reached,
            tuple(capture_paths), None, reading,
        )
    if process.returncode != 0:
        return UnityProbeRun(
            run_id, kind, "fail", f"candidate player exited with code {process.returncode}",
            tuple(command), process.returncode, str(plan_path), str(result_path), str(log_path), reached,
            tuple(capture_paths), None, reading,
        )
    frames = tuple(capture_paths)
    video = (
        str(full_video.resolve())
        if full_video.is_file() and full_video.stat().st_size > 0
        else _encode_video(frames, out_dir / "candidate_capture.mp4", ffmpeg_bin)
    )
    if not reading.get("rows"):
        return UnityProbeRun(
            run_id, kind, "fail", "candidate produced no usable semantic rows",
            tuple(command), process.returncode, str(plan_path), str(result_path), str(log_path),
            reached, frames, video, reading,
        )
    runtime_errors = [str(item) for item in reading.get("errors") or []]
    if runtime_errors:
        # LogError and recoverable exceptions are evidence for stability. A
        # completed semantic stream remains usable for mechanics/behavior.
        return UnityProbeRun(
            run_id, kind, "pass",
            f"semantic stream completed with {len(runtime_errors)} runtime error(s)",
            tuple(command), process.returncode, str(plan_path), str(result_path), str(log_path),
            reached, frames, video, reading,
        )
    return UnityProbeRun(
        run_id, kind, "pass", "evaluator controller/observer stream completed",
        tuple(command), process.returncode, str(plan_path), str(result_path), str(log_path),
        reached, frames, video, reading,
    )


def _probe_wall_timeout(
    ops: Sequence[Op],
    *,
    scenario: UnityHiddenBehaviorScenario | None,
    floor_seconds: int | float,
) -> float:


    op_frames = sum(max(1, int(op.frames)) for op in ops)
    scenario_frames = int(scenario.budget_frames) if scenario is not None else 0
    requested_frames = max(op_frames, scenario_frames)
    derived = 120.0 + requested_frames * 0.02 * 1.5
    return max(float(floor_seconds), min(1800.0, derived))


def _diagnostic_smoke_ops(ops: Sequence[Op], frame_budget: int = 180) -> list[Op]:

    selected: list[Op] = []
    remaining = max(1, int(frame_budget))
    for op in ops:
        if remaining <= 0:
            break
        frames = max(1, int(op.frames))
        used = min(frames, remaining)
        selected.append(replace(op, frames=used))
        remaining -= used
    return selected or [Op(op="wait", frames=1)]


def _plan_payload(
    manifest: UnityInterfaceManifest,
    *,
    run_id: str,
    start_level: int,
) -> dict[str, Any]:
    return {
        "schema": CONTROLLER_CONFIG_SCHEMA,
        "run_id": run_id,
        "start_scene": manifest.levels[start_level].scene,
        "level_count": len(manifest.levels),
        "supported_actions": list(manifest.supported_actions or tuple(manifest.actions)),
        "analog_axes": [item.to_dict() for item in manifest.analog_axes],
        "semantic_roles": sorted(manifest.declared_roles | {"gb_player"}),
        "numeric_slots": sorted(manifest.declared_numeric_slots),
        "note": "public controller metadata only; action stream and hidden goals are not persisted here",
    }


def _command_segments(
    op: Op, manifest: UnityInterfaceManifest
) -> tuple[tuple[tuple[str, ...], tuple[tuple[str, float], ...], int], ...]:
    frames = max(1, int(op.frames))
    if op.op in {"wait", "noop", "release"}:
        return (((), (), frames),)
    actions = tuple(item for item in (op.actions or ((op.action,) if op.action else ())) if item)
    supported = set(manifest.supported_actions or tuple(manifest.actions))
    unknown = sorted(set(actions) - supported)
    if unknown:
        raise UnityControllerProtocolError(f"unsupported action(s) {unknown!r}")
    supported_axes = {item.id for item in manifest.analog_axes}
    unknown_axes = sorted({axis for axis, _ in op.axes} - supported_axes)
    if unknown_axes:
        raise UnityControllerProtocolError(f"unsupported analog axis/axes {unknown_axes!r}")
    axes = tuple(op.axes)
    if not actions and not axes:
        return (((), (), frames),)
    if op.op == "tap" and frames > 1:
        return ((actions, axes, 1), ((), (), frames - 1))
    return ((actions, axes, frames),)


def _stream_reached(rows: Sequence[Mapping[str, Any]], target_scenes: Sequence[str]) -> bool:


    del target_scenes
    return bool(rows and rows[-1].get("wgc", False))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _hidden_routes(path: str | Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    source = Path(path)
    if not source.is_file():
        return []
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    routes = payload.get("routes") if isinstance(payload, dict) else None
    if not isinstance(routes, list):
        return []
    return [
        route for route in routes
        if isinstance(route, dict)
        and route.get("scored", True) is not False
        and isinstance((route.get("authored_solution") or {}).get("ops"), list)
    ]


def _hidden_behavior_inputs(
    directory: str | Path | None,
) -> list[tuple[UnityHiddenBehaviorScenario, Path]]:
    if directory is None:
        return []
    root = Path(directory)


    gate_path = root / "calibration_status.json"
    if not gate_path.is_file():
        gate_path = root / "reference_segments.json"
    if gate_path.is_file():
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        if gate.get("status") != "calibrated" or gate.get("runtime_ready") is False:

            return []
    scenario_path = root / "suite.json"
    policy_path = scenario_path
    if not scenario_path.is_file():
        scenario_path = root / "scenarios.json"
        policy_path = root / "policies.json"
    if not scenario_path.is_file() or not policy_path.is_file():
        return []
    try:
        payload = json.loads(scenario_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = payload.get("scenarios") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    input_contract = payload.get("input_contract") or {}
    supported_actions = tuple(input_contract.get("supported_actions") or UNITY_ACTIONS)
    supported_axes = tuple(
        str(item.get("id") or "") for item in (input_contract.get("analog_axes") or [])
        if isinstance(item, Mapping)
    )
    scenarios = [
        UnityHiddenBehaviorScenario.from_dict(
            item, allowed_actions=supported_actions, allowed_axes=supported_axes
        )
        for item in raw
        if isinstance(item, Mapping)
    ]
    return [(scenario, policy_path) for scenario in scenarios]


def _select_evenly(values: Sequence[Any], maximum: int) -> list[Any]:


    rows = list(values)
    maximum = max(0, int(maximum))
    if len(rows) <= maximum:
        return rows
    if maximum <= 0:
        return []
    if maximum == 1:
        return [rows[len(rows) // 2]]
    indices = [round(index * (len(rows) - 1) / (maximum - 1)) for index in range(maximum)]
    return [rows[index] for index in dict.fromkeys(indices)]


def _select_hidden_behavior_inputs(
    values: Sequence[tuple[UnityHiddenBehaviorScenario, Path]],
) -> list[tuple[UnityHiddenBehaviorScenario, Path]]:


    local = []
    for row in values:
        scenario_id = row[0].id.lower()
        whole_run_root = (
            "--milestone-" not in scenario_id
            and re.search(
                r"(?:^|[-_.])l5[-_.]whole_(?:game|run)_clear$",
                scenario_id,
            )
        )
        if not whole_run_root:
            local.append(row)
    return local


def _counterfactual_budget(
    scenario: UnityHiddenBehaviorScenario,
    counterfactual: UnityCounterfactual,
    positive: UnityProbeRun,
) -> int:


    result = positive.reading.get("behavior_result") or {}
    actions = result.get("actions") if isinstance(result, Mapping) else None
    if not isinstance(actions, list):
        return scenario.budget_frames
    elapsed = 0
    intervention = None
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        frames = max(1, int(action.get("hold_frames") or 1))
        canonical = str(action.get("canonical_action") or "")
        chord = {str(item) for item in action.get("actions") or ()}
        axes = {str(item) for item in dict(action.get("axes") or {})}
        affected = (
            bool(counterfactual.remove and (
                canonical == counterfactual.remove or counterfactual.remove in chord
            ))
            or bool(counterfactual.replace and canonical == counterfactual.replace)
            or bool(counterfactual.neutralize_axis and counterfactual.neutralize_axis in axes)
        )
        elapsed += frames
        if affected:
            intervention = elapsed
            break
    if intervention is None:
        return scenario.budget_frames
    return min(scenario.budget_frames, intervention + COUNTERFACTUAL_GRACE_FRAMES)


def _requested_time_scale() -> float:
    raw = (os.environ.get("GB_UNITY_EVALUATOR_TIME_SCALE") or str(DEFAULT_TIME_SCALE)).strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIME_SCALE
    return min(16.0, max(1.0, value))


def _ops_from_route(route: Mapping[str, Any]) -> list[Op]:
    raw = (route.get("authored_solution") or {}).get("ops") or []
    return [Op.from_dict(item) for item in raw if isinstance(item, dict)]


def _route_target_scenes(
    manifest: UnityInterfaceManifest,
    start_level: int,
    route: Mapping[str, Any],
) -> tuple[str, ...]:
    predicate = str((route.get("goal") or {}).get("predicate") or "")
    success = _ending_scene(manifest, "success")
    if "whole_game_clear" in predicate or start_level + 1 >= len(manifest.levels):
        return (success,)
    return (manifest.levels[start_level + 1].scene, success)


def _matched_null_ops(ops: Sequence[Op]) -> list[Op]:


    remaining = sum(max(1, int(op.frames)) for op in ops)
    result: list[Op] = []
    while remaining:
        frames = min(600, remaining)
        result.append(Op(op="wait", frames=frames))
        remaining -= frames
    return result


def _reached_target(reading: Mapping[str, Any], targets: Sequence[str]) -> bool:
    expected = {_scene_key(item) for item in targets if item}
    observed = {
        _scene_key(str(item))
        for item in reading.get("scenes_visited") or []
        if item
    }
    final_scene = str(reading.get("final_scene") or "")
    if final_scene:
        observed.add(_scene_key(final_scene))
    return bool(expected & observed)


def _scene_key(value: str) -> str:
    text = value.replace("\\", "/").strip()
    if text.lower().endswith(".unity"):
        text = text[:-6]
    return text.rsplit("/", 1)[-1].lower()


def _ending_scene(manifest: UnityInterfaceManifest, kind: str) -> str:
    return next((item.scene for item in manifest.endings if item.kind == kind), "")


def _encode_video(
    frame_paths: Sequence[str],
    output: Path,
    explicit_ffmpeg: str | Path | None,
) -> str | None:
    if not frame_paths:
        return None
    ffmpeg = _resolve_tool(explicit_ffmpeg, "ffmpeg")
    if ffmpeg is None:
        return None
    pattern = str(Path(frame_paths[0]).parent / "*.png")
    process = subprocess.run(
        [
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", "1", "-pattern_type", "glob", "-i", pattern,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return str(output.resolve()) if process.returncode == 0 and output.is_file() else None


def _resolve_tool(explicit: str | Path | None, default: str) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
        found = shutil.which(str(explicit))
        return Path(found).resolve() if found else None
    found = shutil.which(default)
    return Path(found).resolve() if found else None


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "route"


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


__all__ = [
    "PROTOCOL",
    "RuntimeStatus",
    "UnityProbeRun",
    "UnityRuntimeSuite",
    "run_unity_runtime_suite",
]
