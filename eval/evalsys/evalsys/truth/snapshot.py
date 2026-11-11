


from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


GROUPS: tuple[str, ...] = (
    "gb_player",
    "gb_collectible",
    "gb_goal",
    "gb_hazard",
    "gb_enemy",
    "gb_checkpoint",
    "gb_door",
    "gb_interactive",
)


ACTIONS: tuple[str, ...] = (
    "gb_left",
    "gb_right",
    "gb_up",
    "gb_down",
    "gb_jump",
    "gb_action",
    "gb_pause",
    "gb_reset",
)


@dataclass(frozen=True)
class Vec3:


    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_list(self) -> list[float]:
        return [self.x, self.y, self.z]

    @classmethod
    def from_any(cls, raw: Any) -> "Vec3 | None":
        if raw is None:
            return None
        if isinstance(raw, (list, tuple)):
            vals = list(raw) + [0.0, 0.0, 0.0]
            return cls(float(vals[0]), float(vals[1]), float(vals[2]))
        if isinstance(raw, Mapping):
            if "x" not in raw or "y" not in raw:
                return None
            return cls(float(raw["x"]), float(raw["y"]), float(raw.get("z", 0.0)))
        return None


@dataclass(frozen=True)
class NormPoint:


    x: float = 0.0
    y: float = 0.0
    valid: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "valid": self.valid}

    @classmethod
    def from_any(cls, raw: Any) -> "NormPoint":
        if not isinstance(raw, Mapping) or not raw.get("valid", False):
            return cls(0.0, 0.0, False)
        return cls(float(raw.get("x", 0.0)), float(raw.get("y", 0.0)), True)


@dataclass(frozen=True)
class Bounds:


    valid: bool = False
    plane: str = "xy"
    min3: Vec3 = field(default_factory=Vec3)
    max3: Vec3 = field(default_factory=Vec3)

    @property
    def extent(self) -> Vec3:
        return Vec3(
            self.max3.x - self.min3.x,
            self.max3.y - self.min3.y,
            self.max3.z - self.min3.z,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "plane": self.plane,
            "min3": self.min3.to_list(),
            "max3": self.max3.to_list(),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Bounds":
        if not isinstance(raw, Mapping):
            return cls()
        lo = Vec3.from_any(raw.get("min3")) or Vec3()
        hi = Vec3.from_any(raw.get("max3")) or Vec3()
        return cls(bool(raw.get("valid", False)), str(raw.get("plane", "xy")), lo, hi)


@dataclass(frozen=True)
class GroupTruth:


    total: int = 0
    alive: int = 0
    normalised: tuple[NormPoint, ...] = ()
    world: tuple[Vec3, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "alive": self.alive,
            "normalised": [p.to_dict() for p in self.normalised],
            "world": [v.to_list() for v in self.world],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "GroupTruth":
        if not isinstance(raw, Mapping):
            return cls()
        norm = tuple(NormPoint.from_any(p) for p in raw.get("normalised") or ())
        world = tuple(
            v for v in (Vec3.from_any(p) for p in raw.get("world") or ()) if v is not None
        )
        return cls(int(raw.get("total", 0)), int(raw.get("alive", 0)), norm, world)


@dataclass(frozen=True)
class CollectProbe:


    attempted: bool = False
    target: str = ""
    alive_before: int = 0
    alive_after: int = 0
    delta: int = 0
    detail: str = ""
    effect: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "target": self.target,
            "alive_before": self.alive_before,
            "alive_after": self.alive_after,
            "delta": self.delta,
            "detail": self.detail,
            "effect": self.effect,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CollectProbe":
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            bool(raw.get("attempted", False)),
            str(raw.get("target", "")),
            int(raw.get("alive_before", 0)),
            int(raw.get("alive_after", 0)),
            int(raw.get("delta", 0)),
            str(raw.get("detail", "")),
            dict(raw.get("effect") or {}),
        )


@dataclass(frozen=True)
class CollectCycle:


    index: int = 0
    target: str = ""
    delivered: bool = False
    alive_before: int = 0
    alive_after: int = 0
    delta: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "target": self.target,
            "delivered": self.delivered,
            "alive_before": self.alive_before,
            "alive_after": self.alive_after,
            "delta": self.delta,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CollectCycle":
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            int(raw.get("index", 0)),
            str(raw.get("target", "")),
            bool(raw.get("delivered", False)),
            int(raw.get("alive_before", 0)),
            int(raw.get("alive_after", 0)),
            int(raw.get("delta", 0)),
        )


