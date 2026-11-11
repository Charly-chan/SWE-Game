

from __future__ import annotations

import json
from dataclasses import dataclass, replace
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from ...routes.runner import observation_from_row, reading_from_report
from ...routes.schema import BASELINE_PREVIOUS_ROW, Milestone, Route, parse_predicate
from ..modes import UNITY_ACTIONS
from .unity_behavior import UnityHiddenBehaviorScenario


POLICY_SCHEMA = "gamebench.unity-hidden-policies.v1"


@dataclass(frozen=True)
class PolicyAction:
    canonical_action: str
    value: Any = 1.0
    hold_frames: int = 1
    actions: tuple[str, ...] = ()
    axes: tuple[tuple[str, float], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = {
            "canonical_action": self.canonical_action,
            "value": self.value,
            "hold_frames": self.hold_frames,
        }
        if self.actions:
            value["actions"] = list(self.actions)
        if self.axes:
            value["axes"] = {key: axis_value for key, axis_value in self.axes}
        return value


class ClosedLoopPolicy(Protocol):
    def step(self, observation: Mapping[str, Any]) -> PolicyAction | None:
        ...


@dataclass(frozen=True)
class DeclarativePolicyRule:
    when: str
    actions: tuple[PolicyAction, ...]


class DeclarativeClosedLoopPolicy:


    def __init__(
        self,
        policy_id: str,
        rules: tuple[DeclarativePolicyRule, ...],
        fallback: tuple[PolicyAction, ...],
    ) -> None:
        self.policy_id = policy_id
        self.rules = rules
        self.fallback = fallback
        self._cursor: dict[int, int] = {}
        self._predicates = tuple(parse_predicate(rule.when) for rule in rules)

    def step(self, observation: Mapping[str, Any]) -> PolicyAction | None:
        for index, (rule, predicate) in enumerate(zip(self.rules, self._predicates)):
            matched, missing = predicate.evaluate_detail(dict(observation))
            if matched and not missing:
                return self._next(index, rule.actions)
        return self._next(-1, self.fallback) if self.fallback else None

    def _next(self, key: int, actions: tuple[PolicyAction, ...]) -> PolicyAction:
        cursor = self._cursor.get(key, 0)
        self._cursor[key] = cursor + 1
        return actions[cursor % len(actions)]


def _axis_on_grid(value: float, spec: Mapping[str, Any]) -> bool:
    minimum = float(spec["min"])
    maximum = float(spec["max"])
    steps = int(spec["steps"])
    if not math.isfinite(value) or value < minimum or value > maximum or steps < 2:
        return False
    step = (maximum - minimum) / (steps - 1)
    index = round((value - minimum) / step)
    return math.isclose(value, minimum + index * step, rel_tol=0.0, abs_tol=1e-6)


def _policy_actions(
    raw: Any,
    *,
    label: str,
    allowed_actions: tuple[str, ...] = UNITY_ACTIONS,
    analog_axes: tuple[Mapping[str, Any], ...] = (),
) -> tuple[PolicyAction, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{label} requires a non-empty actions array")
    actions: list[PolicyAction] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} action must be an object")
        action = str(item.get("canonical_action") or "")
        chord_raw = item.get("actions") or []
        if not isinstance(chord_raw, list):
            raise ValueError(f"{label} actions must be an array")
        chord = tuple(str(value) for value in chord_raw)
        axes_raw = item.get("axes") or {}
        if not isinstance(axes_raw, Mapping):
            raise ValueError(f"{label} axes must be an object")
        axes = tuple((str(key), float(value)) for key, value in axes_raw.items())
        frames = int(item.get("hold_frames") or 0)
        if action and action not in allowed_actions:
            raise ValueError(f"{label} uses unsupported action {action!r}")
        if any(value not in allowed_actions for value in chord):
            raise ValueError(f"{label} uses unsupported chord action")
        if action and chord:
            raise ValueError(f"{label} must use canonical_action or actions, not both")
        axis_specs = {str(spec.get("id") or ""): spec for spec in analog_axes}
        for axis_id, axis_value in axes:
            if axis_id not in axis_specs or not _axis_on_grid(axis_value, axis_specs[axis_id]):
                raise ValueError(f"{label} uses undeclared or unquantized axis {axis_id!r}")
        if "canonical_action" not in item and not chord and not axes:
            raise ValueError(f"{label} action must dispatch a button, chord, or axis")
        if not 1 <= frames <= 600:
            raise ValueError(f"{label} hold_frames must be in [1, 600]")
        actions.append(PolicyAction(action, item.get("value", 1.0), frames, chord, axes))
    return tuple(actions)


def load_declarative_policies(
    path: str | Path,
) -> tuple[str, dict[str, DeclarativeClosedLoopPolicy]]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read Unity hidden policies {source}: {exc}") from exc
    accepted_schemas = {
        POLICY_SCHEMA,
        "gamebench.unity-hidden-policies.v1",
        "gamebench.unity-hidden-suite.v1",
    }
    if not isinstance(payload, dict) or payload.get("schema") not in accepted_schemas:
        raise ValueError(
            f"hidden policy schema must be one of {sorted(accepted_schemas)!r}"
        )
    game_id = str(payload.get("game_id") or "")
    definitions = payload.get("policies")
    if not isinstance(definitions, list) or not definitions:
        raise ValueError("hidden policies must be a non-empty array")
    input_contract = payload.get("input_contract") or {}
    if not isinstance(input_contract, Mapping):
        raise ValueError("hidden suite input_contract must be an object")
    allowed_actions = tuple(
        str(item) for item in (input_contract.get("supported_actions") or UNITY_ACTIONS)
    )
    analog_axes = tuple(
        item for item in (input_contract.get("analog_axes") or ()) if isinstance(item, Mapping)
    )
    policies: dict[str, DeclarativeClosedLoopPolicy] = {}
    for raw in definitions:
        if not isinstance(raw, Mapping):
            raise ValueError("hidden policy definition must be an object")
        policy_id = str(raw.get("id") or "")
        if not policy_id or policy_id in policies:
            raise ValueError("hidden policy ids must be present and unique")
        raw_rules = raw.get("rules") or []
        if not isinstance(raw_rules, list):
            raise ValueError(f"policy {policy_id} rules must be an array")
        rules: list[DeclarativePolicyRule] = []
        for index, rule in enumerate(raw_rules):
            if not isinstance(rule, Mapping):
                raise ValueError(f"policy {policy_id} rule must be an object")
            when = str(rule.get("when") or "")
            parse_predicate(when)
            rules.append(
                DeclarativePolicyRule(
                    when,
                    _policy_actions(
                        rule.get("actions"), label=f"{policy_id}.rules[{index}]",
                        allowed_actions=allowed_actions, analog_axes=analog_axes,
                    ),
                )
            )
        fallback = _policy_actions(
            raw.get("fallback"), label=f"{policy_id}.fallback",
            allowed_actions=allowed_actions, analog_axes=analog_axes,
        )
        policies[policy_id] = DeclarativeClosedLoopPolicy(
            policy_id, tuple(rules), fallback
        )
    return game_id, policies


@dataclass(frozen=True)
class PolicyRunResult:
    status: str
    detail: str
    goal_reached: bool
    frames_used: int
    actions: tuple[PolicyAction, ...] = ()
    rows: tuple[Mapping[str, Any], ...] = ()
    milestones_reached: tuple[str, ...] = ()
    invariant_failures: tuple[str, ...] = ()
    missing_observations: tuple[str, ...] = ()
    observations_reached: tuple[str, ...] = ()
    observations_triggered: tuple[str, ...] = ()
    source_milestones_reached: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "goal_reached": self.goal_reached,
            "frames_used": self.frames_used,
            "actions": [item.to_dict() for item in self.actions],
            "rows": [dict(item) for item in self.rows],
            "milestones_reached": list(self.milestones_reached),
            "invariant_failures": list(self.invariant_failures),
            "missing_observations": list(self.missing_observations),
            "observations_reached": list(self.observations_reached),
            "observations_triggered": list(self.observations_triggered),
            "source_milestones_reached": list(self.source_milestones_reached),
        }


