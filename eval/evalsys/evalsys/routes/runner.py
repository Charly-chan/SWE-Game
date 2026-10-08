


from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from evalsys.harness import face_path
from evalsys.engine.scene_load import scene_load_failure

GB_PROBE = face_path("gb_probe.gd")
ROUTE_DRIVER = face_path("gb_route_driver.gd")

from evalsys.probe.inject import (
    ScratchPrep,
    find_project_root,
    inject_autoload,
    prepare_scratch,
)
from evalsys.render.modes import RenderMode, build_command, build_env, route_replay_fixed_fps
from evalsys.render.render_cost import first_playable_level
from evalsys.routes import budget as budget_mod
from evalsys.routes.agent import (
    FORBIDDEN_ACTIONS,
    MAX_OP_FRAMES,
    NullAgent,
    Op,
    OpValidation,
    RouteAgent,
    ScriptedAgent,
    validate_ops,
)
from evalsys.routes.budget import L0_IDLE_FRAMES, TIER_WEIGHTS, RouteBudget
from evalsys.routes.schema import (
    BASELINE_PREVIOUS_ROW,
    CANONICAL_GROUPS,
    Route,
    assert_no_leak,
    player_view,
    validate_predicate,
)


_AUTHOR_EXEMPT_AGENTS = (ScriptedAgent,)
_AUTHOR_EXEMPT_NAMES = frozenset({"ScriptedAgent", "PlanAuthoringAgent"})


def _agent_is_author_exempt(agent: object) -> bool:
    return (
        isinstance(agent, _AUTHOR_EXEMPT_AGENTS)
        or type(agent).__name__ in _AUTHOR_EXEMPT_NAMES
    )
from evalsys.verdict import (
    Attribution,
    Interval,
    Item,
    Verdict,
    failed,
    inconclusive,
    passed,
    score_items,
    skipped,
    unmeasurable,
)

HERE = Path(__file__).resolve().parent


def routes_scratch_root() -> Path:


    env = os.environ.get("GB_ROUTES_SCRATCH")
    if env:
        return Path(env)
    try:
        from evalsys.ocard import adapters as _adapters

        module = _adapters.load_tool_module(_adapters.CHECK_RUNTIME)
        return Path(module.ascii_safe_scratch_root("gb_routes_scratch"))
    except Exception:
        if os.name == "posix":
            if Path("/data2").is_dir():
                return Path("/tmp/swe-game/gb_routes_scratch")
            return Path("/tmp/gb_routes_scratch")
        drive = os.environ.get("SystemDrive", "C:") + os.sep
        return Path(drive) / "gb_scratch" / "gb_routes_scratch"


HARNESS_STOPS = frozenset(
    {
        "timeout",
        "driver_error",
        "no_report",
        "launch_failed",
        "agent_unavailable",
        "agent_timeout",
        "budget_ineligible",
        "plan_refused",
    }
)


SUBMISSION_PREFIX_STOPS = frozenset(
    {


        "setup_success_ending",
        "setup_failure_ending",
        "setup_scene_changed",
    }
)

SUBMISSION_STOPS = frozenset(
    {"no_player", "scene_load_failed", "level_unreachable", "missing_level_manifest"}
) | SUBMISSION_PREFIX_STOPS


INTERFACE_GAP_STOPS = frozenset({"no_ending_vocabulary", "reference_observation_missing"})

CANONICAL_DEVICE_ROLES = frozenset(
    name.removeprefix("gb_") for name in CANONICAL_GROUPS
)


#: and denominator as `unmeasurable` with harness attribution.  It is


EVALUATOR_SETUP_STOPS = frozenset({"setup_failed"})


RUN_COMPLETE_KEYS = frozenset({"run_complete"})


@dataclass(frozen=True)
class EndingVocabulary:


    success: list[str]
    failure: list[str]
    gap: str
    """Empty when usable; otherwise why the outcome cannot be read."""
    continuation: tuple[str, ...] = ()
    """Declared per-level wins that may precede a narrower run-complete scene."""

    @property
    def usable(self) -> bool:
        return not self.gap and bool(self.success)


def write_driver(scratch_dir: str | os.PathLike[str]) -> Path:

    if not ROUTE_DRIVER.is_file():
        raise FileNotFoundError(f"route harness driver is not on disk: {ROUTE_DRIVER}")
    out = Path(scratch_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "gb_route_driver.gd"
    path.write_text(ROUTE_DRIVER.read_text(encoding="utf-8"), encoding="utf-8")
    return path


SIGNATURE_TOLERANCES: dict[str, float] = {
    "position": 0.5,
    "velocity": 0.5,
    "groups": 0.5,
    "numeric": 0.5,
    "audio_events": 5.0,
    "anim": 1.0,
    "node_count": 0.5,
    "visible_count": 0.5,
    "text": 0.5,
}


LIVENESS_INSUFFICIENT_ALONE: frozenset[str] = frozenset({"audio_events", "anim"})


SIGNATURE_COMPONENTS = tuple(SIGNATURE_TOLERANCES)


def signature_from_row(row: dict[str, Any]) -> dict[str, Any]:

    sig = row.get("s") or {}
    return {
        "position": (
            float(row.get("px", 0.0)),
            float(row.get("py", 0.0)),
            float(row.get("pz", 0.0)),
        ),
        "velocity": (
            float(row.get("vx", 0.0)),
            float(row.get("vy", 0.0)),
            float(row.get("vz", 0.0)),
        ),
        "groups": dict(row.get("g") or {}),
        "numeric": dict(sig.get("numeric") or {}),
        "audio_events": int(sig.get("audio_events", 0)),
        "anim": int(sig.get("anim", 0)),
        "node_count": int(sig.get("node_count", 0)),
        "visible_count": int(sig.get("visible_count", 0)),
        "text": int(sig.get("text", 0)),
    }


def _dist(name: str, a: Any, b: Any) -> float:
    if name in ("position", "velocity"):
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
    if name in ("groups", "numeric"):
        keys = set(a) | set(b)
        if not keys:
            return 0.0
        return max(abs(float(a.get(k, 0)) - float(b.get(k, 0))) for k in keys)
    if name in ("anim", "text"):
        return 0.0 if a == b else 1.0
    return abs(float(a) - float(b))


@dataclass
class Liveness:


    action: str
    live: bool
    via: str
    components: dict[str, dict[str, float]] = field(default_factory=dict)
    idle_baseline_id: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "live": self.live,
            "via": self.via,
            "components": self.components,
            "idle_baseline_id": self.idle_baseline_id,
            "detail": self.detail,
        }


def liveness(
    action_signature: dict[str, Any],
    idle_signature: dict[str, Any],
    *,
    action: str = "",
    tolerances: dict[str, float] | None = None,
    idle_baseline_id: str = "",
) -> Liveness:


    tol = dict(SIGNATURE_TOLERANCES if tolerances is None else tolerances)
    components: dict[str, dict[str, float]] = {}
    best: tuple[float, str] = (0.0, "none")
    insufficient_hits: list[str] = []
    for name in SIGNATURE_COMPONENTS:
        limit = tol.get(name, 0.5)
        d = _dist(name, action_signature.get(name), idle_signature.get(name))
        exceeded = d > limit
        components[name] = {
            "delta": round(d, 6),
            "tolerance": limit,
            "exceeded": exceeded,
        }
        if not exceeded:
            continue
        if name in LIVENESS_INSUFFICIENT_ALONE:
            insufficient_hits.append(name)
            continue
        ratio = d / limit if limit else d
        if ratio > best[0]:
            best = (ratio, name)
    live = best[1] != "none"
    if live:
        detail = (
            f"{action or 'action'} differs from the idle control on {best[1]} "
            f"(delta {components[best[1]]['delta']} > {components[best[1]]['tolerance']})"
        )
    elif insufficient_hits:
        names = ", ".join(insufficient_hits)
        detail = (
            f"{action or 'action'} differs from the idle control on {names}, "
            "which cannot determine liveness alone"
        )
    else:
        detail = (
            f"{action or 'action'} is indistinguishable from the idle control on "
            "every signature component; whatever the world did, it did without "
            "being asked"
        )
    return Liveness(
        action=action,
        live=live,
        via=best[1],
        components=components,
        idle_baseline_id=idle_baseline_id,
        detail=detail,
    )


def authorable_actions(actions: Sequence[str]) -> list[str]:

    return [action for action in actions if action not in FORBIDDEN_ACTIONS]


