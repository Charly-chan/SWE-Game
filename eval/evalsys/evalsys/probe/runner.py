


from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..engine.scene_load import scene_load_failure
from ..ocard import adapters
from ..render.modes import RenderMode, build_command, build_env, run
from ..render.render_cost import first_playable_level
from ..truth.snapshot import (
    ACTIONS,
    ActionLiveness,
    Bounds,
    CollectCycle,
    CollectProbe,
    GoalProbe,
    GroupTruth,
    LevelTruth,
    NormPoint,
    SeriesSample,
    TruthSnapshot,
    Vec3,
)
from .inject import (
    ImportResult,
    ScratchPrep,
    cold_import,
    find_project_root,
    inject_autoload,
    make_scratch_result,
    prepare_scratch,
)


from evalsys.harness import face_path, provenance as harness_provenance

GB_PROBE = face_path("gb_probe.gd")
TRUTH_DRIVER = face_path("gb_truth_driver.gd")


PROBE_AUTOLOAD = "GBTruthProbe"
DRIVER_AUTOLOAD = "GBTruthDriver"


DEFAULT_SETTLE_FRAMES = 90


GB_ENTRY_ATTEMPTS = ("ui_accept", "gb_action", "gb_jump")


DEFAULT_DWELL_FRAMES = 12


DEFAULT_WATCH_FRAMES = 120


DEFAULT_MAX_CYCLES = 40


DEFAULT_SERIES_INTERVAL = 8


DEFAULT_LEVEL_TIMEOUT = 300.0


H_MODE_MS_PER_FRAME = 2.6


def truth_scratch_root() -> Path:


    env = os.environ.get("GB_TRUTH_SCRATCH")
    if env:
        return Path(env)
    try:
        module = adapters.load_tool_module(adapters.CHECK_RUNTIME)
        return Path(module.ascii_safe_scratch_root("gb_truth_scratch"))
    except Exception:
        if os.name == "posix":
            base = Path("/tmp/swe-game") if Path("/data2").is_dir() else Path("/tmp")
            return base / "gb_truth_scratch"
        drive = os.environ.get("SystemDrive", "C:") + os.sep
        return Path(drive) / "gb_truth_scratch"


@dataclass
class TruthPrep:


    scratch: ScratchPrep | None
    driver_inject: Any = None
    imported: ImportResult | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return (
            self.scratch is not None
            and self.scratch.scratch.ok
            and self.scratch.inject is not None
            and self.scratch.inject.ok
            and self.driver_inject is not None
            and self.driver_inject.ok
            and self.imported is not None
            and self.imported.ok
        )

    @property
    def path(self) -> Path | None:
        return self.scratch.path if self.scratch else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "scratch": self.scratch.to_dict() if self.scratch else None,
            "driver_inject": self.driver_inject.to_dict() if self.driver_inject else None,
            "import": self.imported.to_dict() if self.imported else None,
        }


def prepare(
    project: str | os.PathLike[str],
    dest: str | os.PathLike[str] | None = None,
    *,
    do_import: bool = True,
) -> TruthPrep:


    src = find_project_root(project)
    out = Path(dest) if dest is not None else truth_scratch_root() / src.name

    if not GB_PROBE.is_file():
        return TruthPrep(None, detail=f"the canonical probe is not on disk: {GB_PROBE}")
    if not TRUTH_DRIVER.is_file():
        return TruthPrep(None, detail=f"the truth driver is not on disk: {TRUTH_DRIVER}")

    prep = prepare_scratch(
        src,
        out,
        script_src=GB_PROBE,
        autoload_name=PROBE_AUTOLOAD,
        do_import=False,
    )
    if not prep.scratch.ok or prep.inject is None or not prep.inject.ok:
        return TruthPrep(prep, detail=prep.inject.detail if prep.inject else prep.scratch.detail)

    driver = inject_autoload(
        prep.path, TRUTH_DRIVER, DRIVER_AUTOLOAD, where="back"
    )
    if not driver.ok:
        return TruthPrep(prep, driver, detail=driver.detail)

    imported = cold_import(prep.path) if do_import else None
    return TruthPrep(
        prep,
        driver,
        imported,
        detail=(imported.detail if imported else "import skipped by request"),
    )


