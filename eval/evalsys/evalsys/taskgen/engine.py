


from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Iterator, Sequence

from ..routes.agent import NullAgent, Op, ScriptedAgent, mash_ops
from ..routes.budget import derive
from ..routes.schema import Budget, Goal, Milestone, Route, RouteStart
from .modes import Mode, needs_ops


GRANT_PROBE_FRAMES = 1100
MASH_PROBE_FRAMES = 1100
EARLY_CLEAR_SLACK = 180
PREFERRED_GODOT = "/opt/godot451-bin/godot"
DEFAULT_SCRATCH = Path("/tmp/swe-game/gb_taskgen_scratch")


def ensure_taskgen_scratch() -> Path:

    current = os.environ.get("GB_SCRATCH_ROOT", "").strip()
    root = Path(current) if current else DEFAULT_SCRATCH
    root.mkdir(parents=True, exist_ok=True)
    os.environ["GB_SCRATCH_ROOT"] = str(root)


    routes = root / "gb_routes_scratch"
    routes.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("GB_ROUTES_SCRATCH", str(routes))
    try:
        from ..probe import inject

        inject.SCRATCH_ROOT = root
    except Exception:
        pass
    return root


_SCRATCH_ENV = ("GB_SCRATCH_ROOT", "GB_ROUTES_SCRATCH", "GB_TRUTH_SCRATCH")
KEEP_SCRATCH_ENV = "GB_KEEP_TASKGEN_SCRATCH"


@contextmanager
def isolated_taskgen_scratch(tag: str = "eval") -> Iterator[Path]:


    previous = {name: os.environ.get(name) for name in _SCRATCH_ENV}
    try:
        from ..probe import inject as _inject
    except Exception:
        _inject = None
    previous_inject_root = getattr(_inject, "SCRATCH_ROOT", None)
    parent = ensure_taskgen_scratch()
    safe_tag = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in tag)[:60]
    root = Path(tempfile.mkdtemp(prefix=f"{safe_tag or 'eval'}-", dir=parent))
    os.environ["GB_SCRATCH_ROOT"] = str(root)
    os.environ["GB_ROUTES_SCRATCH"] = str(root / "gb_routes_scratch")
    os.environ["GB_TRUTH_SCRATCH"] = str(root / "gb_truth_scratch")
    for name in ("GB_ROUTES_SCRATCH", "GB_TRUTH_SCRATCH"):
        Path(os.environ[name]).mkdir(parents=True, exist_ok=True)
    if _inject is not None:
        _inject.SCRATCH_ROOT = root
    try:
        yield root
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if _inject is not None and previous_inject_root is not None:
            _inject.SCRATCH_ROOT = previous_inject_root
        if os.environ.get(KEEP_SCRATCH_ENV, "").strip() not in {"1", "true", "yes"}:
            shutil.rmtree(root, ignore_errors=True)


def prepare_session(session: Any, out: dict[str, Any]) -> bool:


    attempts: list[dict[str, Any]] = []
    prep = session.prepare()
    attempts.append(prep.to_dict() if hasattr(prep, "to_dict") else {"ok": bool(getattr(prep, "ok", False))})
    if not getattr(prep, "ok", False):
        prep = session.prepare()
        attempts.append(prep.to_dict() if hasattr(prep, "to_dict") else {"ok": bool(getattr(prep, "ok", False))})
    out["prepare"] = {
        "ok": bool(getattr(prep, "ok", False)),
        "attempts": attempts,
        "driver_error": str(getattr(session, "driver_error", "") or ""),
    }
    return bool(getattr(prep, "ok", False))


@contextmanager
def fresh_user_data() -> Iterator[Path]:


    parent = Path(os.environ.get("GB_SCRATCH_ROOT") or DEFAULT_SCRATCH)
    parent.mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("XDG_DATA_HOME")
    with tempfile.TemporaryDirectory(prefix="feature-user-", dir=parent) as directory:
        os.environ["XDG_DATA_HOME"] = directory
        try:
            yield Path(directory)
        finally:
            if previous is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = previous