def liveness_over_trace(
    action_rows: Sequence[dict[str, Any]],
    idle_rows: Sequence[dict[str, Any]],
    *,
    action: str = "",
    idle_baseline_id: str = "",
) -> Liveness:


    count = min(len(action_rows), len(idle_rows))
    if count == 0:
        return Liveness(
            action=action,
            live=False,
            via="none",
            idle_baseline_id=idle_baseline_id,
            detail=(
                f"{action or 'action'} produced no paired trace against the idle "
                "control, so it was never compared"
            ),
        )
    best: Liveness | None = None
    best_ratio = -1.0
    for index in range(count):
        judged = liveness(
            signature_from_row(action_rows[index]),
            signature_from_row(idle_rows[index]),
            action=action,
            idle_baseline_id=idle_baseline_id,
        )
        ratio = 0.0
        if judged.live and judged.via in judged.components:
            comp = judged.components[judged.via]
            limit = float(comp.get("tolerance") or 0.0)
            delta = float(comp.get("delta") or 0.0)
            ratio = delta / limit if limit else delta
        if best is None or ratio > best_ratio:
            best = judged
            best_ratio = ratio
    assert best is not None
    return best


def state_delta(rows: Sequence[dict[str, Any]]) -> float:

    if len(rows) < 2:
        return 0.0
    first = signature_from_row(rows[0])
    last = signature_from_row(rows[-1])
    return round(
        max(_dist(name, first[name], last[name]) for name in SIGNATURE_COMPONENTS), 6
    )


@dataclass
class RouteReading:


    route_id: str
    tier: int
    reached: bool = False
    segment_clean: bool = False
    used_required: bool = False
    steps: int = 0
    frames: int = 0
    deaths: int = 0
    stall_frames: int = 0
    replans: int = 0
    injects: int = 0
    wall_ms: float = 0.0
    state_delta: float = 0.0
    idle_baseline_id: str = ""
    stop_reason: str = "no_report"

    clean_clear: bool = False


    success_before_all_levels: bool = False
    goal_frame: int = -1
    devices_required: list[str] = field(default_factory=list)
    devices_used: list[str] = field(default_factory=list)
    devices_unresolved: list[str] = field(default_factory=list)
    missing_groups: list[str] = field(default_factory=list)
    refusals: list[dict[str, Any]] = field(default_factory=list)
    physics_frames_at_end: int = 0
    process_frames_at_end: int = 0
    mode: str = RenderMode.H.value
    errors: list[str] = field(default_factory=list)
    detail: str = ""
    milestones_reached: list[str] = field(default_factory=list)


    milestone_frames: dict[str, int] = field(default_factory=dict)
    observations_reached: list[str] = field(default_factory=list)


    observations_triggered: list[str] = field(default_factory=list)
    invariants_failed: list[str] = field(default_factory=list)
    interface: dict[str, Any] = field(default_factory=dict)


    numeric_resolution: dict[str, str] = field(default_factory=dict)

    @property
    def all_three(self) -> bool:
        return self.reached and self.segment_clean and self.used_required

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "tier": self.tier,
            "reached": self.reached,
            "segment_clean": self.segment_clean,
            "used_required": self.used_required,
            "steps": self.steps,
            "frames": self.frames,
            "deaths": self.deaths,
            "stall_frames": self.stall_frames,
            "replans": self.replans,
            "injects": self.injects,
            "wall_ms": round(self.wall_ms, 1),
            "state_delta": self.state_delta,
            "idle_baseline_id": self.idle_baseline_id,
            "stop_reason": self.stop_reason,
            "clean_clear": self.clean_clear,
            "success_before_all_levels": self.success_before_all_levels,
            "goal_frame": self.goal_frame,
            "devices_required": self.devices_required,
            "devices_used": self.devices_used,
            "devices_unresolved": self.devices_unresolved,
            "missing_groups": self.missing_groups,
            "refusals": self.refusals,
            "physics_frames_at_end": self.physics_frames_at_end,
            "process_frames_at_end": self.process_frames_at_end,
            "frames_note": (
                "frames is the trace row count; physics_frames_at_end keeps "
                "counting while the tree is paused for the agent and is not the "
                "game's clock; process_frames_at_end is Engine.get_process_frames() "
                "and can diverge from frames when --fixed-fps is off"
            ),
            "mode": self.mode,
            "errors": self.errors[:20],
            "detail": self.detail,
            "milestones_reached": self.milestones_reached,
            "milestone_frames": dict(self.milestone_frames),
            "observations_reached": self.observations_reached,
            "observations_triggered": self.observations_triggered,
            "invariants_failed": self.invariants_failed,
            "interface": dict(self.interface),
            "numeric_resolution": dict(self.numeric_resolution),
        }


def numeric_resolution_from_rows(
    rows: Sequence[Mapping[str, Any]], declared_numeric: Mapping[str, str]
) -> dict[str, str]:

    from ..interface.loader import classify_numeric_value

    seen: set[str] = set()
    for row in rows:
        bag = row.get("n") or {}
        if isinstance(bag, Mapping):
            seen.update(str(key) for key in bag)
    out: dict[str, str] = {}
    for slot, value in declared_numeric.items():
        if str(slot) not in seen:
            out[str(slot)] = "none"
        elif classify_numeric_value(str(value))["driver_resolves"]:
            out[str(slot)] = "declared"
        else:
            out[str(slot)] = "guess"
    return out


def observation_from_row(
    row: dict[str, Any],
    group_totals: dict[str, Any],
    *,
    start_player: dict[str, float] | None = None,
    origin: dict[str, float] | None = None,
    extent: dict[str, float] | None = None,
    numeric: dict[str, float] | None = None,
    start_numeric: dict[str, float] | None = None,
    start_groups: dict[str, int] | None = None,
    start_devices: dict[str, Any] | None = None,
    whole_game_clear: bool = False,
) -> dict[str, Any]:

    census = row.get("g") or {}
    groups = {
        g: {"alive": int(census.get(g, 0)), "total": int(group_totals.get(g, 0))}
        for g in set(census) | set(group_totals)
    }
    player = None
    if "px" in row:
        player = {
            "x": float(row.get("px", 0.0)),
            "y": float(row.get("py", 0.0)),
            "z": float(row.get("pz", 0.0)),
        }
    bag = dict(numeric or {})
    sig = row.get("s") or {}
    if isinstance(sig, dict) and sig.get("numeric"):
        bag.update({str(k): float(v) for k, v in sig["numeric"].items()
                    if isinstance(v, (int, float))})
    row_n = row.get("n") or {}
    if isinstance(row_n, dict):
        bag.update({str(k): float(v) for k, v in row_n.items()
                    if isinstance(v, (int, float))})
    return {
        "groups": groups,
        "overlaps": row.get("o") or {},
        "player": player,
        "numeric": bag,
        "start_numeric": start_numeric or {},
        "start_groups": start_groups or {},
        "start_player": start_player or player,
        "origin": origin or {},
        "extent": extent or {},
        "whole_game_clear": whole_game_clear or bool(row.get("wgc", False)),


        "levels_visited": (int(row["lv"]) if "lv" in row else None),
        "devices": dict(row.get("d") or {}),
        "start_devices": dict(start_devices or {}),
        "contacts": list(row.get("c") or []),
        "standing_on": str(row.get("so", "")),
    }


def _previous_row_observation(
    observation: dict[str, Any], previous_row: dict[str, Any]
) -> dict[str, Any]:


    prev_groups = {
        str(k): int(v)
        for k, v in (previous_row.get("g") or {}).items()
        if isinstance(v, (int, float))
    }
    prev_numeric = {
        str(k): float(v)
        for k, v in (previous_row.get("n") or {}).items()
        if isinstance(v, (int, float))
    }
    prev_sig = previous_row.get("s") or {}
    if isinstance(prev_sig, dict) and isinstance(prev_sig.get("numeric"), dict):
        for k, v in prev_sig["numeric"].items():
            if isinstance(v, (int, float)):
                prev_numeric.setdefault(str(k), float(v))
    return {**observation, "start_groups": prev_groups, "start_numeric": prev_numeric,
            "previous_level": previous_row.get("lv")}


