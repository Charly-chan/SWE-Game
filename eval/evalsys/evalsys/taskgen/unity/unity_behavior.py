

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from ...routes.schema import Goal, validate_predicate
from ..modes import UNITY_ACTIONS


SCHEMA = "gamebench.unity-hidden-behavior.v1"
EVIDENCE_BASES = frozenset({"gdd_explicit", "video_observable", "source_derived"})
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,95}$")


@dataclass(frozen=True)
class UnityCounterfactual:
    id: str
    remove: str = ""
    replace: str = ""
    with_action: str = ""
    neutralize_axis: str = ""
    with_value: float = 0.0
    expect_goal: bool = False

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "UnityCounterfactual":
        return cls(
            id=str(raw.get("id") or ""),
            remove=str(raw.get("remove") or ""),
            replace=str(raw.get("replace") or ""),
            with_action=str(raw.get("with") or ""),
            neutralize_axis=str(raw.get("neutralize_axis") or ""),
            with_value=float(raw.get("with_value", 0.0)),
            expect_goal=bool(raw.get("expect_goal", False)),
        )


@dataclass(frozen=True)
class UnityCaptureCheckpoint:
    id: str
    kind: str = ""
    predicate: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "UnityCaptureCheckpoint":
        return cls(
            id=str(raw.get("id") or ""),
            kind=str(raw.get("kind") or ""),
            predicate=str(raw.get("predicate") or ""),
        )