def godot_available() -> Path | None:
    env = os.environ.get("GODOT_BIN", "").strip()
    candidates = [env] if env else []
    candidates.extend(
        (
            PREFERRED_GODOT,
            "/usr/local/bin/godot",
            "/opt/godot-4.5.1-stable_linux.x86_64/Godot_v4.5.1-stable_linux.x86_64",
        )
    )
    for raw in candidates:
        path = Path(raw)
        if raw and path.is_file():
            return path
    return None


def godot_version(bin_path: Path | None = None) -> str:
    path = bin_path or godot_available()
    if path is None:
        return ""
    try:
        proc = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or proc.stderr or "").strip().splitlines()[0] if proc.returncode == 0 else ""


SELF_CLEAR_SETTLE_FRAMES = 0


def self_clear_route(
    *,
    task_id: str,
    ops: Sequence[Op],
    predicate: str,
    frames: int,
    milestones: Sequence[Milestone] = (),
    feature_demo: bool = False,
) -> Route:
    return Route(
        route_id=f"{task_id}/L5/self_clear",
        tier=5,
        start=RouteStart(level=0, settle_frames=SELF_CLEAR_SETTLE_FRAMES),
        goal=Goal(predicate, observations=list(milestones)),
        budget=Budget(steps=max(1, len(ops)), frames=max(frames, 120)),
        authored_solution={"ops": [op.to_dict() for op in ops]},
        notes="taskgen self-clear; ops authored by the submission, not a gold tape",
        continue_after_failure=feature_demo,
    )


def matched_horizon_null(ops: Sequence[Op]) -> list[Op]:


    return [Op(op="wait", frames=op.frames) for op in ops]


def idle_route(task_id: str, frames: int = 420, predicate: str = "") -> Route:
    return Route(
        route_id=f"{task_id}/L0/no_input_no_clear",
        tier=0,
        start=RouteStart(level=0, settle_frames=6),
        goal=Goal(predicate),
        budget=Budget(steps=1, frames=frames),
        authored_solution={"ops": [{"op": "wait", "frames": frames}]},
        scored=False,
        notes="taskgen null control",
    )


def grant_probe_route(task_id: str, frames: int, predicate: str) -> Route:
    return Route(
        route_id=f"{task_id}/L0/grant_probe_idle",
        tier=0,
        start=RouteStart(level=0, settle_frames=6),
        goal=Goal(predicate),
        budget=Budget(steps=1, frames=frames),
        authored_solution={"ops": [{"op": "wait", "frames": frames}]},
        scored=False,
        notes=(
            f"idle {frames} frames; long enough to see volley_break's "
            "frame-900 argv grant, which L0=420 cannot"
        ),
    )