def _trace_frame(report: dict[str, Any]) -> dict[str, Any]:

    rows: list[dict[str, Any]] = list(report.get("rows") or [])
    start_player = None
    for row in rows:
        if "px" in row:
            start_player = {
                "x": float(row.get("px", 0.0)),
                "y": float(row.get("py", 0.0)),
                "z": float(row.get("pz", 0.0)),
            }
            break
    first = rows[0] if rows else {}
    start_numeric = {
        str(k): float(v)
        for k, v in (first.get("n") or {}).items()
        if isinstance(v, (int, float))
    }
    start_groups = {
        str(k): int(v)
        for k, v in (first.get("g") or {}).items()
        if isinstance(v, (int, float))
    }
    bounds = report.get("bounds") or {}
    mn = bounds.get("min") or {}
    mx = bounds.get("max") or {}
    origin = {
        "x": float(mn.get("x", 0.0)),
        "y": float(mn.get("y", 0.0)),
        "z": float(mn.get("z", 0.0)),
    }
    extent = {
        "x": float(mx.get("x", 0.0)) - float(mn.get("x", 0.0)),
        "y": float(mx.get("y", 0.0)) - float(mn.get("y", 0.0)),
        "z": float(mx.get("z", 0.0)) - float(mn.get("z", 0.0)),
    }
    return {
        "start_player": start_player,
        "start_numeric": start_numeric,
        "start_groups": start_groups,
        "origin": origin,
        "extent": extent,
        "start_devices": dict(first.get("d") or {}),
    }


def predicate_frame_from_report(
    predicate: str, report: Mapping[str, Any], *, min_frame: int = 0
) -> int:


    check = validate_predicate(predicate)
    if not check.ok or check.predicate is None:
        return -1
    materialized = dict(report)
    rows = [row for row in (materialized.get("rows") or []) if isinstance(row, dict)]
    materialized["rows"] = rows
    totals = dict(materialized.get("group_totals") or {})
    frame = _trace_frame(materialized)
    for row in rows:
        observation = observation_from_row(
            row,
            totals,
            start_player=frame["start_player"],
            start_numeric=frame["start_numeric"],
            start_groups=frame["start_groups"],
            origin=frame["origin"],
            extent=frame["extent"],
            start_devices=frame["start_devices"],
        )
        reached, missing = check.predicate.evaluate_detail(observation)
        if reached and not missing and int(row.get("f", -1)) >= min_frame:
            return int(row.get("f", -1))
    return -1


_LOG_ERROR = re.compile(r"^\s*(SCRIPT ERROR|ERROR:|Parse Error)", re.M)


_LOG_BENIGN = re.compile(
    r"audio|ALSA|PulseAudio|as `root`|GODOT_SILENCE|"
    r"resources still in use at exit|ObjectDB instances leaked|"
    r"RID allocations of type .* (?:were )?leaked at exit|"
    r"^ERROR: Screen space AA is currently unavailable on the Compatibility renderer\.?$|"
    r"^\s*ERROR: BUG:",
    re.I | re.M,
)


def script_errors(log: str) -> list[str]:


    return [
        line.strip()[:200]
        for line in (log or "").splitlines()
        if _LOG_ERROR.search(line) and not _LOG_BENIGN.search(line)
    ]


