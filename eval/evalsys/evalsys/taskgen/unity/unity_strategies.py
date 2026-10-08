


from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from ..modes import UNITY_ACTIONS


STRATEGY_SCHEMA = "gamebench.unity-strategy-library.v2"
STRATEGY_TYPES = frozenset({"objective", "movement", "traversal", "combat", "build"})
_ROLE = re.compile(r"^gb_[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class StrategySpec:


    strategy_type: str
    fallback_actions: tuple[str, ...]
    parameters: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.strategy_type,
            "fallback_actions": list(self.fallback_actions),
            "parameters": dict(self.parameters),
        }


_PALETTES: dict[str, tuple[str, ...]] = {
    "objective": ("gb_action", "gb_right", "gb_left"),
    "movement": ("gb_right", "gb_left", "gb_up", "gb_down"),
    "traversal": ("gb_right", "gb_jump", "gb_left", "gb_jump"),
    "combat": ("gb_attack", "gb_right", "gb_left", "gb_dash"),
    "build": ("gb_action", "gb_right", "gb_left", "gb_attack"),
}


def strategy_for_actions(
    actions: Iterable[str],
    *,
    predicate: str = "",
    parameters: Mapping[str, Any] | None = None,
) -> StrategySpec:


    all_ordered = tuple(dict.fromkeys(str(item) for item in actions if str(item)))
    ordered = tuple(item for item in all_ordered if item in UNITY_ACTIONS)
    names = set(ordered)
    lowered = predicate.lower()
    if names & {"gb_attack", "gb_dash"} or any(
        token in lowered for token in ("alive(", "count(", "score")
    ):
        kind = "combat"
    elif "gb_action" in names and not names & {"gb_jump", "gb_up", "gb_down"}:
        kind = "build"
    elif "gb_jump" in names:
        kind = "traversal"
    elif names & {"gb_left", "gb_right", "gb_up", "gb_down"}:
        kind = "movement"
    else:
        kind = "objective"


    fallback = all_ordered or _PALETTES[kind]
    return StrategySpec(kind, fallback, dict(parameters or {}))


def policy_definition(
    spec: StrategySpec,
    *,
    policy_id: str,
    supported_actions: Iterable[str] = UNITY_ACTIONS,
    analog_axes: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:


    if spec.strategy_type not in STRATEGY_TYPES:
        raise ValueError(
            f"strategy type must be one of {sorted(STRATEGY_TYPES)}, "
            f"got {spec.strategy_type!r}"
        )
    allowed_actions = set(str(item) for item in supported_actions)
    axis_specs = {str(item.get("id") or ""): item for item in analog_axes}
    actions = [
        {"canonical_action": action, "value": 1.0, "hold_frames": 1}
        for action in spec.fallback_actions
        if action in allowed_actions
    ]
    if not actions:
        raise ValueError(f"strategy {spec.strategy_type!r} has no canonical actions")
    def sequence(name: str, default: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raw = spec.parameters.get(name)
        if raw is None:
            return [dict(item) for item in default]
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"strategy parameter {name!r} must be a non-empty array")
        result: list[dict[str, Any]] = []
        for index, item in enumerate(raw):
            if isinstance(item, str):
                action, value, frames = item, 1.0, 1
            elif isinstance(item, Mapping):
                action = str(item.get("action", item.get("canonical_action", "")))
                value = item.get("value", 1.0)
                frames = item.get("hold_frames", 1)
            else:
                raise ValueError(f"strategy parameter {name}[{index}] must be an action")
            chord_raw = item.get("actions", []) if isinstance(item, Mapping) else []
            if not isinstance(chord_raw, list):
                raise ValueError(f"strategy parameter {name}[{index}].actions must be an array")
            chord = [str(value) for value in chord_raw]
            axes_raw = item.get("axes", {}) if isinstance(item, Mapping) else {}
            if not isinstance(axes_raw, Mapping):
                raise ValueError(f"strategy parameter {name}[{index}].axes must be an object")
            if action and action not in allowed_actions:
                raise ValueError(f"strategy parameter {name}[{index}] uses {action!r}")
            if any(value not in allowed_actions for value in chord):
                raise ValueError(f"strategy parameter {name}[{index}] uses unsupported chord")
            for axis_id in axes_raw:
                if str(axis_id) not in axis_specs:
                    raise ValueError(f"strategy parameter {name}[{index}] uses unsupported axis {axis_id!r}")
            if isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= 600:
                raise ValueError(f"strategy parameter {name}[{index}] hold_frames must be in [1, 600]")
            rendered = {"canonical_action": action, "value": value, "hold_frames": frames}
            if chord:
                rendered.pop("canonical_action", None)
                rendered["actions"] = chord
            if axes_raw:
                rendered["axes"] = {str(key): float(axis_value) for key, axis_value in axes_raw.items()}
            result.append(rendered)
        return result

    def role(name: str, default: str) -> str:
        value = str(spec.parameters.get(name) or default)
        if not _ROLE.fullmatch(value):
            raise ValueError(f"invalid strategy role {name}={value!r}")
        return value

    by_name = {item["canonical_action"]: item for item in actions}
    rules: list[dict[str, Any]] = []
    if spec.strategy_type == "combat":
        verb = next(
            (by_name[name] for name in ("gb_attack", "gb_action", "gb_jump", "gb_dash") if name in by_name),
            actions[0],
        )
        target_role = role("target_role", "gb_enemy")
        rules.append({
            "when": f"alive({target_role})",
            "actions": sequence("engage_sequence", [verb]),
        })
        if spec.parameters.get("collectible_sequence") is not None:
            collectible_role = role("collectible_role", "gb_collectible")
            rules.append({
                "when": f"alive({collectible_role})",
                "actions": sequence("collectible_sequence", actions),
            })
    elif spec.strategy_type == "build":
        verb = next(
            (by_name[name] for name in ("gb_action", "gb_attack", "gb_jump") if name in by_name),
            actions[0],
        )
        interaction_role = role("interaction_role", "gb_interactive")
        rules.append({
            "when": f"overlap(gb_player, {interaction_role})",
            "actions": sequence("interaction_sequence", [verb]),
        })
    elif spec.strategy_type == "traversal":
        for prefix, default_role in (
            ("interaction", "gb_interactive"),
            ("checkpoint", "gb_checkpoint"),
            ("hazard", "gb_hazard"),
        ):
            key = f"{prefix}_sequence"
            if spec.parameters.get(key) is None:
                continue
            target = role(f"{prefix}_role", default_role)
            rules.append({
                "when": f"overlap(gb_player, {target})",
                "actions": sequence(key, actions),
            })
    return {
        "id": policy_id,
        "strategy_type": spec.strategy_type,
        "parameters": dict(spec.parameters),
        "rules": rules,
        "fallback": sequence("fallback_sequence", actions),
    }


__all__ = [
    "STRATEGY_SCHEMA",
    "STRATEGY_TYPES",
    "StrategySpec",
    "policy_definition",
    "strategy_for_actions",
]