def mash_probe_route(task_id: str, frames: int, predicate: str) -> Route:
    return Route(
        route_id=f"{task_id}/L0/extended_mash",
        tier=0,
        start=RouteStart(level=0, settle_frames=6),
        goal=Goal(predicate),
        budget=Budget(steps=max(1, (frames + 599) // 600), frames=frames),
        authored_solution={"ops": []},
        scored=False,
        notes=(
            f"hold every declared extra and park every analog axis at max "
            f"from frame 0 for {frames} frames; the negative control for a "
            "declared win button"
        ),
    )


def run_extended_mash(
    session: Any,
    *,
    extra_actions: Sequence[str],
    predicate: str,
    frames: int = MASH_PROBE_FRAMES,
    task_id: str = "mash",
    extra_axes: Sequence[Any] = (),
) -> dict[str, Any]:


    extras = tuple(name for name in extra_actions if name)
    axes = tuple(axis for axis in extra_axes if axis)
    if not extras and not axes:
        return {
            "applicable": False,
            "won": False,
            "reason": "no extended actions or analog axes declared",
            "extras": [],
            "analog_axes": [],
        }
    from ..routes.budget import derive

    ops = mash_ops(extras, frames, extra_axes=axes)
    route = mash_probe_route(task_id, frames, predicate)
    budget = derive(0, 21.6, declared_steps=len(ops), declared_frames=frames)
    reading = session.run(
        route,
        ScriptedAgent(ops, name="extended_mash"),
        budget,
        run_tag="extended_mash",
        override_ops=ops,
        expose_cmdline_plan=False,
    )
    payload = _reading_dict(reading)
    won = _mash_won(payload)
    return {
        "applicable": True,
        "won": won,
        "extras": list(extras),
        "analog_axes": [
            item.id if hasattr(item, "id") else str(item) for item in axes
        ],
        "frames": frames,
        "reading": payload,
    }


def _mash_won(reading: dict[str, Any]) -> bool:

    return reading_won(reading)


def _stop(reading: Any) -> str:
    if reading is None:
        return ""
    if isinstance(reading, dict):
        return str(reading.get("stop_reason") or "").lower()
    return str(getattr(reading, "stop_reason", "") or "").lower()


WIN_STOP_REASONS = frozenset({"goal_reached", "win", "won", "success", "victory"})


def reading_won(reading: Any) -> bool:

    if reading is None:
        return False
    reached = (
        reading.get("reached") if isinstance(reading, dict)
        else getattr(reading, "reached", False)
    )
    if bool(reached):
        return True
    return _stop(reading) in WIN_STOP_REASONS


_successish = reading_won


def reading_unmeasured(reading: Any) -> bool:


    if not reading:
        return True
    from ..routes.runner import HARNESS_STOPS
    return _stop(reading) in HARNESS_STOPS


def _idle_diverged(env: Any, flagged: Any) -> bool:


    if _successish(flagged) and not _successish(env):
        return True
    env_stop = _stop(env)
    flag_stop = _stop(flagged)
    if flag_stop == env_stop:
        return False
    return flag_stop in {"scene_changed", "goal_reached", "success", "victory"} and env_stop in {
        "budget_frames",
        "ops_exhausted",
        "",
    }


def _early_flag_clear(env: dict[str, Any], flagged: dict[str, Any], slack: int = EARLY_CLEAR_SLACK) -> bool:
    if not (env.get("reached") and flagged.get("reached")):
        return False
    env_frame = int(env.get("goal_frame") or env.get("frames") or 0)
    flag_frame = int(flagged.get("goal_frame") or flagged.get("frames") or 0)
    return env_frame > 0 and flag_frame > 0 and (env_frame - flag_frame) >= slack


def _differential(
    *,
    idle_env: Any,
    idle_flagged: Any,
    ops_env: Any | None = None,
    ops_flagged: Any | None = None,
) -> dict[str, Any]:
    idle_grant = _idle_diverged(idle_env, idle_flagged)
    ops_grant = False
    early = False
    ops_env_d = _reading_dict(ops_env) if ops_env is not None else {}
    ops_flag_d = _reading_dict(ops_flagged) if ops_flagged is not None else {}
    if ops_env is not None and ops_flagged is not None:
        ops_grant = bool(_successish(ops_flagged) and not _successish(ops_env))
        early = _early_flag_clear(ops_env_d, ops_flag_d)
    return {
        "idle_differential_grant": idle_grant,
        "ops_differential_grant": ops_grant,
        "early_flag_clear": early,
        "differential_grant": bool(idle_grant or ops_grant or early),
    }


def run_self_play(
    project: Path,
    *,
    mode: Mode,
    ops: Sequence[Op],
    predicate: str,
    interface: Any,
    milestones: Sequence[Milestone] = (),
    feature_demo: bool = False,
) -> dict[str, Any]:

    from ..routes.budget import L0_IDLE_FRAMES
    from ..routes.runner import RouteSession

    ensure_taskgen_scratch()
    bin_path = godot_available()
    if bin_path is None:
        return {"ran": False, "reason": "no Godot binary (GODOT_BIN unset)"}

    session = RouteSession(project, interface=interface)
    out: dict[str, Any] = {
        "ran": True,
        "godot": str(bin_path),
        "godot_version": godot_version(bin_path),
        "scratch": os.environ.get("GB_SCRATCH_ROOT", ""),
        "grant_probe_frames": GRANT_PROBE_FRAMES,
    }
    prepare_session(session, out)

    def run_case(*args, **kwargs):
        with fresh_user_data() if feature_demo else nullcontext():
            return session.run(*args, **kwargs)

    idle_budget = derive(0, 21.6, declared_steps=1, declared_frames=L0_IDLE_FRAMES)
    idle_reading = run_case(
        idle_route(project.name, L0_IDLE_FRAMES, predicate),
        NullAgent(),
        idle_budget,
        run_tag="taskgen_null",
        expose_cmdline_plan=False,
    )
    out["null"] = _reading_dict(idle_reading)

    probe = grant_probe_route(project.name, GRANT_PROBE_FRAMES, predicate)
    probe_budget = derive(0, 21.6, declared_steps=1, declared_frames=GRANT_PROBE_FRAMES)
    idle_env = run_case(
        probe,
        NullAgent(),
        probe_budget,
        run_tag="taskgen_idle_env",
        expose_cmdline_plan=False,
    )
    idle_flagged = run_case(
        probe,
        NullAgent(),
        probe_budget,
        run_tag="taskgen_idle_flag",
        expose_cmdline_plan=True,
    )
    out["idle_env"] = _reading_dict(idle_env)
    out["idle_flagged"] = _reading_dict(idle_flagged)

    honest = flagged = None
    if needs_ops(mode) and ops:
        frames = sum(op.frames for op in ops) + 120
        route = self_clear_route(
            task_id=project.name,
            ops=ops,
            predicate=predicate,
            frames=frames,
            milestones=milestones,
            feature_demo=feature_demo,
        )
        budget = derive(
            5, 21.6, declared_steps=len(ops), declared_frames=frames
        )
        honest = run_case(
            route,
            ScriptedAgent(ops, name="submission_ops"),
            budget,
            run_tag="taskgen_ops_env",
            override_ops=ops,
            expose_cmdline_plan=False,
        )
        flagged = run_case(
            route,
            ScriptedAgent(ops, name="submission_ops_flagged"),
            budget,
            run_tag="taskgen_ops_flag",
            override_ops=ops,
            expose_cmdline_plan=True,
        )
        out["ops_env"] = _reading_dict(honest)
        out["ops_flagged"] = _reading_dict(flagged)


        null_ops = matched_horizon_null(ops)
        matched_null = run_case(
            route,
            ScriptedAgent(null_ops, name="evaluator_matched_null"),
            budget,
            run_tag="taskgen_ops_matched_null",
            override_ops=null_ops,
            expose_cmdline_plan=False,
        )
        out["matched_null"] = _reading_dict(matched_null)
        out["matched_null_ops"] = [op.to_dict() for op in null_ops]
    elif needs_ops(mode):


        out["ops_pair_unavailable"] = (
            "the submission supplied no usable ops, so the ops half of the "
            "anti-grant differential could not be driven"
        )
    out.update(
        _differential(
            idle_env=idle_env,
            idle_flagged=idle_flagged,
            ops_env=honest,
            ops_flagged=flagged,
        )
    )
    extras = tuple(getattr(interface, "extended_action_ids", ()) or ())
    axes = tuple(getattr(interface, "analog_axes", ()) or ())
    with fresh_user_data() if feature_demo else nullcontext():
        out["extended_mash"] = run_extended_mash(
            session,
            extra_actions=extras,
            extra_axes=axes,
            predicate=predicate,
            task_id=project.name,
        )
    return out


def run_gold_replay(
    project: Path,
    routes_file: Path,
    interface: Any,
    *,
    expose_cmdline_plan: bool = False,
) -> dict[str, Any]:


    from ..pipeline import run_routes
    from ..routes.budget import derive
    from ..routes.runner import RouteSession, validate_route_on_gold
    from ..routes.schema import load_route_file

    ensure_taskgen_scratch()
    if not routes_file.is_file():
        return {"ran": False, "reason": f"no route file: {routes_file}"}
    if godot_available() is None:
        return {"ran": False, "reason": "no Godot binary (GODOT_BIN unset)"}
    if expose_cmdline_plan:
        return run_routes(project, routes_file=routes_file, agent="scripted", interface=interface)

    session = RouteSession(project, interface=interface)
    prepare_record: dict[str, Any] = {}
    prepare_session(session, prepare_record)
    routes = load_route_file(routes_file)
    passed_ids: list[str] = []
    failed_ids: list[str] = []
    readings: list[dict[str, Any]] = []
    for route in routes:
        budget = derive(
            route.tier,
            21.6,
            declared_steps=route.budget.steps,
            declared_frames=route.budget.frames,
        )
        validation = validate_route_on_gold(
            route, session, budget, expose_cmdline_plan=False
        )
        payload = {
            "route_id": route.route_id,
            "tier": route.tier,
            "ok": bool(validation.ok),
            "detail": validation.detail,
        }
        if validation.reading is not None:
            payload["reading"] = _reading_dict(validation.reading)
        readings.append(payload)
        if validation.ok:
            passed_ids.append(route.route_id)
        else:
            failed_ids.append(route.route_id)
    return {
        "ran": True,
        "baseline": "honest",
        "expose_cmdline_plan": False,
        "routes_file": str(routes_file),
        "rt1_passed": passed_ids,
        "rt1_failed": failed_ids,
        "readings": readings,
        **prepare_record,
    }


def corpus_genuine_clear(game_id: str) -> dict[str, Any]:


    from ..tasks import certificate_path, eval_root

    if not game_id:
        return {"present": False, "reason": "no game_id"}
    inner = _genuine_clear_object(certificate_path(game_id))
    if inner is None:
        return {
            "present": False,
            "reason": (
                "certificate has no genuine_clear attestation from recertify; "
                "a clean scan is not a genuine clear"
            ),
        }
    if inner.get("produced_by") != "recertify":
        return {
            "present": False,
            "reason": "genuine_clear was not produced by recertify",
        }
    if inner.get("l5_passed") is not True:
        return {
            "present": False,
            "reason": "genuine_clear does not record a passing L5",
        }
    return {"present": True, "reason": "", "l5_route_id": inner.get("l5_route_id")}


def _genuine_clear_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    inner = payload.get("genuine_clear")
    if isinstance(inner, dict):
        return inner
    if payload.get("schema") == "gamebench.genuine-clear.v1":
        return payload
    return None


@fresh_user_data()
def run_bugfix_gates(
    project: Path,
    routes_file: Path,
    *,
    interface: Any,
    game_id: str = "",
    genuine_override: dict[str, Any] | None = None,
    scratch_root: Path | None = None,
) -> dict[str, Any]:


    from ..routes.budget import L0_IDLE_FRAMES, derive
    from ..routes.runner import RouteSession, validate_route_on_gold
    from ..routes.schema import load_route_file

    ensure_taskgen_scratch()
    bin_path = godot_available()
    if bin_path is None:
        return {"ran": False, "reason": "no Godot binary (GODOT_BIN unset)"}

    genuine = dict(genuine_override) if genuine_override is not None else corpus_genuine_clear(game_id)
    session = RouteSession(
        project,
        interface=interface,
        scratch=(scratch_root / "project_routes") if scratch_root else None,
        io_root=(scratch_root / "route_io") if scratch_root else None,
    )
    predicate = clear_predicate(interface)
    out: dict[str, Any] = {
        "ran": True,
        "godot": str(bin_path),
        "godot_version": godot_version(bin_path),
        "scratch": str(scratch_root) if scratch_root else os.environ.get("GB_SCRATCH_ROOT", ""),
        "grant_probe_frames": GRANT_PROBE_FRAMES,
        "genuine_clear": genuine,
    }
    prepare_session(session, out)

    idle_budget = derive(0, 21.6, declared_steps=1, declared_frames=L0_IDLE_FRAMES)
    with fresh_user_data():
        idle_reading = session.run(
            idle_route(project.name, L0_IDLE_FRAMES, predicate),
            NullAgent(),
            idle_budget,
            run_tag="bugfix_null",
            expose_cmdline_plan=False,
        )
    out["null"] = _reading_dict(idle_reading)

    probe = grant_probe_route(project.name, GRANT_PROBE_FRAMES, predicate)
    probe_budget = derive(0, 21.6, declared_steps=1, declared_frames=GRANT_PROBE_FRAMES)
    with fresh_user_data():
        idle_env = session.run(
            probe, NullAgent(), probe_budget,
            run_tag="bugfix_idle_env", expose_cmdline_plan=False,
        )
    with fresh_user_data():
        idle_flagged = session.run(
            probe, NullAgent(), probe_budget,
            run_tag="bugfix_idle_flag", expose_cmdline_plan=True,
        )
    out["idle_env"] = _reading_dict(idle_env)
    out["idle_flagged"] = _reading_dict(idle_flagged)

    extras = tuple(getattr(interface, "extended_action_ids", ()) or ())
    axes = tuple(getattr(interface, "analog_axes", ()) or ())
    with fresh_user_data():
        out["extended_mash"] = run_extended_mash(
            session,
            extra_actions=extras,
            extra_axes=axes,
            predicate=predicate,
            task_id=project.name,
        )

    if not routes_file.is_file():
        out.update(_differential(idle_env=idle_env, idle_flagged=idle_flagged))
        out["gold"] = {
            "ran": False,
            "reason": f"no route file: {routes_file}",
            "genuine_clear": genuine,
        }
        return out

    routes = load_route_file(routes_file)
    l5s = [r for r in routes if int(getattr(r, "tier", -1)) == 5]
    passed_ids: list[str] = []
    failed_ids: list[str] = []
    gold_readings: list[dict[str, Any]] = []
    honest_l5 = None
    flagged_l5 = None
    for route in routes:
        budget = derive(
            route.tier,
            21.6,
            declared_steps=route.budget.steps,
            declared_frames=route.budget.frames,
        )
        with fresh_user_data():
            honest = validate_route_on_gold(
                route, session, budget, expose_cmdline_plan=False
            )
        payload = {
            "route_id": route.route_id,
            "tier": route.tier,
            "ok": bool(honest.ok),
            "detail": honest.detail,
            "baseline": "honest",
        }
        if honest.reading is not None:
            payload["reading"] = _reading_dict(honest.reading)
        gold_readings.append(payload)
        if honest.ok:
            passed_ids.append(route.route_id)
        else:
            failed_ids.append(route.route_id)
        if l5s and route.route_id == l5s[-1].route_id:
            honest_l5 = honest.reading
            with fresh_user_data():
                flagged = validate_route_on_gold(
                    route, session, budget, expose_cmdline_plan=True
                )
            flagged_l5 = flagged.reading
            out["ops_flagged"] = _reading_dict(flagged_l5)
            out["ops_env"] = _reading_dict(honest_l5)

    out.update(
        _differential(
            idle_env=idle_env,
            idle_flagged=idle_flagged,
            ops_env=honest_l5,
            ops_flagged=flagged_l5,
        )
    )
    out["gold"] = {
        "ran": True,
        "baseline": "honest",
        "expose_cmdline_plan": False,
        "routes_file": str(routes_file),
        "genuine_clear": genuine,
        "rt1_passed": passed_ids,
        "rt1_failed": failed_ids,
        "readings": gold_readings,
    }
    return out


def run_corpus_gates(
    project: Path,
    routes_file: Path,
    *,
    interface: Any,
    include_challenge_routes: bool = False,
) -> dict[str, Any]:


    from ..pipeline import routes_path
    from ..routes.agent import Op
    from ..routes.budget import L0_IDLE_FRAMES, derive
    from ..routes.runner import RouteSession, validate_route_on_gold
    from ..routes.schema import load_route_file

    ensure_taskgen_scratch()
    bin_path = godot_available()
    if bin_path is None:
        return {"ran": False, "reason": "no Godot binary (GODOT_BIN unset)"}
    project = Path(project)
    if not routes_file.is_file():
        guessed = routes_path(project.name)
        routes_file = guessed if guessed.is_file() else routes_file
    if not routes_file.is_file():
        return {"ran": False, "reason": f"no route file: {routes_file}"}

    routes = load_route_file(routes_file)
    l5s = [r for r in routes if int(getattr(r, "tier", -1)) == 5]
    if not l5s:
        return {"ran": False, "reason": f"no tier-5 route in {routes_file}"}
    l5 = l5s[-1]
    raw_ops = (l5.authored_solution or {}).get("ops") or []
    ops = [Op.from_dict(item) for item in raw_ops]
    predicate = str(getattr(l5.goal, "predicate", "") or "")
    frames = int(getattr(l5.budget, "frames", 0) or 0)

    session = RouteSession(project, interface=interface)
    session.prepare()
    out: dict[str, Any] = {
        "ran": True,
        "godot": str(bin_path),
        "godot_version": godot_version(bin_path),
        "scratch": os.environ.get("GB_SCRATCH_ROOT", ""),
        "grant_probe_frames": GRANT_PROBE_FRAMES,
        "project": str(project),
        "l5_route_id": l5.route_id,
        "l5_ops": len(ops),
        "l5_declared_frames": frames,
        "predicate": predicate,
    }

    null_budget = derive(0, 21.6, declared_steps=1, declared_frames=L0_IDLE_FRAMES)
    null_reading = session.run(
        idle_route(project.name, L0_IDLE_FRAMES, predicate),
        NullAgent(),
        null_budget,
        run_tag="prove_null",
        expose_cmdline_plan=False,
    )
    out["null"] = _reading_dict(null_reading)

    probe = grant_probe_route(project.name, GRANT_PROBE_FRAMES, predicate)
    probe_budget = derive(0, 21.6, declared_steps=1, declared_frames=GRANT_PROBE_FRAMES)
    idle_env = session.run(
        probe, NullAgent(), probe_budget,
        run_tag="prove_idle_env", expose_cmdline_plan=False,
    )
    idle_flagged = session.run(
        probe, NullAgent(), probe_budget,
        run_tag="prove_idle_flag", expose_cmdline_plan=True,
    )
    out["idle_env"] = _reading_dict(idle_env)
    out["idle_flagged"] = _reading_dict(idle_flagged)

    extras = tuple(getattr(interface, "extended_action_ids", ()) or ())
    axes = tuple(getattr(interface, "analog_axes", ()) or ())
    out["extended_mash"] = run_extended_mash(
        session,
        extra_actions=extras,
        extra_axes=axes,
        predicate=predicate,
        task_id=project.name,
    )

    ops_budget = derive(
        5, 21.6, declared_steps=l5.budget.steps, declared_frames=l5.budget.frames
    )
    honest = session.run(
        l5,
        ScriptedAgent(ops, name="gold_ops_env"),
        ops_budget,
        run_tag="prove_ops_env",
        override_ops=ops,
        expose_cmdline_plan=False,
    )


    gold_l5 = validate_route_on_gold(l5, session, ops_budget)
    flagged = gold_l5.reading
    out["ops_env"] = _reading_dict(honest)
    out["ops_flagged"] = _reading_dict(flagged)
    out.update(
        _differential(
            idle_env=idle_env,
            idle_flagged=idle_flagged,
            ops_env=honest,
            ops_flagged=flagged,
        )
    )

    gold_targets = [r for r in routes if int(getattr(r, "tier", -1)) == 0]
    if include_challenge_routes:
        gold_targets.extend(
            r for r in routes if int(getattr(r, "tier", -1)) not in {0, 5}
        )
    passed_ids: list[str] = []
    failed_ids: list[str] = []
    gold_readings: list[dict[str, Any]] = []
    if gold_l5.ok:
        passed_ids.append(l5.route_id)
    else:
        failed_ids.append(l5.route_id)
    gold_readings.append({
        "route_id": l5.route_id,
        "tier": 5,
        "ok": bool(gold_l5.ok),
        "detail": gold_l5.detail,
        "reading": _reading_dict(flagged),
        "note": "same run as ops_flagged; certify path (argv flag on)",
    })
    for gold_route in gold_targets:
        budget = derive(
            gold_route.tier,
            21.6,
            declared_steps=gold_route.budget.steps,
            declared_frames=gold_route.budget.frames,
        )
        validation = validate_route_on_gold(gold_route, session, budget)
        payload = {
            "route_id": gold_route.route_id,
            "tier": gold_route.tier,
            "ok": bool(validation.ok),
            "detail": validation.detail,
        }
        if validation.reading is not None:
            payload["reading"] = _reading_dict(validation.reading)
        gold_readings.append(payload)
        if validation.ok:
            passed_ids.append(gold_route.route_id)
        else:
            failed_ids.append(gold_route.route_id)

    out["gold"] = {
        "ran": True,
        "routes_file": str(routes_file),
        "rt1_passed": passed_ids,
        "rt1_failed": failed_ids,
        "readings": gold_readings,
    }
    if session.prep is not None:
        try:
            from ..probe.inject import drop_scratch

            drop_scratch(session.prep.path)
        except Exception:
            pass
    return out


def _reading_dict(reading: Any) -> dict[str, Any]:
    if reading is None:
        return {}
    if hasattr(reading, "to_dict"):
        return reading.to_dict()
    return {
        "route_id": getattr(reading, "route_id", ""),
        "reached": bool(getattr(reading, "reached", False)),
        "stop_reason": str(getattr(reading, "stop_reason", "")),
        "detail": str(getattr(reading, "detail", "")),
    }


def clear_predicate(interface: Any) -> str:


    del interface
    return "whole_game_clear()"