def reading_from_report(
    route: Route,
    report: dict[str, Any],
    *,
    wall_ms: float = 0.0,
    mode: str = RenderMode.H.value,
    idle_baseline_id: str = "",
    refusals: Sequence[dict[str, Any]] = (),
    log: str = "",
    declared_numeric: Mapping[str, str] | None = None,
) -> RouteReading:

    rows: list[dict[str, Any]] = list(report.get("rows") or [])
    totals = dict(report.get("group_totals") or {})
    stop_reason = str(report.get("stop_reason") or "no_report")

    reached = False
    goal_frame = -1
    missing: list[str] = []
    frame = _trace_frame(report)
    milestone_checks = [validate_predicate(p.predicate) for p in route.goal.milestones]
    invariant_checks = [validate_predicate(p.predicate) for p in route.goal.invariants]
    observation_checks = [validate_predicate(p.predicate) for p in route.goal.observations]
    trigger_checks = [
        validate_predicate(p.trigger) if p.trigger else None
        for p in route.goal.observations
    ]
    invariant_failed: list[str] = []
    milestone_index = 0
    milestones_reached: list[str] = []
    milestone_frames: dict[str, int] = {}
    observations_reached: list[str] = []
    observations_triggered: list[str] = []
    check = validate_predicate(route.goal.predicate)
    if check.ok and check.predicate is not None:
        previous_row: dict[str, Any] | None = None
        for row in rows:
            observation = observation_from_row(
                row,
                totals,
                start_player=frame["start_player"],
                start_numeric=frame["start_numeric"],
                start_groups=frame["start_groups"],
                origin=frame["origin"],
                extent=frame["extent"],
                start_devices=frame["start_devices"],
            )


            step_observation = (
                _previous_row_observation(observation, previous_row)
                if previous_row is not None
                else None
            )
            previous_row = row
            for invariant, invariant_check in zip(route.goal.invariants, invariant_checks):
                if invariant_check.ok and invariant_check.predicate is not None:
                    if invariant.baseline == BASELINE_PREVIOUS_ROW:
                        if step_observation is None:
                            continue
                        invariant_view = step_observation
                    else:
                        invariant_view = observation
                    invariant_ok, invariant_missing = invariant_check.predicate.evaluate_detail(
                        invariant_view
                    )
                    missing.extend(invariant_missing)
                    if not invariant_ok and invariant.name not in invariant_failed:
                        invariant_failed.append(invariant.name)
            for rubric_observation, observation_check, trigger_check in zip(
                route.goal.observations, observation_checks, trigger_checks
            ):


                if (
                    trigger_check is not None
                    and trigger_check.ok
                    and trigger_check.predicate is not None
                    and rubric_observation.name not in observations_triggered
                ):
                    triggered, trigger_missing = trigger_check.predicate.evaluate_detail(
                        observation
                    )
                    missing.extend(trigger_missing)
                    if triggered:
                        observations_triggered.append(rubric_observation.name)
                if rubric_observation.name in observations_reached:
                    continue


                if (
                    trigger_check is not None
                    and rubric_observation.name not in observations_triggered
                ):
                    continue
                if observation_check.ok and observation_check.predicate is not None:
                    if rubric_observation.baseline == BASELINE_PREVIOUS_ROW:
                        if step_observation is None:
                            continue


                        if (
                            row.get("wgc")
                            or ("lv" in row and row["lv"] != step_observation.get("previous_level"))
                        ):
                            continue
                        view = step_observation
                    else:
                        view = observation
                    observed, observation_missing = observation_check.predicate.evaluate_detail(
                        view
                    )
                    missing.extend(observation_missing)
                    if observed:
                        observations_reached.append(rubric_observation.name)
            if milestone_index < len(milestone_checks):
                milestone = milestone_checks[milestone_index]
                if milestone.ok and milestone.predicate is not None:
                    milestone_spec = route.goal.milestones[milestone_index]
                    if milestone_spec.baseline == BASELINE_PREVIOUS_ROW:
                        if step_observation is None:
                            continue
                        milestone_view = step_observation
                    else:
                        milestone_view = observation
                    milestone_ok, milestone_missing = milestone.predicate.evaluate_detail(
                        milestone_view
                    )
                    missing.extend(milestone_missing)
                    if milestone_ok:
                        milestone_name = milestone_spec.name
                        milestones_reached.append(milestone_name)
                        milestone_frames[milestone_name] = int(row.get("f", -1))
                        milestone_index += 1


                if milestone_index < len(milestone_checks):
                    continue
            value, miss = check.predicate.evaluate_detail(observation)
            missing.extend(miss)
            if value:
                reached = True
                goal_frame = int(row.get("f", -1))
                break
    elif route.tier == 0 and not route.goal.predicate.strip():
        reached = stop_reason != "goal_reached"
    else:

        reached = not route.goal.predicate.strip()

    if reached and route.goal.end_predicate.strip() and rows:
        end_check = validate_predicate(route.goal.end_predicate)
        if not end_check.ok or end_check.predicate is None:
            reached = False
        else:
            end_ok, end_miss = end_check.predicate.evaluate_detail(
                observation_from_row(
                    rows[-1], totals,
                    start_player=frame["start_player"],
                    start_numeric=frame["start_numeric"],
                    start_groups=frame["start_groups"],
                    origin=frame["origin"],
                    extent=frame["extent"],
                    start_devices=frame["start_devices"],
                )
            )
            missing.extend(end_miss)
            reached = bool(end_ok)
            if reached and goal_frame < 0:
                goal_frame = int(rows[-1].get("f", -1))

    contacts = dict(report.get("device_contacts") or {})
    index = dict(report.get("device_index") or {})
    used = [d for d in route.required_devices if int(contacts.get(d, 0)) > 0]
    unresolved = [d for d in route.required_devices if d not in index]
    used_required = len(used) == len(route.required_devices)


    # genuinely unresolved names fail-closed, and do not use observations made

    observed_before_goal: set[str] = set()
    for row in rows:
        frame_number = int(row.get("f", -1))
        if goal_frame >= 0 and frame_number > goal_frame:
            continue
        for role, count in dict(row.get("g") or {}).items():
            if isinstance(count, (int, float)) and not isinstance(count, bool) and count > 0:
                observed_before_goal.add(str(role))
        for role, overlaps in dict(row.get("o") or {}).items():
            if overlaps:
                observed_before_goal.add(str(role))
        for selector, state in dict(row.get("d") or {}).items():
            if isinstance(state, Mapping) and state.get("present") is True:
                observed_before_goal.add(str(selector))
        for slot, value in dict(row.get("n") or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                observed_before_goal.add("numeric:" + str(slot))
    missing = [name for name in missing if name not in observed_before_goal]

    errors = [str(e) for e in report.get("errors") or []] + script_errors(log)
    injects = int(report.get("injects", 0))
    declared_injects = 1 if route.start.has_inject else 0


    left_the_segment = stop_reason == "scene_changed" and not reached
    segment_clean = (
        not errors
        and not invariant_failed
        and int(report.get("out_of_bounds", 0)) == 0
        and injects <= declared_injects
        and stop_reason not in HARNESS_STOPS
        and not left_the_segment
    )

    return RouteReading(
        route_id=route.route_id,
        tier=route.tier,
        reached=reached,
        segment_clean=segment_clean,
        used_required=used_required,
        steps=int(report.get("steps", 0)),
        frames=len(rows),
        deaths=int(report.get("deaths", 0)),
        stall_frames=int(report.get("stall_frames", 0)),
        replans=max(0, int(report.get("turns", 0)) - 1),
        injects=injects,
        wall_ms=wall_ms,
        state_delta=state_delta(rows),
        idle_baseline_id=idle_baseline_id,
        stop_reason=stop_reason,
        clean_clear=segment_clean and reached and injects == 0,
        success_before_all_levels=bool(report.get("success_before_all_levels", False)),
        goal_frame=goal_frame,
        devices_required=list(route.required_devices),
        devices_used=used,
        devices_unresolved=unresolved,
        missing_groups=sorted(set(missing)),
        refusals=[dict(r) for r in refusals],
        physics_frames_at_end=int(report.get("physics_frames_at_end", 0)),
        process_frames_at_end=int(report.get("process_frames_at_end", 0)),
        mode=mode,
        errors=errors,
        detail=str(report.get("detail", "")),
        milestones_reached=milestones_reached,
        milestone_frames=milestone_frames,
        observations_reached=observations_reached,
        observations_triggered=observations_triggered,
        invariants_failed=invariant_failed,
        numeric_resolution=numeric_resolution_from_rows(rows, declared_numeric or {}),
    )


def verdict_for(
    route: Route,
    reading: RouteReading,
    weight: float = 1.0,
    *,
    channel: str = "O8",
) -> Item:


    item_id = f"{channel}/L{route.tier}/{route.route_id}"
    evidence = reading.to_dict()

    if reading.stop_reason in HARNESS_STOPS:
        return inconclusive(
            item_id,
            weight=weight,
            detail=(
                f"{reading.stop_reason}: the measurement did not happen for a "
                "reason that belongs to this harness, so it leaves the "
                "denominator rather than scoring the submission zero"
            ),
            attribution=Attribution.HARNESS,
            evidence=evidence,
        )
    if reading.stop_reason in SUBMISSION_PREFIX_STOPS:
        return failed(
            item_id,
            weight=weight,
            detail=(
                f"{reading.stop_reason}: candidate input entered an ending or "
                "left gameplay before the certified measured suffix began; "
                "the required later segment is unreachable"
            ),
            attribution=Attribution.SUBMISSION,
            evidence=evidence,
        )
    if reading.stop_reason in SUBMISSION_STOPS:
        return skipped(
            item_id,
            weight=weight,
            detail=(
                f"{reading.stop_reason}: the submission did not put us in a "
                "position to measure this route; it stays in the denominator"
            ),
            attribution=Attribution.SUBMISSION,
            evidence=evidence,
        )
    if (
        reading.stop_reason == "reference_observation_missing"
        and route.start.at_device
        and "did not resolve from gb groups" in " ".join(reading.errors)
        and route.start.at_device.rsplit("/", 1)[-1].split("#", 1)[0]
        in CANONICAL_DEVICE_ROLES
    ):


        return failed(
            item_id,
            weight=weight,
            detail=(
                f"start device {route.start.at_device!r} did not resolve from the "
                "submission's canonical gb_* runtime groups; the certified route "
                "cannot start because the required candidate device is absent"
            ),
            attribution=Attribution.SUBMISSION,
            evidence={**evidence, "root_cause_class": "submission_start_device_missing"},
        )
    if reading.stop_reason in INTERFACE_GAP_STOPS:
        if reading.stop_reason == "reference_observation_missing":
            gap_detail = (
                "semantic setup needs a reference-side observe() anchor/travel field that "
                "this project does not expose. This is an evaluator observability gap, not "
                "evidence that the submitted mechanic failed"
            )
        else:
            gap_detail = (
                "the run left the last declared level, but gb_levels.json carries no "
                "§L2.6 endings map able to tell victory from defeat. Whether this was a "
                "clear is not expressible by the manifest"
            )
        return unmeasurable(
            item_id,
            weight=weight,
            detail=gap_detail + "; it leaves the denominator rather than scoring zero",
            evidence=evidence,
        )
    if reading.stop_reason in EVALUATOR_SETUP_STOPS:
        return unmeasurable(
            item_id,
            weight=weight,
            detail=(
                f"not measured: {reading.stop_reason} -- the evaluator's own setup "
                "(teleport/inject before the measured ops) lost the player, so the "
                "segment never ran. This is an evaluator-side stop; it leaves the "
                "numerator and denominator and is not charged to the submission"
            ),
            attribution=Attribution.HARNESS,
            evidence={**evidence, "root_cause_class": "evaluator_setup"},
        )
    if (
        reading.devices_unresolved
        and not reading.used_required
        and reading.reached
        and reading.segment_clean
    ):
        unresolved_roles = {
            device.rsplit("/", 1)[-1].split("#", 1)[0]
            for device in reading.devices_unresolved
        }
        if unresolved_roles <= CANONICAL_DEVICE_ROLES:


            return failed(
                item_id,
                weight=weight,
                detail=(
                    "failed required-device gate: canonical semantic devices "
                    f"{', '.join(reading.devices_unresolved)} did not resolve in the "
                    "submission; the corresponding public gb_* role is missing or "
                    "does not contain the required indexed device"
                ),
                attribution=Attribution.SUBMISSION,
                evidence={**evidence, "root_cause_class": "submission_semantic_device_missing"},
            )


        return unmeasurable(
            item_id,
            weight=weight,
            detail=(
                "not measured: reached and clean, but required_devices "
                f"{', '.join(reading.devices_unresolved)} did not resolve to any node "
                "in this submission (no gb_levels.json.device_ids entry and not a "
                "gb_* group), so `used_required` is undecidable here. The route's "
                "device vocabulary is the defect; it leaves the denominator rather "
                "than scoring zero"
            ),
            attribution=Attribution.HARNESS,
            evidence={**evidence, "root_cause_class": "route_device_vocabulary"},
        )
    if reading.all_three:
        return passed(
            item_id,
            weight=weight,
            credit=1.0,
            detail=(
                f"reached in {reading.frames} frames / {reading.steps} steps, "
                "segment clean, required devices engaged"
            ),
            evidence=evidence,
        )


    absent = [
        name
        for name, value in (
            ("reached", reading.reached),
            ("segment_clean", reading.segment_clean),
            ("used_required", reading.used_required),
        )
        if not value
    ]
    milestones = list(getattr(route.goal, "milestones", ()) or ())
    hit = min(len(reading.milestones_reached), len(milestones))
    reached_milestones = milestones[:hit]
    has_checkpoint_gates = bool(reached_milestones) and all(
        milestone.required_devices for milestone in reached_milestones
    )
    used = set(reading.devices_used)
    missing_checkpoint_devices = sorted({
        device
        for milestone in reached_milestones
        for device in milestone.required_devices
        if device not in used
    })
    checkpoint_devices_ok = has_checkpoint_gates and not missing_checkpoint_devices

    if not reading.segment_clean or (not reading.used_required and not checkpoint_devices_ok):
        checkpoint_detail = ""
        if missing_checkpoint_devices:
            checkpoint_detail = (
                " Checkpoint-local required devices missing: "
                + ", ".join(missing_checkpoint_devices)
                + "."
            )
        return failed(
            item_id,
            weight=weight,
            detail=(
                f"failed on {', '.join(absent)} (stop_reason={reading.stop_reason}). "
                "segment_clean and the applicable required-device gate are pass/fail "
                "with no partial credit: they are anti-bypass gates, not a measure "
                f"of progress.{checkpoint_detail}"
            ),
            evidence=evidence,
        )


    if milestones:
        credit = max(0.0, min(1.0, hit / len(milestones)))
        names = ", ".join(reading.milestones_reached) or "none"
        partial_evidence = {
            **evidence,
            "checkpoint_credit": credit,
            "milestones_total": len(milestones),
            "checkpoint_required_devices": sorted({
                d for m in reached_milestones for d in m.required_devices
            }),
            "checkpoint_devices_ok": checkpoint_devices_ok,
        }
        fidelity_note = (
            "This is a fidelity reading -- how far the authored route still "
            "runs here -- not a claim about whether the game is clearable by "
            "other means."
        )


        if hit == 0:
            return failed(
                item_id,
                weight=weight,
                detail=(
                    f"0/{len(milestones)} ordered milestones reached; checkpoint "
                    "credit is 0.000. Clean and passed the applicable binary "
                    "required-device gate, but did not finish (stop_reason="
                    f"{reading.stop_reason}). {fidelity_note}"
                ),
                evidence=partial_evidence,
            )
        return failed(
            item_id,
            weight=weight,
            credit=credit,
            detail=(
                f"partial: {hit}/{len(milestones)} ordered milestones ({names}); "
                f"checkpoint credit is {hit}/{len(milestones)} = {credit:.3f}. "
                "Clean and passed the applicable binary required-device gate, "
                "but did not finish (stop_reason="
                f"{reading.stop_reason}). {fidelity_note}"
            ),
            evidence=partial_evidence,
        )


    return failed(
        item_id,
        weight=weight,
        detail=(
            f"failed on {', '.join(absent)} (stop_reason={reading.stop_reason}). "
            "This route declares no ordered milestones, so there is no partial "
            "credit to award -- add milestones to make its progress legible."
        ),
        evidence=evidence,
    )


def score_o8(
    routes: Sequence[Route],
    readings: Sequence[RouteReading],
) -> tuple[list[Item], Interval]:


    by_id = {r.route_id: r for r in readings}
    per_tier: dict[int, list[Route]] = {}
    for route in routes:
        if not route.scored:
            continue
        per_tier.setdefault(route.tier, []).append(route)

    items: list[Item] = []
    for tier, tier_routes in sorted(per_tier.items()):
        weight = TIER_WEIGHTS.get(tier, 0.0) / max(1, len(tier_routes))
        for route in tier_routes:
            reading = by_id.get(route.route_id)
            if reading is None:
                items.append(
                    skipped(
                        f"O8/L{tier}/{route.route_id}",
                        weight=weight,
                        detail=(
                            "no reading was reported for this route; an "
                            "unreported route is not a passed route"
                        ),
                        attribution=Attribution.SUBMISSION,
                    )
                )
                continue
            items.append(verdict_for(route, reading, weight, channel="O8"))
    return items, score_items(items)


def gold_ok_ids(gold: Sequence[GoldValidation]) -> set[str]:

    return {g.route_id for g in gold if g.ok}


def score_o7_items(
    routes: Sequence[Route],
    readings: Sequence[RouteReading],
    gold: Sequence[GoldValidation],
) -> list[Item]:


    allowed = gold_ok_ids(gold)
    by_id = {r.route_id: r for r in readings}
    l5 = [r for r in routes if r.scored and r.tier == 5 and r.route_id in allowed]
    if not l5:
        return []
    weight = 1.0 / len(l5)
    items: list[Item] = []
    for route in l5:
        reading = by_id.get(route.route_id)
        if reading is None:
            items.append(
                skipped(
                    f"O7/L5/{route.route_id}",
                    weight=weight,
                    detail="no reading was reported for this L5 route",
                    attribution=Attribution.SUBMISSION,
                )
            )
            continue
        items.append(verdict_for(route, reading, weight, channel="O7"))
    return items


def h_items_from_segments(
    routes: Sequence[Route],
    readings: Sequence[RouteReading],
    gold: Sequence[GoldValidation],
) -> list[Item]:


    allowed = gold_ok_ids(gold)
    by_id = {r.route_id: r for r in readings}
    segments = [
        r for r in routes if r.scored and 1 <= r.tier <= 4 and r.route_id in allowed
    ]
    if not segments:
        return []
    weight = 1.0 / len(segments)
    items: list[Item] = []
    for route in segments:
        reading = by_id.get(route.route_id)
        if reading is None:
            items.append(
                skipped(
                    f"O3/H/L{route.tier}/{route.route_id}",
                    weight=weight,
                    detail="no reading was reported for this H segment",
                    attribution=Attribution.SUBMISSION,
                )
            )
            continue
        items.append(verdict_for(route, reading, weight, channel="O3/H"))
    return items


def build_plan(
    route: Route,
    budget: RouteBudget,
    seed_ops: Sequence[Op],
    *,
    scene: str,
    io_dir: str | os.PathLike[str],
    idle: bool = False,
    interactive: bool = False,
    override_frames: int | None = None,
    declared_levels: Sequence[str] = (),
    level_entry: Mapping[str, Any] | None = None,
    success_scenes: Sequence[str] = (),
    failure_scenes: Sequence[str] = (),
    continuation_scenes: Sequence[str] = (),
    ending_vocab: bool = False,
    setup_ops: Sequence[Op] = (),
) -> dict[str, Any]:


    pred = validate_predicate(route.goal.predicate).predicate
    hints = pred.stop_hints() if pred is not None else {}
    return {
        "route_id": route.route_id,
        "level": scene,
        "whole_game": route.tier == 5,
        **({"continue_after_failure": True} if route.continue_after_failure else {}),
        **({"continue_after_success": True} if route.continue_after_success else {}),
        **({"success_scope": route.success_scope} if route.success_scope != "declared_ending" else {}),
        "declared_levels": list(declared_levels),
        "level_entry": dict(level_entry or {}),
        "success_scenes": list(success_scenes),
        "failure_scenes": list(failure_scenes),
        "continuation_scenes": list(continuation_scenes),
        "ending_vocab": bool(ending_vocab),
        "inject": route.start.inject,
        "at_device": route.start.at_device,
        "anchor": route.start.anchor,
        "offset_norm": route.start.offset_norm,
        "settle": route.start.settle_frames,
        "tail": 30,
        "budget_frames": override_frames or budget.frames,
        "ops": [op.to_dict() for op in seed_ops],
        "setup_ops": [op.to_dict() for op in setup_ops],
        "setup_frames": sum(op.frames for op in setup_ops),
        "idle": idle,
        "interactive": interactive,
        "io_dir": str(io_dir).replace("\\", "/"),


        "max_turns": 16384 if interactive else 32,
        "boot_deadline": 900,
        "stop_overlap": hints.get("overlap_groups", []),
        "stop_collect": hints.get("collect_all_groups", []),
        "sig_interval": 10,
    }


@dataclass
class RunArtifacts:


    plan: str = ""
    report: str = ""
    io_dir: str = ""
    log: str = ""


class RouteSession:


    def __init__(
        self,
        project_dir: str | os.PathLike[str],
        *,
        scratch: str | os.PathLike[str] | None = None,
        needs_pixels: bool = False,
        io_root: str | os.PathLike[str] | None = None,
        interface: Any = None,
        dispatch_extended: Sequence[str] | None = None,
        dispatch_axes: Sequence[Any] | None = None,
    ) -> None:
        if interface is None:
            from ..interface import load_submission_interface

            interface = load_submission_interface(project_dir)
        self.interface = interface


        if dispatch_extended is None:
            self._dispatch_extended = tuple(
                getattr(interface, "extended_action_ids", ()) or ()
            )
        else:
            self._dispatch_extended = tuple(
                str(name) for name in dispatch_extended if str(name)
            )
        from .agent import axis_specs

        if dispatch_axes is None:
            self._dispatch_axes = tuple(
                getattr(interface, "analog_axes", ()) or ()
            )
        else:
            self._dispatch_axes = axis_specs(dispatch_axes)
        self.project_dir = interface.project_root
        root = routes_scratch_root()
        self.scratch_dest = Path(scratch) if scratch else root / (
            self.project_dir.name + "_routes"
        )
        self.needs_pixels = needs_pixels
        self.io_root = Path(io_root) if io_root else root / "_route_io"
        self.prep: ScratchPrep | None = None
        self.driver_error = ""
        self.interface_path: Path | None = None

    def extra_actions(self) -> tuple[str, ...]:
        return self._dispatch_extended

    def extra_axes(self) -> tuple[Any, ...]:
        return self._dispatch_axes

    def check_ops(self, ops: Iterable[Any], **kwargs: Any) -> OpValidation:
        return validate_ops(
            ops,
            extra_actions=self._dispatch_extended,
            extra_axes=self._dispatch_axes,
            **kwargs,
        )

    def prepare(self, *, do_import: bool = True) -> ScratchPrep:
        driver_dir = self.scratch_dest.parent / "_route_driver"
        driver = write_driver(driver_dir)
        prep = prepare_scratch(
            self.project_dir,
            self.scratch_dest,
            script_src=driver,
            autoload_name="GBRouteDriver",
            do_import=do_import,
        )
        if prep.ok:
            probe = inject_autoload(prep.path, GB_PROBE, "GBHarnessProbe")
            if not probe.ok:
                prep.inject = probe
            else:
                from dataclasses import replace

                from ..interface.model import ExtendedAction
                from ..interface.runtime import write_runtime_interface

                runtime_iface = self.interface
                live_ids = tuple(getattr(self.interface, "extended_action_ids", ()) or ())
                live_axis_ids = tuple(
                    getattr(self.interface, "analog_axis_ids", ()) or ()
                )
                frozen_axis_ids = tuple(item.id for item in self._dispatch_axes)
                replace_fields: dict[str, Any] = {}
                if live_ids != self._dispatch_extended:


                    replace_fields["extended_actions"] = tuple(
                        ExtendedAction(name, "task-frozen dispatch set")
                        for name in self._dispatch_extended
                    )
                if live_axis_ids != frozen_axis_ids:
                    replace_fields["analog_axes"] = self._dispatch_axes
                if replace_fields:
                    runtime_iface = replace(self.interface, **replace_fields)
                self.interface_path = write_runtime_interface(
                    runtime_iface, prep.path.parent / "_interface_io"
                )
        self.prep = prep
        if not prep.ok:
            self.driver_error = prep.failure_summary()
        return prep

    def level_scene(self, level: int | str) -> str:

        if isinstance(level, str) and level.startswith("res://"):
            return level
        levels = self.level_scenes()
        if not levels:
            return ""
        index = int(level) if isinstance(level, int) else 0
        if index >= len(levels):
            return ""
        return levels[index]

    def level_scenes(self) -> list[str]:
        return [level.scene for level in self.interface.levels]

    def level_entry(self) -> dict[str, Any]:


        return dict(self.interface.level_entry)

    def ending_scenes(self) -> "EndingVocabulary":


        endings = dict(self.interface.endings.scenes)
        if not endings:
            return EndingVocabulary(
                [], [],
                "gb_levels.json declares no `endings` map (§L2.6), so victory and "
                "defeat cannot be told apart from the scene path alone",
            )
        success = [str(endings[key]) for key in self.interface.endings.success]
        failure = [str(endings[key]) for key in self.interface.endings.failure]
        run_complete = list(dict.fromkeys(
            str(value) for key, value in endings.items()
            if str(key).lower() in RUN_COMPLETE_KEYS and str(value).startswith("res://")
        ))
        success = list(dict.fromkeys(success))
        failure = list(dict.fromkeys(failure))
        shared = [s for s in success if s in failure]
        discriminating = [s for s in success if s not in failure]
        if not discriminating:
            return EndingVocabulary(
                success, failure,
                (
                    f"`endings` routes victory and defeat to the same scene(s) "
                    f"{shared}; the manifest cannot discriminate the outcome"
                    if shared
                    else "`endings` declares no success scene, only "
                         f"{failure or 'nothing recognised'}"
                ),
            )


        whole_run = [s for s in run_complete if s not in failure]
        if whole_run:
            continuation = tuple(s for s in discriminating if s not in whole_run)
            return EndingVocabulary(whole_run, failure, "", continuation)
        return EndingVocabulary(discriminating, failure, "")


    def run(
        self,
        route: Route,
        agent: RouteAgent,
        budget: RouteBudget,
        *,
        idle_baseline_id: str = "",
        run_tag: str = "",
        override_ops: Sequence[Op] | None = None,
        override_frames: int | None = None,
        override_setup_ops: Sequence[Op] | None = None,
        expose_cmdline_plan: bool = True,
    ) -> RouteReading:


        started = time.time()
        tag = run_tag or route.route_id.replace("/", "_")

        if not budget.eligible:
            return self._stopped(route, "budget_ineligible", budget, started, budget.detail)
        if self.prep is None:
            self.prepare()
        if self.prep is None or not self.prep.ok:
            return self._stopped(
                route, "launch_failed", budget, started, self.driver_error
            )
        if not agent.provider_available:
            return self._stopped(
                route,
                "agent_unavailable",
                budget,
                started,
                agent.provider_error or "the agent provider is not available",
            )

        scene = self.level_scene(route.start.level)
        if not scene:
            return self._stopped(
                route,
                "missing_level_manifest",
                budget,
                started,
                "gb_levels.json does not declare the route's level; this prompt-declared "
                "submission obligation is a measured failure, not a main-scene fallback",
            )
        io_dir = (self.io_root / tag).resolve()
        io_dir.mkdir(parents=True, exist_ok=True)
        for stale in io_dir.glob("*.json"):
            stale.unlink()

        view = player_view(route)
        leaks = assert_no_leak(view, route)
        if leaks:
            return self._stopped(
                route, "plan_refused", budget, started, f"player view leaks: {leaks}"
            )

        seed_ops: list[Op] = list(override_ops) if override_ops is not None else []
        setup_ops: list[Op] = list(override_setup_ops or [])
        refusals: list[dict[str, Any]] = []
        if override_setup_ops is None:
            raw_setup = route.authored_solution.get("setup_ops", [])
            if isinstance(raw_setup, list) and raw_setup:
                setup_check = self.check_ops(
                    raw_setup, op_cap=None, frame_cap=budget.frames
                )
                if setup_check.refusals:
                    return self._stopped(
                        route,
                        "plan_refused",
                        budget,
                        started,
                        f"route setup ops were refused: {[r.to_dict() for r in setup_check.refusals]}",
                        refusals=[r.to_dict() for r in setup_check.refusals],
                    )
                setup_ops = setup_check.accepted
        if override_ops is None and not isinstance(agent, NullAgent):
            first = agent.propose({}, view, [])
            check = self.check_ops(
                first,


                op_cap=None if _agent_is_author_exempt(agent) else budget.ops,
                frame_cap=override_frames or budget.frames,
            )
            seed_ops = check.accepted
            refusals.extend(r.to_dict() for r in check.refusals)

        vocabulary = self.ending_scenes()
        plan = build_plan(
            route,
            budget,
            seed_ops,
            scene=scene,
            io_dir=io_dir,
            idle=isinstance(agent, NullAgent) and not seed_ops,
            interactive=not isinstance(agent, (NullAgent, ScriptedAgent)),
            override_frames=override_frames,
            declared_levels=self.level_scenes(),
            level_entry=self.level_entry(),
            success_scenes=vocabulary.success,
            failure_scenes=vocabulary.failure,
            continuation_scenes=vocabulary.continuation,
            ending_vocab=vocabulary.usable,
            setup_ops=setup_ops,
        )
        plan_path = io_dir / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        report_path = io_dir / "route_report.json"

        extra_args: list[str] = []
        if os.environ.get("GB_GODOT_VERBOSE", "").strip() in {"1", "true", "yes"}:
            extra_args.append("--verbose")
        extra_args.extend(
            part for part in os.environ.get("GB_GODOT_EXTRA_ARGS", "").split() if part
        )


        windows_cmd = os.name == "nt"
        from ..interface.runtime import runtime_args

        interface_cli = runtime_args(self.interface, self.interface_path) if self.interface_path else []
        if windows_cmd:
            user_args = ["--gb-route-out", "env", "--pl-offline-clock"]
            if expose_cmdline_plan:
                user_args = ["--gb-route-plan", "env", *user_args]
        else:
            user_args = ["--gb-route-out", str(report_path), "--pl-offline-clock"]
            if expose_cmdline_plan:
                user_args.extend(["--gb-route-plan", str(plan_path)])
        user_args.extend(interface_cli)
        cmd = build_command(
            budget.mode,
            self.prep.path,
            scene=scene or None,
            extra_args=extra_args,


            quit_after=None,
            fixed_fps=route_replay_fixed_fps(),
            user_args=user_args,
        )
        rc, log, timed_out = self._drive(
            cmd,
            io_dir,
            agent,
            view,
            budget,
            deadline_s=budget.wall_timeout_s,
        )
        wall_ms = (time.time() - started) * 1000.0
        (io_dir / "run.log").write_text(log[-20000:], encoding="utf-8")

        if timed_out:
            return self._stopped(
                route,
                "timeout",
                budget,
                started,
                f"the {budget.wall_timeout_s}s deadline we derived from our own "
                "measurement fired; that is a coverage gap, not a finding",
                refusals=refusals,
            )
        if not report_path.is_file():
            scene_error = scene_load_failure(log, scene)
            tail = log.strip().splitlines()[-1][:200] if log.strip() else "no output"
            return self._stopped(
                route,
                "scene_load_failed" if scene_error else "no_report",
                budget,
                started,
                scene_error or f"the driver wrote no report (rc={rc}); last log line: {tail}",
                refusals=refusals,
            )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8", errors="replace"))
        except ValueError as exc:
            return self._stopped(
                route, "driver_error", budget, started, f"unreadable report: {exc}",
                refusals=refusals,
            )
        reading = reading_from_report(
            route,
            report,
            wall_ms=wall_ms,
            mode=budget.mode.value,
            idle_baseline_id=idle_baseline_id,
            refusals=refusals,
            log=log,
            declared_numeric=dict(getattr(self.interface, "numeric", {}) or {}),
        )
        reading.interface = self.interface.provenance()
        return reading

    def _drive(
        self,
        cmd: Sequence[str],
        io_dir: Path,
        agent: RouteAgent,
        view: dict[str, Any],
        budget: RouteBudget,
        *,
        deadline_s: float,
    ) -> tuple[int, str, bool]:


        env = build_env(budget.mode)
        env["GB_ROUTE_PLAN"] = str(io_dir / "plan.json").replace("\\", "/")
        env["GB_ROUTE_OUT"] = str(io_dir / "route_report.json").replace("\\", "/")
        try:
            early_plan = json.loads((io_dir / "plan.json").read_text(encoding="utf-8"))
            early_state = (early_plan.get("inject") or {}).get("autoload_state")
            if early_state:
                env["GB_ROUTE_AUTOLOAD_STATE"] = json.dumps(
                    early_state, ensure_ascii=True, separators=(",", ":")
                )
        except (OSError, ValueError, AttributeError):


            pass
        log_path = io_dir / "godot_stdout.log"
        log_f = open(log_path, "w", encoding="utf-8", errors="replace")


        proc = subprocess.Popen(
            list(cmd),
            cwd=str(self.prep.path) if self.prep else None,
            env=env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
        )

        def _log() -> str:
            log_f.flush()
            try:
                return log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""

        deadline = time.time() + max(30.0, deadline_s)
        turn = 0
        history: list[dict[str, Any]] = []
        timed_out = False
        try:
            while proc.poll() is None:
                if time.time() > deadline:
                    proc.kill()
                    timed_out = True
                    break
                obs_path = io_dir / f"obs_{turn:04d}.json"
                act_path = io_dir / f"act_{turn:04d}.json"
                if obs_path.is_file() and not act_path.is_file():
                    try:
                        obs = json.loads(obs_path.read_text(encoding="utf-8"))
                    except (ValueError, OSError):


                        time.sleep(0.05)
                        continue
                    ops = agent.propose(obs, view, history)
                    check = self.check_ops(
                        ops,
                        op_cap=(
                            None if _agent_is_author_exempt(agent) else budget.ops
                        ),
                        frame_cap=budget.frames,
                    )
                    payload: dict[str, Any] = {
                        "ops": [o.to_dict() for o in check.accepted],
                        "refusals": [r.to_dict() for r in check.refusals],
                    }
                    if not check.accepted:
                        payload["stop"] = True
                        payload["stop_reason"] = (
                            "agent_unavailable"
                            if not agent.provider_available
                            else "agent_no_ops"
                        )
                    tmp = act_path.with_suffix(".part")
                    tmp.write_text(json.dumps(payload), encoding="utf-8")
                    os.replace(tmp, act_path)
                    history.append({"turn": turn, "ops": payload["ops"]})
                    turn += 1
                    continue
                time.sleep(0.02)
            if timed_out:
                return -9, _log() + f"\nTIMEOUT after {deadline_s}s", True
            return proc.returncode, _log(), False
        finally:
            if proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                        check=False,
                    )
                else:
                    proc.kill()
            if log_f is not None:
                log_f.close()

    def _stopped(
        self,
        route: Route,
        reason: str,
        budget: RouteBudget,
        started: float,
        detail: str,
        *,
        refusals: Sequence[dict[str, Any]] = (),
    ) -> RouteReading:
        return RouteReading(
            route_id=route.route_id,
            tier=route.tier,
            stop_reason=reason,
            wall_ms=(time.time() - started) * 1000.0,
            mode=budget.mode.value,
            devices_required=list(route.required_devices),
            refusals=[dict(r) for r in refusals],
            detail=detail,
            interface=self.interface.provenance(),
        )