@dataclass
class LevelRun:


    declared_scene: str
    record: dict[str, Any] = field(default_factory=dict)
    returncode: int = 0
    seconds: float = 0.0
    timed_out: bool = False
    launch_failed: str = ""
    log_tail: str = ""

    @property
    def reported(self) -> bool:
        return bool(self.record)


def run_level(
    scratch: Path,
    scene: str,
    out_dir: Path,
    *,
    probe_sha: str,
    settle: int = DEFAULT_SETTLE_FRAMES,
    dwell: int = DEFAULT_DWELL_FRAMES,
    watch: int = DEFAULT_WATCH_FRAMES,
    max_cycles: int = DEFAULT_MAX_CYCLES,
    series_interval: int = DEFAULT_SERIES_INTERVAL,
    timeout: float = DEFAULT_LEVEL_TIMEOUT,
    interface: Any = None,
    interface_path: Path | None = None,
    behavior_plan: dict[str, Any] | None = None,
) -> LevelRun:


    out_dir.mkdir(parents=True, exist_ok=True)
    record_path = out_dir / "truth_level.json"
    if record_path.exists():
        record_path.unlink()

    worst_case = settle * (1 + len(GB_ENTRY_ATTEMPTS)) + max_cycles * 2 * dwell + watch
    from ..interface.runtime import runtime_args

    cmd = build_command(
        RenderMode.H,
        scratch,
        scene=scene,
        quit_after=2 * worst_case,
        user_args=[
            "--gb-truth-out", str(record_path),
            "--gb-truth-level", scene,
            "--gb-truth-settle", str(settle),
            "--gb-truth-dwell", str(dwell),
            "--gb-truth-watch", str(watch),
            "--gb-truth-cycles", str(max_cycles),
            "--gb-truth-series-interval", str(series_interval),
            "--gb-truth-probe-sha", probe_sha,
            "--gb-truth-behavior-plan", json.dumps(behavior_plan or {}),
        ] + (runtime_args(interface, interface_path) if interface and interface_path else []),
    )
    from ..render.modes import scaled_timeout

    res = run(cmd, cwd=scratch, timeout=scaled_timeout(timeout), env=build_env(RenderMode.H))
    (out_dir / "run.log").write_text(res.log[-40000:], encoding="utf-8")

    record: dict[str, Any] = {}
    if record_path.is_file():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8", errors="replace"))
        except ValueError as exc:
            record = {}
            res.stderr += f"\nthe driver's record did not parse: {exc}"
    return LevelRun(
        declared_scene=scene,
        record=record,
        returncode=res.returncode,
        seconds=res.seconds,
        timed_out=res.timed_out,
        launch_failed=res.launch_failed,
        log_tail=res.log.strip()[-2000:],
    )