@dataclass(frozen=True)
class GoalProbe:


    attempted: bool = False
    target: str = ""
    scene_before: str = ""
    scene_after: str = ""
    changed: bool = False
    frames_to_change: int = -1
    detail: str = ""
    effect: dict[str, Any] = field(default_factory=dict)

    @property
    def transition(self) -> str | None:

        return self.scene_after if self.changed and self.scene_after else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "target": self.target,
            "scene_before": self.scene_before,
            "scene_after": self.scene_after,
            "changed": self.changed,
            "frames_to_change": self.frames_to_change,
            "detail": self.detail,
            "effect": self.effect,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "GoalProbe":
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            bool(raw.get("attempted", False)),
            str(raw.get("target", "")),
            str(raw.get("scene_before", "")),
            str(raw.get("scene_after", "")),
            bool(raw.get("changed", False)),
            int(raw.get("frames_to_change", -1)),
            str(raw.get("detail", "")),
            dict(raw.get("effect") or {}),
        )


@dataclass(frozen=True)
class SeriesSample:


    frame: int = 0
    phase: str = ""
    player: Vec3 | None = None
    numeric: dict[str, float] = field(default_factory=dict)
    scene_path: str = ""
    alive: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": self.frame,
            "phase": self.phase,
            "player": self.player.to_list() if self.player else None,
            "numeric": {k: float(v) for k, v in sorted(self.numeric.items())},
            "scene_path": self.scene_path,
            "alive": {k: int(v) for k, v in sorted(self.alive.items())},
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "SeriesSample":
        if not isinstance(raw, Mapping):
            return cls()
        numeric_raw = raw.get("numeric") or {}
        alive_raw = raw.get("alive") or {}
        return cls(
            frame=int(raw.get("frame", 0)),
            phase=str(raw.get("phase", "")),
            player=Vec3.from_any(raw.get("player")),
            numeric={
                str(k): float(v)
                for k, v in numeric_raw.items()
                if isinstance(v, (int, float))
            },
            scene_path=str(raw.get("scene_path", "")),
            alive={
                str(k): int(v)
                for k, v in alive_raw.items()
                if isinstance(v, (int, float))
            },
        )


@dataclass(frozen=True)
class LevelTruth:


    declared_scene: str
    scene: str = ""
    reached: bool = False
    entry_method: str = "none"
    stop_reason: str = ""
    groups: dict[str, GroupTruth] = field(default_factory=dict)
    bounds: Bounds = field(default_factory=Bounds)
    spawn: Vec3 | None = None
    spawn_norm: NormPoint | None = None
    bound_actions: tuple[str, ...] = ()
    collect: CollectProbe = field(default_factory=CollectProbe)
    cycles: tuple[CollectCycle, ...] = ()
    cycles_capped: bool = False
    goal: GoalProbe = field(default_factory=GoalProbe)

    series: tuple[SeriesSample, ...] = ()

    numeric: dict[str, float] = field(default_factory=dict)


    numeric_source: dict[str, str] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    @property
    def scene_matches_declaration(self) -> bool:
        return bool(self.scene) and self.scene == self.declared_scene

    @property
    def census(self) -> dict[str, int]:


        return {g: self.groups.get(g, GroupTruth()).alive for g in GROUPS}

    @property
    def goal_transition(self) -> str | None:
        return self.goal.transition

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared_scene": self.declared_scene,
            "scene": self.scene,
            "reached": self.reached,
            "entry_method": self.entry_method,
            "stop_reason": self.stop_reason,
            "groups": {k: v.to_dict() for k, v in sorted(self.groups.items())},
            "bounds": self.bounds.to_dict(),
            "spawn": self.spawn.to_list() if self.spawn else None,
            "spawn_norm": self.spawn_norm.to_dict() if self.spawn_norm else None,
            "bound_actions": list(self.bound_actions),
            "collect": self.collect.to_dict(),
            "cycles": [c.to_dict() for c in self.cycles],
            "cycles_capped": self.cycles_capped,
            "goal": self.goal.to_dict(),
            "series": [s.to_dict() for s in self.series],
            "numeric": {k: float(v) for k, v in sorted(self.numeric.items())},
            "numeric_source": {k: str(v) for k, v in sorted(self.numeric_source.items())},
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LevelTruth":
        spawn_norm = raw.get("spawn_norm")
        numeric_raw = raw.get("numeric") or {}
        source_raw = raw.get("numeric_source") or {}
        return cls(
            declared_scene=str(raw.get("declared_scene", "")),
            scene=str(raw.get("scene", "")),
            reached=bool(raw.get("reached", False)),
            entry_method=str(raw.get("entry_method", "none")),
            stop_reason=str(raw.get("stop_reason", "")),
            groups={
                str(k): GroupTruth.from_dict(v)
                for k, v in (raw.get("groups") or {}).items()
            },
            bounds=Bounds.from_dict(raw.get("bounds")),
            spawn=Vec3.from_any(raw.get("spawn")),
            spawn_norm=NormPoint.from_any(spawn_norm) if spawn_norm else None,
            bound_actions=tuple(str(a) for a in raw.get("bound_actions") or ()),
            collect=CollectProbe.from_dict(raw.get("collect")),
            cycles=tuple(CollectCycle.from_dict(c) for c in raw.get("cycles") or ()),
            cycles_capped=bool(raw.get("cycles_capped", False)),
            goal=GoalProbe.from_dict(raw.get("goal")),
            series=tuple(SeriesSample.from_dict(s) for s in raw.get("series") or ()),
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
            errors=tuple(str(e) for e in raw.get("errors") or ()),
        )