@dataclass
class L0Result:


    no_input_no_win: bool = False
    no_input_stop_reason: str = ""
    live_actions: list[str] = field(default_factory=list)
    dead_actions: list[str] = field(default_factory=list)
    liveness: list[Liveness] = field(default_factory=list)
    idle_baseline_id: str = ""
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.no_input_no_win and bool(self.live_actions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "no_input_no_win": self.no_input_no_win,
            "no_input_stop_reason": self.no_input_stop_reason,
            "live_actions": self.live_actions,
            "dead_actions": self.dead_actions,
            "liveness": [l.to_dict() for l in self.liveness],
            "idle_baseline_id": self.idle_baseline_id,
            "detail": self.detail,
        }


def judge_no_input(reading: RouteReading) -> bool:


    return reading.stop_reason != "goal_reached"


def run_l0(
    session: RouteSession,
    route: Route,
    budget: RouteBudget,
    actions: Sequence[str],
    *,
    idle_frames: int = L0_IDLE_FRAMES,
) -> L0Result:


    actions = authorable_actions(actions)
    idle_id = f"{route.route_id}#idle{idle_frames}"
    idle_reading = session.run(
        route,
        NullAgent(),
        budget,
        run_tag=f"{route.route_id.replace('/', '_')}_idle",
        override_ops=[],
        override_frames=idle_frames,
        idle_baseline_id=idle_id,
    )
    result = L0Result(
        no_input_no_win=judge_no_input(idle_reading),
        no_input_stop_reason=idle_reading.stop_reason,
        idle_baseline_id=idle_id,
    )
    idle_tag = f"{route.route_id.replace('/', '_')}_idle"
    idle_rows = _trace_rows(session, idle_tag)
    if not idle_rows:
        result.detail = (
            "the idle control produced no trace, so there is nothing to subtract "
            "and no action can be judged live"
        )
        return result

    for action in actions:
        tag = f"{route.route_id.replace('/', '_')}_act_{action}"


        session.run(
            route,
            ScriptedAgent([Op("hold", action, min(idle_frames, MAX_OP_FRAMES))]),
            budget,
            run_tag=tag,
            override_frames=idle_frames,
            idle_baseline_id=idle_id,
        )
        action_rows = _trace_rows(session, tag)
        if not action_rows:
            result.dead_actions.append(action)
            continue
        judged = liveness_over_trace(
            action_rows, idle_rows, action=action, idle_baseline_id=idle_id
        )
        if not judged.live:
            tap_tag = tag + "_taps"
            session.run(
                route,
                ScriptedAgent(_liveness_tap_ops(action, idle_frames)),
                budget,
                run_tag=tap_tag,
                override_frames=idle_frames,
                idle_baseline_id=idle_id,
            )
            tap_rows = _trace_rows(session, tap_tag)
            if tap_rows:
                tapped = liveness_over_trace(
                    tap_rows, idle_rows, action=action, idle_baseline_id=idle_id
                )
                if tapped.live:
                    judged = tapped
                    judged.detail += "; observed with repeated taps (1 frame on, 5 off)"
                else:
                    judged.detail += "; also no effect under repeated taps (1 frame on, 5 off)"
            else:
                judged.detail += "; repeated-tap trial produced no trace"
        result.liveness.append(judged)
        (result.live_actions if judged.live else result.dead_actions).append(action)
    result.detail = (
        f"{len(result.live_actions)} live / {len(result.dead_actions)} dead against "
        f"the {idle_frames}-frame idle control {idle_id}"
    )
    return result