@dataclass(frozen=True)
class UnityHiddenBehaviorScenario:
    id: str
    evidence_basis: tuple[str, ...]
    start_level: int
    budget_frames: int
    policy: str
    goal: Goal
    source_goal: Goal | None = None
    counterfactuals: tuple[UnityCounterfactual, ...] = ()
    capture_checkpoints: tuple[UnityCaptureCheckpoint, ...] = ()
    required_actions: tuple[str, ...] = ()
    required_axes: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ()
    required_numeric: tuple[str, ...] = ()
    authoring_record: str = ""

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
        *,
        allowed_actions: tuple[str, ...] = UNITY_ACTIONS,
        allowed_axes: tuple[str, ...] = (),
    ) -> "UnityHiddenBehaviorScenario":
        if raw.get("schema") != SCHEMA:
            raise ValueError(f"hidden behavior schema must be {SCHEMA!r}")
        goal = raw.get("goal")
        if not isinstance(goal, dict):
            raise ValueError("hidden behavior goal must be an object")
        scenario = cls(
            id=str(raw.get("id") or ""),
            evidence_basis=tuple(str(item) for item in (raw.get("evidence_basis") or ())),
            start_level=int(raw.get("start_level", 0)),
            budget_frames=int(raw.get("budget_frames", 0)),
            policy=str(raw.get("policy") or ""),
            goal=Goal.from_dict(goal),
            source_goal=(
                Goal.from_dict(raw["source_goal"])
                if isinstance(raw.get("source_goal"), Mapping)
                else None
            ),
            counterfactuals=tuple(
                UnityCounterfactual.from_dict(item)
                for item in (raw.get("counterfactuals") or ())
                if isinstance(item, Mapping)
            ),
            capture_checkpoints=tuple(
                UnityCaptureCheckpoint.from_dict(item)
                for item in (raw.get("capture_checkpoints") or ())
                if isinstance(item, Mapping)
            ),
            required_actions=tuple(str(item) for item in (raw.get("required_actions") or ())),
            required_axes=tuple(str(item) for item in (raw.get("required_axes") or ())),
            required_roles=tuple(str(item) for item in (raw.get("required_roles") or ())),
            required_numeric=tuple(str(item) for item in (raw.get("required_numeric") or ())),
            authoring_record=str(raw.get("authoring_record") or ""),
        )
        findings = scenario.findings(
            allowed_actions=allowed_actions, allowed_axes=allowed_axes
        )
        if findings:
            raise ValueError("; ".join(findings))
        return scenario

    def findings(
        self,
        *,
        allowed_actions: tuple[str, ...] = UNITY_ACTIONS,
        allowed_axes: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        errors: list[str] = []
        if not _ID.fullmatch(self.id):
            errors.append("scenario id is missing or invalid")
        if not self.evidence_basis or not set(self.evidence_basis) <= EVIDENCE_BASES:
            errors.append("evidence_basis must use the frozen evidence vocabulary")
        if "source_derived" in self.evidence_basis and not self.authoring_record:
            errors.append("source-derived scenario requires an evidence record")
        if self.start_level < 0:
            errors.append("start_level must be non-negative")
        if not 1 <= self.budget_frames <= 36000:
            errors.append("budget_frames must be in [1, 36000]")
        if not self.policy:
            errors.append("policy is required")
        if "source_derived" in self.evidence_basis and self.source_goal is None:
            errors.append(
                "source-derived scenario requires an independently validated source_goal"
            )
        if "source_derived" not in self.evidence_basis and self.source_goal is not None:
            errors.append("source_goal is only valid for source-derived scenarios")
        for predicate in (
            self.goal.predicate,
            self.goal.end_predicate,
            *(item.predicate for item in self.goal.milestones),
            *(item.predicate for item in self.goal.invariants),
            *(item.predicate for item in self.goal.observations),
            *((
                self.source_goal.predicate,
                self.source_goal.end_predicate,
                *(item.predicate for item in self.source_goal.milestones),
                *(item.predicate for item in self.source_goal.invariants),
                *(item.predicate for item in self.source_goal.observations),
            ) if self.source_goal is not None else ()),
        ):
            if predicate and not validate_predicate(predicate).ok:
                errors.append(f"invalid predicate: {predicate}")
        unknown_actions = sorted(set(self.required_actions) - set(allowed_actions))
        if unknown_actions:
            errors.append("unsupported required_actions: " + ", ".join(unknown_actions))
        unknown_axes = sorted(set(self.required_axes) - set(allowed_axes))
        if unknown_axes:
            errors.append("unsupported required_axes: " + ", ".join(unknown_axes))
        ids: set[str] = set()
        for counterfactual in self.counterfactuals:
            if not _ID.fullmatch(counterfactual.id) or counterfactual.id in ids:
                errors.append("counterfactual ids must be unique valid identifiers")
            ids.add(counterfactual.id)
            remove = bool(counterfactual.remove)
            replace = bool(counterfactual.replace and counterfactual.with_action)
            neutralize_axis = bool(counterfactual.neutralize_axis)
            if sum((remove, replace, neutralize_axis)) != 1:
                errors.append(
                    f"counterfactual {counterfactual.id} must declare exactly one of "
                    "remove, replace/with, or neutralize_axis"
                )
            actions = {counterfactual.remove, counterfactual.replace, counterfactual.with_action} - {""}
            if not actions <= set(allowed_actions):
                errors.append(f"counterfactual {counterfactual.id} uses unsupported action")
            if neutralize_axis and counterfactual.neutralize_axis not in allowed_axes:
                errors.append(f"counterfactual {counterfactual.id} uses unsupported axis")
        checkpoint_ids: set[str] = set()
        for checkpoint in self.capture_checkpoints:
            if not _ID.fullmatch(checkpoint.id) or checkpoint.id in checkpoint_ids:
                errors.append("capture checkpoint ids must be unique valid identifiers")
            checkpoint_ids.add(checkpoint.id)
            if bool(checkpoint.kind) == bool(checkpoint.predicate):
                errors.append(f"capture checkpoint {checkpoint.id} must declare kind xor predicate")
            if checkpoint.predicate and not validate_predicate(checkpoint.predicate).ok:
                errors.append(f"invalid capture predicate: {checkpoint.predicate}")
        return tuple(dict.fromkeys(errors))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "id": self.id,
            "evidence_basis": list(self.evidence_basis),
            "start_level": self.start_level,
            "budget_frames": self.budget_frames,
            "policy": self.policy,
            "goal": self.goal.to_dict(),
            **({"source_goal": self.source_goal.to_dict()} if self.source_goal else {}),
            "counterfactuals": [
                {
                    "id": item.id,
                    **({"remove": item.remove} if item.remove else {}),
                    **({"replace": item.replace, "with": item.with_action} if item.replace else {}),
                    **(
                        {"neutralize_axis": item.neutralize_axis, "with_value": item.with_value}
                        if item.neutralize_axis else {}
                    ),
                    "expect_goal": item.expect_goal,
                }
                for item in self.counterfactuals
            ],
            "capture_checkpoints": [
                {
                    "id": item.id,
                    **({"kind": item.kind} if item.kind else {}),
                    **({"predicate": item.predicate} if item.predicate else {}),
                }
                for item in self.capture_checkpoints
            ],
            "required_actions": list(self.required_actions),
            **({"required_axes": list(self.required_axes)} if self.required_axes else {}),
            "required_roles": list(self.required_roles),
            "required_numeric": list(self.required_numeric),
            "authoring_record": self.authoring_record,
        }


__all__ = [
    "EVIDENCE_BASES",
    "SCHEMA",
    "UnityCaptureCheckpoint",
    "UnityCounterfactual",
    "UnityHiddenBehaviorScenario",
]
