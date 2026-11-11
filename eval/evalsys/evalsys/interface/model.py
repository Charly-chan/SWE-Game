

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


def frozen_mapping(value: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True)
class LevelAddress:
    scene: str
    file: Path | None
    valid: bool


@dataclass(frozen=True)
class EndingVocabulary:
    scenes: Mapping[str, str]
    success: tuple[str, ...]
    failure: tuple[str, ...]
    shared_scenes: tuple[str, ...] = ()


@dataclass(frozen=True)
class NodeAddress:
    device_id: str
    node_path: str


@dataclass(frozen=True)
class CameraAddress:
    scene: str
    node_path: str

    @property
    def text(self) -> str:
        return f"{self.scene}::{self.node_path}"


@dataclass(frozen=True)
class Predicate:
    expression: str


@dataclass(frozen=True)
class GroupDeclaration:
    present: tuple[str, ...]
    absent: tuple[str, ...]


@dataclass(frozen=True)
class ActionDeclaration:
    bound: tuple[str, ...]
    missing: tuple[str, ...]
    unread: tuple[str, ...]


@dataclass(frozen=True)
class ExtendedAction:


    id: str
    why: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "why": self.why}


@dataclass(frozen=True)
class AnalogAxis:


    id: str
    why: str
    min: float
    max: float
    steps: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "why": self.why,
            "min": self.min,
            "max": self.max,
            "steps": self.steps,
        }

    def quantize(self, value: float) -> float:
        if self.steps <= 1:
            return self.min
        span = self.max - self.min
        t = (float(value) - self.min) / span
        idx = int(round(t * (self.steps - 1)))
        idx = max(0, min(self.steps - 1, idx))
        return round(self.min + idx * span / (self.steps - 1), 6)

    @classmethod
    def from_dict(cls, raw: Any) -> "AnalogAxis | None":
        if not isinstance(raw, dict):
            return None
        ident = str(raw.get("id") or "").strip()
        why = str(raw.get("why") or "").strip()
        if not ident:
            return None
        try:
            lo = float(raw["min"])
            hi = float(raw["max"])
            steps = int(raw["steps"])
        except (KeyError, TypeError, ValueError):
            return None
        return cls(ident, why, lo, hi, steps)


@dataclass(frozen=True)
class SubmissionInterface:
    version: int
    project_root: Path
    project_file: Path
    levels: tuple[LevelAddress, ...]
    endings: EndingVocabulary
    numeric: Mapping[str, str]
    device_ids: Mapping[str, NodeAddress]
    anchor_camera: CameraAddress | None
    audio_buses: tuple[str, ...]
    level_clear: Predicate | None
    level_entry: Mapping[str, Any]
    groups: GroupDeclaration
    actions: ActionDeclaration
    extended_actions: tuple[ExtendedAction, ...]
    analog_axes: tuple[AnalogAxis, ...]
    source_sha256: str
    normalized_sha256: str
    report: Any

    @property
    def extended_action_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.extended_actions)

    @property
    def analog_axis_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.analog_axes)

    def provenance(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "interface_version": self.version,
            "source_sha256": self.source_sha256,
            "normalized_sha256": self.normalized_sha256,
        }


        if self.extended_actions:
            out["extended_actions"] = [item.to_dict() for item in self.extended_actions]
        if self.analog_axes:
            out["analog_axes"] = [item.to_dict() for item in self.analog_axes]
        return out

    def runtime_dict(self) -> dict[str, Any]:
        return {
            **self.provenance(),
            "levels": [level.scene for level in self.levels],
            "endings": dict(self.endings.scenes),
            "numeric": dict(self.numeric),
            "device_ids": {
                key: address.node_path for key, address in self.device_ids.items()
            },
            "anchor_camera": self.anchor_camera.text if self.anchor_camera else None,
            "audio_buses": list(self.audio_buses),
            "level_clear": (
                {"predicate": self.level_clear.expression}
                if self.level_clear else None
            ),
            "level_entry": dict(self.level_entry),
            "groups": list(self.groups.present),
            "actions": list(self.actions.bound),
            "extended_actions": [item.to_dict() for item in self.extended_actions],
            "analog_axes": [item.to_dict() for item in self.analog_axes],
        }