def _liveness_tap_ops(action: str, frames: int) -> list[Op]:

    ops: list[Op] = []
    remaining = frames
    while remaining > 0:
        ops.append(Op("hold", action, 1))
        remaining -= 1
        if remaining:
            pause = min(5, remaining)
            ops.append(Op("wait", frames=pause))
            remaining -= pause
    return ops


def _trace_rows(session: RouteSession, tag: str) -> list[dict[str, Any]]:
    path = session.io_root / tag / "route_report.json"
    if not path.is_file():
        return []
    try:
        report = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return []
    rows = report.get("rows") or []
    return [row for row in rows if isinstance(row, dict)]


def _tail_signature(session: RouteSession, tag: str) -> dict[str, Any] | None:
    rows = _trace_rows(session, tag)
    if not rows:
        return None
    return signature_from_row(rows[-1])


@dataclass
class GoldValidation:


    route_id: str
    ok: bool
    detail: str
    reading: RouteReading | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "ok": self.ok,
            "detail": self.detail,
            "reading": self.reading.to_dict() if self.reading else None,
        }

    def as_item(self, weight: float = 1.0) -> Item:
        item_id = f"O8/gold/{self.route_id}"
        if self.ok:
            return passed(item_id, weight=weight, detail=self.detail)

        return inconclusive(
            item_id,
            weight=weight,
            detail=self.detail,
            attribution=Attribution.HARNESS,
            evidence=self.to_dict(),
        )