@dataclass(frozen=True)
class ActionLiveness:


    action: str
    live: bool = False
    via: str = "none"
    idle_baseline_id: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "live": self.live,
            "via": self.via,
            "idle_baseline_id": self.idle_baseline_id,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ActionLiveness":
        return cls(
            action=str(raw.get("action", "")),
            live=bool(raw.get("live", False)),
            via=str(raw.get("via", "none")),
            idle_baseline_id=str(raw.get("idle_baseline_id", "")),
            detail=str(raw.get("detail", "")),
        )


SNAPSHOT_VERSION = "1"


@dataclass(frozen=True)
class TruthSnapshot:


    project: str
    levels: tuple[LevelTruth, ...] = ()
    liveness: tuple[ActionLiveness, ...] = ()
    declared_actions: tuple[str, ...] = ()


    endings: dict[str, str] = field(default_factory=dict)


    no_input_no_win: bool | None = None
    no_input_stop_reason: str = ""
    probe_sha256: str = ""
    driver_sha256: str = ""
    harness: dict[str, str] = field(default_factory=dict)
    interface: dict[str, Any] = field(default_factory=dict)
    engine: str = ""
    taken_at: str = ""
    notes: tuple[str, ...] = ()


    read_failure: str = ""
    version: str = SNAPSHOT_VERSION

    @property
    def read_ok(self) -> bool:

        return not self.read_failure

    @property
    def live_actions(self) -> dict[str, bool]:


        from ..routes.agent import FORBIDDEN_ACTIONS

        return {
            a.action: a.live
            for a in self.liveness
            if a.action not in FORBIDDEN_ACTIONS
        }

    @property
    def reached_levels(self) -> tuple[LevelTruth, ...]:
        return tuple(lv for lv in self.levels if lv.reached)

    @property
    def digraph(self) -> dict[str, str]:


        out: dict[str, str] = {}
        from ..conformance import FAILURE_ENDINGS
        failure_scenes = {scene for name, scene in self.endings.items() if name in FAILURE_ENDINGS}
        for lv in self.levels:
            target = lv.goal_transition
            if lv.reached and lv.scene and target and target not in failure_scenes:
                out[lv.scene] = target
        return out

    def level(self, scene: str) -> LevelTruth | None:
        for lv in self.levels:
            if lv.scene == scene or lv.declared_scene == scene:
                return lv
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "project": self.project,
            "levels": [lv.to_dict() for lv in self.levels],
            "liveness": [a.to_dict() for a in self.liveness],
            "declared_actions": list(self.declared_actions),
            "endings": dict(sorted(self.endings.items())),
            "no_input_no_win": self.no_input_no_win,
            "no_input_stop_reason": self.no_input_stop_reason,
            "probe_sha256": self.probe_sha256,
            "driver_sha256": self.driver_sha256,
            "harness": dict(sorted(self.harness.items())),
            "interface": dict(sorted(self.interface.items())),
            "engine": self.engine,
            "taken_at": self.taken_at,
            "notes": list(self.notes),
            "read_failure": self.read_failure,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TruthSnapshot":
        return cls(
            project=str(raw.get("project", "")),
            levels=tuple(LevelTruth.from_dict(lv) for lv in raw.get("levels") or ()),
            liveness=tuple(
                ActionLiveness.from_dict(a) for a in raw.get("liveness") or ()
            ),
            declared_actions=tuple(str(a) for a in raw.get("declared_actions") or ()),
            endings={str(k): str(v) for k, v in (raw.get("endings") or {}).items()},
            no_input_no_win=(
                None if raw.get("no_input_no_win") is None
                else bool(raw.get("no_input_no_win"))
            ),
            no_input_stop_reason=str(raw.get("no_input_stop_reason", "")),
            probe_sha256=str(raw.get("probe_sha256", "")),
            driver_sha256=str(raw.get("driver_sha256", "")),
            harness={str(k): str(v) for k, v in (raw.get("harness") or {}).items()},
            interface=dict(raw.get("interface") or {}),
            engine=str(raw.get("engine", "")),
            taken_at=str(raw.get("taken_at", "")),
            notes=tuple(str(n) for n in raw.get("notes") or ()),
            read_failure=str(raw.get("read_failure", "")),
            version=str(raw.get("version", SNAPSHOT_VERSION)),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)

    def write(self, path: str | Path) -> Path:


        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.to_json(), encoding="utf-8", newline="\n")
        return out

    @classmethod
    def read(cls, path: str | Path) -> "TruthSnapshot":
        return cls.from_dict(
            json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
        )