def _numeric(row: Mapping[str, Any]) -> dict[str, float]:
    return {
        str(key): float(value)
        for key, value in dict(row.get("n") or {}).items()
        if isinstance(value, (int, float))
    }


def run_closed_loop_policy(
    scenario: UnityHiddenBehaviorScenario,
    policy: ClosedLoopPolicy,
    initial_row: Mapping[str, Any],
    dispatch: Callable[[PolicyAction], Mapping[str, Any] | Sequence[Mapping[str, Any]]],
    *,
    dispatch_batch: Callable[[Sequence[PolicyAction]], Mapping[str, Any] | Sequence[Mapping[str, Any]]] | None = None,
    group_totals: Mapping[str, int],
    origin: Mapping[str, float] | None = None,
    extent: Mapping[str, float] | None = None,
    on_checkpoint: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> PolicyRunResult:
    result = _run_closed_loop_policy(
        scenario, policy, initial_row, dispatch,
        dispatch_batch=dispatch_batch,
        group_totals=group_totals, origin=origin, extent=extent,
        on_checkpoint=on_checkpoint,
    )


    if scenario.goal.observations:
        route = Route.from_dict({"route_id": scenario.id, "tier": 2,
                                 "goal": scenario.goal.to_dict()})
        minimum = {axis: float((origin or {}).get(axis, 0)) for axis in ("x", "y", "z")}
        maximum = {axis: minimum[axis] + float((extent or {}).get(axis, 0)) for axis in minimum}
        reading = reading_from_report(route, {
            "rows": list(result.rows), "group_totals": dict(group_totals),
            "bounds": {"min": minimum, "max": maximum},
            "stop_reason": "goal_reached" if result.goal_reached else "budget_exhausted",
        }, log="")
        missing = tuple(sorted(set(result.missing_observations) | set(reading.missing_groups)))
        result = replace(result,
                         observations_reached=tuple(reading.observations_reached),
                         observations_triggered=tuple(reading.observations_triggered),
                         missing_observations=missing,
                         status="inconclusive" if missing else result.status,
                         goal_reached=False if missing else result.goal_reached)
    return result


def _run_closed_loop_policy(
    scenario: UnityHiddenBehaviorScenario,
    policy: ClosedLoopPolicy,
    initial_row: Mapping[str, Any],
    dispatch: Callable[[PolicyAction], Mapping[str, Any] | Sequence[Mapping[str, Any]]],
    *,
    dispatch_batch: Callable[[Sequence[PolicyAction]], Mapping[str, Any] | Sequence[Mapping[str, Any]]] | None = None,
    group_totals: Mapping[str, int],
    origin: Mapping[str, float] | None = None,
    extent: Mapping[str, float] | None = None,
    on_checkpoint: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> PolicyRunResult:


    rows: list[Mapping[str, Any]] = [dict(initial_row)]
    pending: list[Mapping[str, Any]] = []
    actions: list[PolicyAction] = []
    reached: list[str] = []
    invariant_failures: list[str] = []
    missing: set[str] = set()
    frames_used = 0
    start = dict(initial_row)
    start_player = (
        {
            "x": float(start.get("px", 0.0)),
            "y": float(start.get("py", 0.0)),
            "z": float(start.get("pz", 0.0)),
        }
        if "px" in start else None
    )
    goal = parse_predicate(scenario.goal.predicate)
    source_goal = parse_predicate(scenario.source_goal.predicate) if scenario.source_goal else None
    end_goal = parse_predicate(scenario.goal.end_predicate) if scenario.goal.end_predicate else None
    milestones = [(item, parse_predicate(item.predicate)) for item in scenario.goal.milestones]
    source_milestones = [
        (item, parse_predicate(item.predicate))
        for item in (scenario.source_goal.milestones if scenario.source_goal else ())
    ]
    source_reached: list[str] = []
    capture_predicates = [
        (item.id, parse_predicate(item.predicate))
        for item in scenario.capture_checkpoints
        if item.predicate and item.predicate != scenario.goal.predicate
    ]
    emitted_checkpoints: set[str] = set()

    def emit_checkpoint(checkpoint_id: str, row: Mapping[str, Any]) -> None:
        if checkpoint_id in emitted_checkpoints:
            return
        emitted_checkpoints.add(checkpoint_id)
        if on_checkpoint is not None:
            on_checkpoint(checkpoint_id, row)
    invariants = [(item, parse_predicate(item.predicate)) for item in scenario.goal.invariants]


    for predicate in [goal, *([source_goal] if source_goal else []),
                      *([end_goal] if end_goal else []), *(p for _, p in milestones),
                      *(p for _, p in source_milestones), *(p for _, p in invariants)]:
        for call in predicate.calls:
            if call.name in {"contact", "standing_on"}:
                device_id = f"{call.args[0]}#{int(call.args[1])}"
                devices = dict(start.get("d") or {})
                if not any(key == device_id or key.endswith("/" + device_id) for key in devices):
                    missing.add("device:" + device_id)

    def semantic_observation(
        row: Mapping[str, Any], checkpoint: Milestone | None = None
    ) -> dict[str, Any]:
        baseline = start
        if (
            checkpoint is not None
            and checkpoint.baseline == BASELINE_PREVIOUS_ROW
            and len(rows) > 1
        ):
            baseline = dict(rows[-2])
        baseline_groups = {
            str(key): int(value)
            for key, value in dict(baseline.get("g") or {}).items()
        }
        return observation_from_row(
            dict(row),
            dict(group_totals),
            start_player=start_player,
            origin=dict(origin or {}),
            extent=dict(extent or {}),
            start_numeric=_numeric(baseline),
            start_groups=baseline_groups,
            start_devices=dict(baseline.get("d") or {}),
            whole_game_clear=bool(row.get("wgc", False)),
        )


    while True:
        current = dict(rows[-1])
        observation = semantic_observation(current)
        for checkpoint_id, predicate in capture_predicates:
            if checkpoint_id in emitted_checkpoints:
                continue
            value, _capture_missing = predicate.evaluate_detail(observation)
            if value:
                emit_checkpoint(checkpoint_id, current)
        for checkpoint, predicate in invariants:
            scoped = semantic_observation(current, checkpoint)
            if checkpoint.trigger:
                triggered, trigger_missing = parse_predicate(
                    checkpoint.trigger
                ).evaluate_detail(observation)
                missing.update(trigger_missing)
                if not triggered:
                    continue
            value, absent = predicate.evaluate_detail(scoped)
            missing.update(absent)
            if not value and not absent and checkpoint.name not in invariant_failures:
                invariant_failures.append(checkpoint.name)
        if invariant_failures:
            return PolicyRunResult(
                "fail", "scenario invariant failed", False, frames_used,
                tuple(actions), tuple(rows), tuple(reached), tuple(invariant_failures),
                tuple(sorted(missing)),
            )
        if len(reached) < len(milestones):
            checkpoint, predicate = milestones[len(reached)]
            scoped = semantic_observation(current, checkpoint)
            if checkpoint.trigger:
                triggered, trigger_missing = parse_predicate(
                    checkpoint.trigger
                ).evaluate_detail(observation)
                missing.update(trigger_missing)
                if not triggered:
                    triggered = False
            else:
                triggered = True
            value, absent = predicate.evaluate_detail(scoped) if triggered else (False, [])
            missing.update(absent)
            if value:
                reached.append(checkpoint.name)
                emit_checkpoint(checkpoint.name, current)
        if len(source_reached) < len(source_milestones):
            checkpoint, predicate = source_milestones[len(source_reached)]
            scoped = semantic_observation(current, checkpoint)
            value, absent = predicate.evaluate_detail(scoped)
            missing.update(absent)
            if value:
                source_reached.append(checkpoint.name)
                emit_checkpoint(checkpoint.name, current)
        goal_value, absent = goal.evaluate_detail(observation)
        missing.update(absent)
        source_value, source_absent = (
            source_goal.evaluate_detail(observation) if source_goal else (True, [])
        )
        missing.update(source_absent)
        if (goal_value and not absent and source_value and not source_absent
                and len(reached) == len(milestones)
                and len(source_reached) == len(source_milestones) and not missing):
            if end_goal is not None:
                end_value, end_missing = end_goal.evaluate_detail(observation)
                missing.update(end_missing)
                if not end_value or end_missing:
                    return PolicyRunResult(
                        "inconclusive" if missing else "fail",
                        "terminal observation does not satisfy end_predicate",
                        False, frames_used, tuple(actions), tuple(rows), tuple(reached),
                        (), tuple(sorted(missing)),
                    )
            emit_checkpoint("goal", current)
            return PolicyRunResult(
                "pass", "goal, source goal, and ordered milestones reached", True, frames_used,
                tuple(actions), tuple(rows), tuple(reached), (), tuple(sorted(missing)),
                source_milestones_reached=tuple(source_reached),
            )

        if pending:
            rows.append(pending.pop(0))
            continue
        if frames_used >= scenario.budget_frames:
            break
        action = policy.step(observation)
        if action is None:
            return PolicyRunResult(
                "inconclusive", "policy produced no next action", False, frames_used,
                tuple(actions), tuple(rows), tuple(reached), (), tuple(sorted(missing)),
            )
        remaining = scenario.budget_frames - frames_used
        if action.hold_frames <= 0:
            return PolicyRunResult(
                "inconclusive", "policy produced a non-positive action horizon", False,
                frames_used, tuple(actions), tuple(rows), tuple(reached), (),
                tuple(sorted(missing)),
            )
        bounded = PolicyAction(
            action.canonical_action,
            action.value,
            min(action.hold_frames, remaining),
            action.actions,
            action.axes,
        )
        dispatched = [bounded]


        if (
            dispatch_batch is not None
            and isinstance(policy, DeclarativeClosedLoopPolicy)
            and not policy.rules
        ):
            batch_frames = bounded.hold_frames
            while len(dispatched) < 64 and batch_frames < remaining:
                next_action = policy.step(observation)
                if next_action is None or next_action.hold_frames <= 0:
                    break
                room = remaining - batch_frames
                next_bounded = PolicyAction(
                    next_action.canonical_action,
                    next_action.value,
                    min(next_action.hold_frames, room),
                    next_action.actions,
                    next_action.axes,
                )
                dispatched.append(next_bounded)
                batch_frames += next_bounded.hold_frames
            response = dispatch_batch(dispatched)
        else:
            response = dispatch(bounded)
        batch = [dict(response)] if isinstance(response, Mapping) else [dict(row) for row in response]
        last_frame = int(current.get("f", -1))
        valid = bool(batch)
        for row in batch:
            frame = row.get("f")
            if not isinstance(frame, int) or isinstance(frame, bool) or frame <= last_frame:
                valid = False
                break
            last_frame = frame
        if not valid:
            return PolicyRunResult(
                "inconclusive", "controller returned a non-monotonic semantic frame", False,
                frames_used, tuple(actions), tuple(rows), tuple(reached), (),
                tuple(sorted(missing)),
            )
        actions.extend(dispatched)
        rows.append(batch[0])
        pending.extend(batch[1:])
        frames_used += sum(item.hold_frames for item in dispatched)

    return PolicyRunResult(
        "inconclusive" if missing else "fail",
        "required observations were missing" if missing else "policy exhausted the evaluator-owned frame budget",
        False, frames_used,
        tuple(actions), tuple(rows), tuple(reached), tuple(invariant_failures),
        tuple(sorted(missing)), source_milestones_reached=tuple(source_reached),
    )


__all__ = [
    "ClosedLoopPolicy",
    "DeclarativeClosedLoopPolicy",
    "DeclarativePolicyRule",
    "POLICY_SCHEMA",
    "PolicyAction",
    "PolicyRunResult",
    "load_declarative_policies",
    "run_closed_loop_policy",
]