def _authored_op_limit(route: Route, budget: RouteBudget) -> int | None:

    if route.tier == 5 or route.provenance.kind == "E":
        return None
    if route.continue_after_success:


        return route.budget.steps or budget.ops
    return budget.ops


def validate_route_on_gold(
    route: Route,
    session: RouteSession,
    budget: RouteBudget,
    *,
    expose_cmdline_plan: bool = True,
) -> GoldValidation:


    authored = route.authored_solution if isinstance(route.authored_solution, dict) else {}
    ops = authored.get("ops")
    if not isinstance(ops, list) or not ops:
        return GoldValidation(
            route.route_id,
            False,
            "authored_solution carries no machine-executable `ops` list, so RT-1 "
            "cannot be run and this route must not be scored against anybody",
        )
    check = session.check_ops(
        ops,
        op_cap=_authored_op_limit(route, budget),
        frame_cap=budget.frames,
    )
    if check.refusals:
        return GoldValidation(
            route.route_id,
            False,
            f"the authored solution contains ops this harness refuses: "
            f"{[r.to_dict() for r in check.refusals]}",
        )
    raw_setup = authored.get("setup_ops", [])
    setup_check = session.check_ops(
        raw_setup if isinstance(raw_setup, list) else [],
        op_cap=None,
        frame_cap=budget.frames,
    )
    if setup_check.refusals:
        return GoldValidation(
            route.route_id,
            False,
            f"the authored setup contains ops this harness refuses: "
            f"{[r.to_dict() for r in setup_check.refusals]}",
        )
    reading = session.run(
        route,
        ScriptedAgent(check.accepted, name="gold"),
        budget,
        run_tag=f"gold_{route.route_id.replace('/', '_')}",
        override_setup_ops=setup_check.accepted,
        expose_cmdline_plan=expose_cmdline_plan,
    )
    ok = reading.all_three
    return GoldValidation(
        route.route_id,
        ok,
        (
            f"the authored solution clears the route on the reference "
            f"({reading.frames} frames, from the authored tape)"
            if ok
            else (
                "the authored solution does NOT clear the route on the reference: "
                f"reached={reading.reached} segment_clean={reading.segment_clean} "
                f"used_required={reading.used_required} "
                f"stop_reason={reading.stop_reason}. The route is the defect."
            )
        ),
        reading,
    )