def level_truth(run_result: LevelRun) -> LevelTruth:


    rec = run_result.record
    if not rec:
        scene_error = (scene_load_failure(run_result.log_tail, run_result.declared_scene)
                       if not run_result.timed_out and not run_result.launch_failed else None)
        why = (
            "our deadline fired before the driver wrote anything"
            if run_result.timed_out
            else run_result.launch_failed
            or f"the driver wrote no record (rc={run_result.returncode})"
        )
        return LevelTruth(
            declared_scene=run_result.declared_scene,
            stop_reason="scene_load_failed" if scene_error else "no_record",
            errors=(scene_error or why, run_result.log_tail[-400:]),
        )

    base = rec.get("base") or {}
    groups: dict[str, GroupTruth] = {}
    world_all = base.get("world_positions") or {}
    for name, entry in (base.get("groups") or {}).items():
        if not isinstance(entry, dict):
            continue
        norm = tuple(NormPoint.from_any(p) for p in entry.get("positions") or ())
        world = tuple(
            v for v in (Vec3.from_any(p) for p in world_all.get(name) or ()) if v is not None
        )
        groups[str(name)] = GroupTruth(
            total=int(entry.get("total", 0)),
            alive=int(entry.get("alive", 0)),
            normalised=norm,
            world=world,
        )

    player = base.get("player") or {}
    spawn = Vec3.from_any(player.get("position")) if player.get("exists") else None
    bounds = Bounds.from_dict(base.get("bounds"))
    spawn_norm = _normalise(spawn, bounds) if spawn else None

    scene = str(base.get("scene_path", ""))
    series = tuple(SeriesSample.from_dict(s) for s in rec.get("series") or ())
    numeric_raw = rec.get("numeric") or {}
    if not numeric_raw:
        for sample in reversed(series):
            if sample.numeric:
                numeric_raw = sample.numeric
                break
    source_raw = rec.get("numeric_source") or {}
    if not source_raw:
        for sample in reversed(rec.get("series") or []):
            src = (sample or {}).get("numeric_source") or {}
            if src:
                source_raw = src
                break
    return LevelTruth(
        declared_scene=run_result.declared_scene,
        scene=scene,


        reached=bool(scene) and scene == run_result.declared_scene,
        entry_method=str(rec.get("entry_method", "none")),
        stop_reason=str(rec.get("stop_reason", "")),
        groups=groups,
        bounds=bounds,
        spawn=spawn,
        spawn_norm=spawn_norm,
        bound_actions=tuple(str(a) for a in base.get("bound_actions") or ()),
        collect=CollectProbe.from_dict(rec.get("collect")),
        cycles=tuple(CollectCycle.from_dict(c) for c in rec.get("cycles") or ()),
        cycles_capped=bool(rec.get("cycles_capped", False)),
        goal=GoalProbe.from_dict(rec.get("goal")),
        series=series,
        numeric={
            str(k): float(v)
            for k, v in numeric_raw.items()
            if isinstance(v, (int, float))
        },
        numeric_source={
            str(k): str(v)
            for k, v in source_raw.items()
            if str(v) in {"declared", "guess"}
        },
        errors=tuple(str(e) for e in rec.get("errors") or ()),
    )


def _normalise(point: Vec3, bounds: Bounds) -> NormPoint:


    if not bounds.valid:
        return NormPoint(0.0, 0.0, False)
    plane = bounds.plane
    if plane == "xz":
        px, py = point.x, point.z
        lo_x, lo_y = bounds.min3.x, bounds.min3.z
        hi_x, hi_y = bounds.max3.x, bounds.max3.z
    elif plane == "zy":
        px, py = point.z, point.y
        lo_x, lo_y = bounds.min3.z, bounds.min3.y
        hi_x, hi_y = bounds.max3.z, bounds.max3.y
    else:
        px, py = point.x, point.y
        lo_x, lo_y = bounds.min3.x, bounds.min3.y
        hi_x, hi_y = bounds.max3.x, bounds.max3.y
    width = hi_x - lo_x
    height = hi_y - lo_y
    if width <= 1e-6 or height <= 1e-6:
        return NormPoint(0.0, 0.0, False)
    return NormPoint(
        min(1.0, max(0.0, (px - lo_x) / width)),
        min(1.0, max(0.0, (py - lo_y) / height)),
        True,
    )


@dataclass
class LivenessReading:


    actions: tuple[ActionLiveness, ...] = ()
    no_input_no_win: bool | None = None
    no_input_stop_reason: str = ""
    note: str = ""


