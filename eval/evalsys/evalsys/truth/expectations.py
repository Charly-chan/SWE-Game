


from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .snapshot import GROUPS, LevelTruth, NormPoint, TruthSnapshot


BANDS = 5


DISTANCE_TOLERANCE = 0.8


EXPECTATIONS_VERSION = "2"


def _spread(values: Sequence[float]) -> float:
    return (max(values) - min(values)) if values else 0.0


def dominant_axis(points: Sequence[NormPoint]) -> str:


    valid = [p for p in points if p.valid]
    if not valid:
        return "x"
    return "x" if _spread([p.x for p in valid]) >= _spread([p.y for p in valid]) else "y"


def occupied_bands(points: Sequence[NormPoint], axis: str, bands: int = BANDS) -> int:

    seen: set[int] = set()
    for p in points:
        if not p.valid:
            continue
        value = p.x if axis == "x" else p.y
        seen.add(min(bands - 1, max(0, int(value * bands))))
    return len(seen)


def norm_distance(a: NormPoint | None, b: NormPoint | None) -> float | None:


    if a is None or b is None or not a.valid or not b.valid:
        return None
    return math.hypot(a.x - b.x, a.y - b.y)


def goal_point(level: LevelTruth) -> NormPoint | None:
    group = level.groups.get("gb_goal")
    if group is None:
        return None
    for point in group.normalised:
        if point.valid:
            return point
    return None


@dataclass(frozen=True)
class LevelExpectation:


    scene: str
    census: dict[str, int] = field(default_factory=dict)


    spawn_goal_distance: float | None = None

    dispersion: dict[str, list[Any]] = field(default_factory=dict)
    goal_transition: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene": self.scene,
            "census": dict(sorted(self.census.items())),
            "spawn_goal_distance": self.spawn_goal_distance,
            "dispersion": {k: list(v) for k, v in sorted(self.dispersion.items())},
            "goal_transition": self.goal_transition,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LevelExpectation":
        distance = raw.get("spawn_goal_distance")
        return cls(
            scene=str(raw.get("scene", "")),
            census={str(k): int(v) for k, v in (raw.get("census") or {}).items()},
            spawn_goal_distance=None if distance is None else float(distance),
            dispersion={
                str(k): list(v) for k, v in (raw.get("dispersion") or {}).items()
            },
            goal_transition=raw.get("goal_transition") or None,
        )


@dataclass(frozen=True)
class Expectations:


    project: str
    levels: tuple[LevelExpectation, ...] = ()
    declared_actions: tuple[str, ...] = ()
    anchor_composition: dict[str, Any] = field(default_factory=dict)
    interface_requirements: dict[str, Any] = field(default_factory=dict)
    generated_at: str = ""
    commit: str = ""
    probe_sha256: str = ""
    engine: str = ""
    snapshot_version: str = ""
    version: str = EXPECTATIONS_VERSION
    notes: tuple[str, ...] = ()


    runtime_assets: dict[str, str] | None = None

    @property
    def level_count(self) -> int:
        return len(self.levels)

    @property
    def digraph(self) -> dict[str, str]:
        return {
            lv.scene: lv.goal_transition
            for lv in self.levels
            if lv.scene and lv.goal_transition
        }

    def collectible_counts(self) -> tuple[int, ...]:
        return tuple(lv.census.get("gb_collectible", 0) for lv in self.levels)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "project": self.project,
            "levels": [lv.to_dict() for lv in self.levels],
            "declared_actions": list(self.declared_actions),
            "anchor_composition": dict(self.anchor_composition),
            "interface_requirements": dict(self.interface_requirements),
            "generated_at": self.generated_at,
            "commit": self.commit,
            "probe_sha256": self.probe_sha256,
            "engine": self.engine,
            "snapshot_version": self.snapshot_version,
            "notes": list(self.notes),
            **({"runtime_assets": dict(self.runtime_assets)}
               if self.runtime_assets is not None else {}),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Expectations":
        return cls(
            project=str(raw.get("project", "")),
            levels=tuple(
                LevelExpectation.from_dict(lv) for lv in raw.get("levels") or ()
            ),
            declared_actions=tuple(str(a) for a in raw.get("declared_actions") or ()),
            anchor_composition=dict(raw.get("anchor_composition") or {}),
            interface_requirements=dict(raw.get("interface_requirements") or {}),
            generated_at=str(raw.get("generated_at", "")),
            commit=str(raw.get("commit", "")),
            probe_sha256=str(raw.get("probe_sha256", "")),
            engine=str(raw.get("engine", "")),
            snapshot_version=str(raw.get("snapshot_version", "")),
            version=str(raw.get("version", EXPECTATIONS_VERSION)),
            notes=tuple(str(n) for n in raw.get("notes") or ()),
            runtime_assets=(dict(raw["runtime_assets"])
                            if raw.get("runtime_assets") is not None else None),
        )

    def write(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
            newline="\n",
        )
        return out

    @classmethod
    def read(cls, path: str | Path) -> "Expectations":
        return cls.from_dict(
            json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
        )


def _head_commit(where: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(where),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def extract(snapshot: TruthSnapshot, *, repo: str | Path | None = None,
            task_id: str | None = None) -> Expectations:


    levels: list[LevelExpectation] = []
    for lv in snapshot.levels:
        dispersion: dict[str, list[Any]] = {}
        for name in GROUPS:
            group = lv.groups.get(name)
            if group is None or len(group.normalised) < 2:
                continue
            axis = dominant_axis(group.normalised)
            dispersion[name] = [axis, occupied_bands(group.normalised, axis)]
        levels.append(
            LevelExpectation(
                scene=lv.scene or lv.declared_scene,
                census=lv.census,
                spawn_goal_distance=norm_distance(lv.spawn_norm, goal_point(lv)),
                dispersion=dispersion,


                goal_transition=snapshot.digraph.get(lv.scene) if lv.reached else None,
            )
        )

    notes: list[str] = []
    unreached = [lv.declared_scene for lv in snapshot.levels if not lv.reached]
    if unreached:
        notes.append(
            "frozen from a gold run in which these declared levels were never "
            "reached, so their readings are absent rather than measured: "
            + ", ".join(unreached)
            + ". The reference expectation requires review; "
            "not a reason to expect less of a submission."
        )

    return Expectations(
        project=task_id or snapshot.project,
        levels=tuple(levels),
        declared_actions=snapshot.declared_actions,
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        commit=_head_commit(Path(repo) if repo else Path(__file__).resolve().parent),
        probe_sha256=snapshot.probe_sha256,
        engine=snapshot.engine,
        snapshot_version=snapshot.version,
        notes=tuple(notes),
    )