def replay_authored_route(
    route: Route,
    session: RouteSession,
    budget: RouteBudget,
) -> RouteReading:


    authored = route.authored_solution if isinstance(route.authored_solution, dict) else {}
    ops = authored.get("ops")
    if not isinstance(ops, list) or not ops:
        return session._stopped(
            route,
            "plan_refused",
            budget,
            time.time(),
            "registered route has no machine-executable authored ops",
        )


    op_limit = _authored_op_limit(route, budget)
    check = session.check_ops(ops, op_cap=op_limit, frame_cap=budget.frames)
    if check.refusals:
        return session._stopped(
            route,
            "plan_refused",
            budget,
            time.time(),
            f"registered authored ops were refused: {[r.to_dict() for r in check.refusals]}",
            refusals=[r.to_dict() for r in check.refusals],
        )
    raw_setup = authored.get("setup_ops", [])
    setup_check = session.check_ops(
        raw_setup if isinstance(raw_setup, list) else [],
        op_cap=None,
        frame_cap=budget.frames,
    )
    if setup_check.refusals:
        return session._stopped(
            route,
            "plan_refused",
            budget,
            time.time(),
            f"registered authored setup ops were refused: "
            f"{[r.to_dict() for r in setup_check.refusals]}",
            refusals=[r.to_dict() for r in setup_check.refusals],
        )
    return session.run(
        route,
        ScriptedAgent(check.accepted, name="registered-authored"),
        budget,
        run_tag=f"scored_{route.route_id.replace('/', '_')}",
        override_ops=check.accepted,
        override_setup_ops=setup_check.accepted,
    )


@dataclass
class Finding:
    id: str
    severity: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "severity": self.severity, "message": self.message}


def check_route_set(
    routes: Sequence[Route],
    *,
    gold: Sequence[GoldValidation] = (),
    planner_passes: dict[str, bool] | None = None,
) -> list[Finding]:


    findings: list[Finding] = []
    tiers = {r.tier for r in routes}

    if not routes:
        return [Finding("RT-3", "error", "the route set is empty")]
    if 0 not in tiers:
        findings.append(
            Finding(
                "RT-3a",
                "error",
                "no L0 route: without the floor task there is no no-input-no-win "
                "control and no liveness baseline",
            )
        )
    if planner_passes is None:
        findings.append(
            Finding(
                "RT-3b",
                "warning",
                "no reference-planner results supplied, so it is unknown whether "
                "any route resists the scripted baseline",
            )
        )
    elif planner_passes and all(planner_passes.get(r.route_id, False) for r in routes):
        findings.append(
            Finding(
                "RT-3b",
                "error",
                "the reference planner passes every route; a set where everything "
                "scores 1.0 measures nothing. At least one route must resist it.",
            )
        )

    gold_by_id = {g.route_id: g for g in gold}
    for route in routes:
        g = gold_by_id.get(route.route_id)
        if g is None:
            findings.append(
                Finding(
                    "RT-1",
                    "error",
                    f"{route.route_id}: never validated on the reference "
                    "implementation; an unsolvable route penalises every correct "
                    "reproduction",
                )
            )
        elif not g.ok:
            findings.append(Finding("RT-1", "error", f"{route.route_id}: {g.detail}"))
        for message in route.validate():
            findings.append(Finding("route-schema", "warning", message))

    counts: dict[str, int] = {}
    for route in routes:
        counts[route.provenance.kind] = counts.get(route.provenance.kind, 0) + 1
    extracted = counts.get("E", 0) / len(routes)
    if extracted < 0.5:
        findings.append(
            Finding(
                "provenance",
                "warning",
                f"only {extracted:.0%} of routes are mechanically extracted (target "
                ">= 50%); extraction is the only provenance class with no judgement "
                "in it",
            )
        )
    return findings