def _measured_ms_per_frame(runs: Sequence[LevelRun]) -> tuple[float, float]:


    samples = [
        run.seconds * 1000.0 / max(1, int(run.record.get("driver_frames") or 0))
        for run in runs
        if run.reported and int(run.record.get("driver_frames") or 0) > 0
    ]
    if not samples:
        return H_MODE_MS_PER_FRAME, H_MODE_MS_PER_FRAME
    samples.sort()
    median = samples[len(samples) // 2]
    return max(H_MODE_MS_PER_FRAME, median), max(H_MODE_MS_PER_FRAME, samples[-1])


def run_liveness(
    project: str | os.PathLike[str],
    actions: Sequence[str],
    *,
    scratch: Path | None = None,
    ms_per_frame_p50: float = H_MODE_MS_PER_FRAME,
    ms_per_frame_p95: float = H_MODE_MS_PER_FRAME,
    interface: Any = None,
) -> "LivenessReading":


    from ..render.modes import wall_timeout_s
    from ..routes.budget import L0_IDLE_FRAMES, WALL_SAFETY, derive
    from ..routes.runner import RouteSession, authorable_actions, run_l0
    from ..routes.schema import Budget, Goal, Route, RouteStart

    actions = authorable_actions(list(actions))
    if not actions:
        return LivenessReading(
            note="the project binds none of the authorable declared actions, so "
                 "there was nothing to sweep",
        )

    route = Route(
        route_id="truth/L0",
        tier=0,
        start=RouteStart(level=0),
        goal=Goal(""),
        budget=Budget(steps=0, frames=L0_IDLE_FRAMES),
        notes="synthetic L0 used only to drive the liveness sweep; never scored as a route",
    )
    budget = derive(0, ms_per_frame_p50, ms_per_frame_p95, declared_frames=L0_IDLE_FRAMES)


    budget.wall_timeout_s = wall_timeout_s(L0_IDLE_FRAMES, ms_per_frame_p95, WALL_SAFETY)
    session = RouteSession(
        project,
        scratch=(scratch if scratch is not None else truth_scratch_root() / (
            find_project_root(project).name + "_liveness"
        )),
        interface=interface,
    )
    prep = session.prepare()
    if not prep.ok:
        return LivenessReading(
            note="the liveness scratch copy could not be prepared: "
                 f"{prep.to_dict().get('import')}",
        )

    result = run_l0(session, route, budget, list(actions))
    readings = tuple(
        ActionLiveness(
            action=j.action,
            live=j.live,
            via=j.via,
            idle_baseline_id=j.idle_baseline_id,
            detail=j.detail,
        )
        for j in result.liveness
    )


    judged = {r.action for r in readings}
    missing = tuple(
        ActionLiveness(
            action=a,
            live=False,
            via="none",
            idle_baseline_id=result.idle_baseline_id,
            detail="this action's run produced no trace, so it was never compared "
                   "against the idle control",
        )
        for a in actions
        if a not in judged
    )
    return LivenessReading(
        actions=readings + missing,
        no_input_no_win=result.no_input_no_win,
        no_input_stop_reason=result.no_input_stop_reason,
        note=result.detail,
    )


def take_snapshot(
    project: str | os.PathLike[str],
    *,
    dest: str | os.PathLike[str] | None = None,
    settle: int = DEFAULT_SETTLE_FRAMES,
    dwell: int = DEFAULT_DWELL_FRAMES,
    watch: int = DEFAULT_WATCH_FRAMES,
    max_cycles: int = DEFAULT_MAX_CYCLES,
    timeout: float = DEFAULT_LEVEL_TIMEOUT,
    with_liveness: bool = True,
    max_levels: int | None = None,
    interface: Any = None,
    task_id: str | None = None,
) -> TruthSnapshot:


    if interface is None:
        from ..interface import load_submission_interface

        interface = load_submission_interface(project)
    src = interface.project_root
    from ..assertions.behavior_contracts import probe_plan
    behavior_plan = probe_plan(task_id)
    collect_frames = int(behavior_plan.get("collect", {}).get("frames", dwell))


    max_cycles = int(behavior_plan.get("collect", {}).get("cycles", max_cycles))
    dwell = max(dwell, collect_frames)
    started = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    notes: list[str] = []

    probe_sha = adapters.sha256_file(GB_PROBE) if GB_PROBE.is_file() else ""
    driver_sha = adapters.sha256_file(TRUTH_DRIVER) if TRUTH_DRIVER.is_file() else ""
    harness = harness_provenance(require=False)

    declared = [level.scene for level in interface.levels]
    if not declared:
        return TruthSnapshot(
            project=src.name,
            interface=interface.provenance(),
            probe_sha256=probe_sha,
            driver_sha256=driver_sha,
            harness=harness,
            taken_at=started,
            notes=((interface.report.checks.get("gb_levels") or {}).get(
                "detail", "no declared level"
            ),),
        )
    if max_levels is not None:
        declared = declared[:max_levels]

    prep = prepare(src, dest)
    if not prep.ok or prep.path is None:
        return TruthSnapshot(
            project=src.name,
            interface=interface.provenance(),
            probe_sha256=probe_sha,
            driver_sha256=driver_sha,
            harness=harness,
            taken_at=started,
            notes=(
                "no level was read because the scratch copy could not be prepared: "
                + prep.detail,
            ),


            read_failure="the scratch copy could not be prepared: " + prep.detail,
        )

    out_root = prep.path.parent / (src.name + "_truth_io")
    from ..interface.runtime import write_runtime_interface
    interface_path = write_runtime_interface(interface, prep.path.parent / "_interface_io")
    levels: list[LevelTruth] = []
    runs: list[LevelRun] = []
    for index, scene in enumerate(declared):
        result = run_level(
            prep.path,
            scene,
            out_root / f"level_{index:02d}",
            probe_sha=probe_sha,
            settle=settle,
            dwell=dwell,
            watch=watch,
            max_cycles=max_cycles,
            timeout=timeout,
            interface=interface,
            interface_path=interface_path,
            behavior_plan=behavior_plan,
        )
        runs.append(result)
        levels.append(level_truth(result))


    engine = ""
    for index, scene in enumerate(declared):
        rec_path = out_root / f"level_{index:02d}" / "truth_level.json"
        if rec_path.is_file():
            try:
                engine = str(
                    json.loads(rec_path.read_text(encoding="utf-8", errors="replace")).get(
                        "engine", ""
                    )
                )
            except ValueError:
                engine = ""
            if engine:
                break

    bound = _bound_actions(levels)
    liveness: tuple[ActionLiveness, ...] = ()
    reading = LivenessReading(note="")
    if with_liveness:
        p50, p95 = _measured_ms_per_frame(runs)
        reading = run_liveness(
            src, bound, ms_per_frame_p50=p50, ms_per_frame_p95=p95,
            interface=interface,
        )
        liveness, note = reading.actions, reading.note
        notes.append(
            f"liveness deadline derived from this project's own structural pass: "
            f"{p50:.1f} ms/frame median, {p95:.1f} ms/frame worst"
        )
        if note:
            notes.append("liveness sweep: " + note)
    else:
        notes.append(
            "the liveness sweep was skipped by request; U2 has no reading in this snapshot"
        )

    return TruthSnapshot(
        project=src.name,
        interface=interface.provenance(),
        levels=tuple(levels),
        liveness=liveness,
        declared_actions=bound,
        endings=declared_endings(interface),
        no_input_no_win=reading.no_input_no_win,
        no_input_stop_reason=reading.no_input_stop_reason,
        probe_sha256=probe_sha,
        driver_sha256=driver_sha,
        harness=harness,
        engine=engine,
        taken_at=started,
        notes=tuple(notes),
    )


def declared_endings(interface: Any) -> dict[str, str]:


    return dict(interface.endings.scenes)


def _bound_actions(levels: Sequence[LevelTruth]) -> tuple[str, ...]:


    seen: set[str] = set()
    for lv in levels:
        seen.update(lv.bound_actions)
    return tuple(a for a in ACTIONS if a in seen)
