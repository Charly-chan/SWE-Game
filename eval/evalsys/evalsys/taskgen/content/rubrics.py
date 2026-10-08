


from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...interface.contract import GROUPS
from ...routes.agent import CANONICAL_ACTIONS
from ...routes.schema import (
    BASELINE_SEGMENT_START,
    MILESTONE_BASELINES,
    Milestone,
    validate_predicate,
)


CATALOG_PATH = Path(__file__).with_name("rubric_catalog.json")
ALLOWED_OBSERVABLES = frozenset({"interface_contract", "trace_checkpoint", "counterfactual"})
ALLOWED_SOURCE_BASIS = frozenset({
    "gdd_specified", "skeleton_task_specified", "video_observed",
    "asset_observable", "unshown_reference_only",
})
DECLARED_ID = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
FORBIDDEN_RUBRIC_TEXT = re.compile(
    r"(?:/data2/|/root/|res://|[A-Za-z0-9_./-]+\.gd(?::\d+)?|"
    r"certificate\.rt1|official_routes|gold (?:tape|route)|--gb-route-plan)",
    re.I,
)


class RubricError(ValueError):
    pass


def load_rubric_catalog(path: str | Path = CATALOG_PATH) -> dict[str, dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise RubricError(f"rubric catalog is missing: {source}")
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RubricError("rubric catalog must be a game_id -> rubric object")
    out: dict[str, dict[str, Any]] = {}
    for game_id, value in raw.items():
        if not isinstance(value, dict):
            raise RubricError(f"rubric {game_id!r} must be an object")
        out[str(game_id)] = dict(value)
    return out


def audit_rubric(game_id: str, rubric: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    from ...scard.task_visual import visual_budget

    try:
        visual_budget(rubric)
    except ValueError as exc:
        errors.append(str(exc))
    serialized = json.dumps(rubric, ensure_ascii=False)
    leak = FORBIDDEN_RUBRIC_TEXT.search(serialized)
    if leak:
        errors.append(f"implementation/evaluator detail leaked: {leak.group(0)!r}")
    if int(rubric.get("min_levels") or 0) < 1:
        errors.append("min_levels must be >= 1")

    actions = {str(item) for item in rubric.get("required_actions") or []}
    unknown_actions = sorted(actions - set(CANONICAL_ACTIONS))
    if unknown_actions:
        errors.append("unknown canonical actions: " + ", ".join(unknown_actions))
    extended = {str(item) for item in rubric.get("required_extended_actions") or []}
    axes = {str(item) for item in rubric.get("required_analog_axes") or []}
    bad_declared = sorted(item for item in extended | axes if not DECLARED_ID.fullmatch(item))
    if bad_declared:
        errors.append("invalid extended/axis ids: " + ", ".join(bad_declared))
    collisions = sorted(extended & axes)
    if collisions:
        errors.append("ids cannot be both extended actions and axes: " + ", ".join(collisions))
    errors.extend(_audit_extended_action_count(rubric, extended))

    groups = {str(item) for item in rubric.get("required_groups") or []}
    unknown_groups = sorted(groups - set(GROUPS))
    if unknown_groups:
        errors.append("unknown GB groups: " + ", ".join(unknown_groups))

    numeric = {str(item) for item in rubric.get("required_numeric_slots") or []}
    unknown_numeric = sorted(numeric - {"progress", "health", "timer", "score"})
    if unknown_numeric:
        errors.append("unknown numeric slots: " + ", ".join(unknown_numeric))

    checks = rubric.get("mechanic_checks") or []
    if not isinstance(checks, list) or not checks:
        errors.append("mechanic_checks must be a non-empty list")
        checks = []
    seen: set[str] = set()
    executable = 0
    runtime_checkpoints = 0
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            errors.append(f"mechanic_checks[{index}] must be an object")
            continue
        check_id = str(check.get("id") or "")
        if not check_id or check_id in seen:
            errors.append(f"mechanic_checks[{index}] has an empty/duplicate id")
        seen.add(check_id)
        if not str(check.get("claim") or "").strip():
            errors.append(f"mechanic_checks[{index}] has no observable claim")
        basis = check.get("source_basis") or []
        if not isinstance(basis, list) or not basis:
            errors.append(f"mechanic_checks[{index}] has no source_basis")
            basis = []
        unknown_basis = sorted(str(item) for item in basis if str(item) not in ALLOWED_SOURCE_BASIS)
        if unknown_basis:
            errors.append(
                f"mechanic_checks[{index}] has unknown source_basis: "
                + ", ".join(unknown_basis)
            )
        if "gdd_specified" not in basis:
            errors.append(f"mechanic_checks[{index}] is not grounded in the published Mode-2 GDD")
        if "skeleton_task_specified" not in basis:
            errors.append(
                f"mechanic_checks[{index}] is not disclosed in the Mode-3 skeleton task"
            )
        measurable = check.get("measurable") is not False
        observable = check.get("observable") or {}
        if not isinstance(observable, dict):
            errors.append(f"mechanic_checks[{index}].observable must be an object")
            continue
        kind = str(observable.get("kind") or "")
        requires_action = observable.get("requires_player_action", True)
        if not isinstance(requires_action, bool):
            errors.append(f"mechanic_checks[{index}] requires_player_action must be boolean")
        if requires_action is False and not str(observable.get("automatic_reason") or "").strip():
            errors.append(f"mechanic_checks[{index}] automatic behavior needs an automatic_reason")
        required = observable.get("required_on_witness", True)
        if not isinstance(required, bool):
            errors.append(f"mechanic_checks[{index}] required_on_witness must be boolean")
        if required is False and not str(observable.get("witness_scope_reason") or "").strip():
            errors.append(f"mechanic_checks[{index}] optional witness check needs a witness_scope_reason")
        if kind not in ALLOWED_OBSERVABLES:
            errors.append(f"mechanic_checks[{index}] has unsupported observable kind {kind!r}")
            continue
        if measurable and kind == "trace_checkpoint":
            predicate = str(observable.get("predicate") or "")
            finding = validate_predicate(predicate)
            baseline = str(observable.get("baseline") or BASELINE_SEGMENT_START)
            trigger = str(observable.get("trigger") or "")
            trigger_finding = validate_predicate(trigger) if trigger else None
            if not finding.ok:
                errors.append(
                    f"mechanic_checks[{index}] predicate rejected: "
                    + "; ".join(finding.findings)
                )
            elif trigger_finding is not None and not trigger_finding.ok:
                errors.append(
                    f"mechanic_checks[{index}] trigger rejected: "
                    + "; ".join(trigger_finding.findings)
                )
            elif baseline not in MILESTONE_BASELINES:
                errors.append(
                    f"mechanic_checks[{index}] baseline must be one of "
                    f"{sorted(MILESTONE_BASELINES)}, got {baseline!r}"
                )
            else:
                executable += 1
                runtime_checkpoints += 1
        elif measurable and kind == "interface_contract":
            executable += 1
        elif measurable and kind == "counterfactual":
            errors.append(
                f"mechanic_checks[{index}] counterfactual has no generic executor; "
                "mark it unmeasurable or add an evaluator-owned probe"
            )
        elif not measurable and not str(check.get("reason") or "").strip():
            errors.append(f"mechanic_checks[{index}] is unmeasurable without a reason")

    if runtime_checkpoints < 1:
        errors.append("at least one executable trace_checkpoint is required")
    return {
        "game_id": game_id,
        "ready": not errors,
        "errors": errors,
        "checks": len(checks),
        "executable_checks": executable,
        "runtime_checkpoints": runtime_checkpoints,
    }


def required_extended_action_count(rubric: dict[str, Any]) -> int:

    raw = rubric.get("required_extended_action_count")
    if raw is None or isinstance(raw, bool):
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def unity_v1_schema_blockers(rubric: dict[str, Any]) -> list[str]:


    extended = sorted(
        {str(item).strip() for item in rubric.get("required_extended_actions") or []}
        - {""}
    )
    axes = sorted(
        {str(item).strip() for item in rubric.get("required_analog_axes") or []}
        - {""}
    )
    count = required_extended_action_count(rubric)
    blockers: list[str] = []
    if extended or count:
        requirement = ", ".join(extended) if extended else f"at least {count} unnamed verbs"
        blockers.append(
            "Mode 5 schema v1 cannot express required extended actions: " + requirement
        )
    if axes:
        blockers.append(
            "Mode 5 schema v1 cannot express required analog axes: " + ", ".join(axes)
        )
    return blockers


def extended_action_hints(rubric: dict[str, Any]) -> list[str]:
    hints = rubric.get("extended_action_hints") or []
    if not isinstance(hints, list):
        return []
    return [str(item).strip() for item in hints if str(item).strip()]


def _audit_extended_action_count(rubric: dict[str, Any], exact_ids: set[str]) -> list[str]:
    errors: list[str] = []
    raw = rubric.get("required_extended_action_count")
    if raw is not None and (isinstance(raw, bool) or not isinstance(raw, int) or raw < 0):
        errors.append("required_extended_action_count must be a non-negative integer")
        return errors
    count = required_extended_action_count(rubric)


    if exact_ids and count > len(exact_ids):
        errors.append(
            f"required_extended_action_count {count} exceeds the "
            f"{len(exact_ids)} named required_extended_actions"
        )
    raw_hints = rubric.get("extended_action_hints")
    if raw_hints is not None and not isinstance(raw_hints, list):
        errors.append("extended_action_hints must be a list of short phrases")
    elif raw_hints:
        for index, hint in enumerate(raw_hints):
            if not isinstance(hint, str) or not hint.strip():
                errors.append(f"extended_action_hints[{index}] must be a non-empty phrase")
            elif "`" in hint:


                errors.append(f"extended_action_hints[{index}] must not name an id in backticks")
        if count == 0:
            errors.append("extended_action_hints given without a required_extended_action_count")
    return errors


_EXTENDED_WHAT = (
    "`gb_levels.json.extended_actions` (extra InputMap verbs beyond the eight "
    "canonical `gb_*` actions, each bound to a real key and read in gameplay code)"
)
_EXTENDED_MASH = "Holding every declared extra from frame 0 must not clear the game."


def describe_extended_action_requirement(
    rubric: dict[str, Any], *, mode: str = "brief"
) -> str:


    exact = [str(item) for item in rubric.get("required_extended_actions") or []]
    if mode != "brief" and exact:
        ids = ", ".join(f"`{item}`" for item in exact)
        return (
            f"This task requires the extended actions {ids}, with exactly those ids, "
            f"declared in {_EXTENDED_WHAT}. {_EXTENDED_MASH}"
        )
    count = required_extended_action_count(rubric)
    if count < 1:
        return ""
    noun = "extended action" if count == 1 else "extended actions"
    sentence = f"This task requires at least {count} declared {noun} in {_EXTENDED_WHAT}"
    hints = extended_action_hints(rubric)
    if hints:
        sentence += " covering: " + "; ".join(hints)
    return sentence + f". Choose your own stable ids. {_EXTENDED_MASH}"


def require_rubric(game_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = load_rubric_catalog()
    rubric = catalog.get(game_id)
    if rubric is None:
        raise RubricError(f"no hidden rubric for {game_id!r}")
    audit = audit_rubric(game_id, rubric)
    if not audit["ready"]:
        raise RubricError(
            f"hidden rubric for {game_id!r} is not publishable: "
            + "; ".join(audit["errors"])
        )
    return rubric, audit


def rubric_milestones(rubric: dict[str, Any]) -> list[Milestone]:

    out: list[Milestone] = []
    for check in rubric.get("mechanic_checks") or []:
        if not isinstance(check, dict) or check.get("measurable") is False:
            continue
        observable = check.get("observable") or {}
        if not isinstance(observable, dict) or observable.get("kind") != "trace_checkpoint":
            continue
        predicate = str(observable.get("predicate") or "")
        trigger = str(observable.get("trigger") or "")
        if trigger and not validate_predicate(trigger).ok:
            continue
        if validate_predicate(predicate).ok:
            out.append(
                Milestone(
                    str(check.get("id") or f"m{len(out) + 1}"),
                    predicate,
                    baseline=str(observable.get("baseline") or BASELINE_SEGMENT_START),
                    trigger=trigger,
                )
            )
    return out


def action_required_checkpoints(rubric: dict[str, Any]) -> set[str]:


    expected = {m.name for m in rubric_milestones(rubric)}
    automatic = {
        str(check.get("id")) for check in rubric.get("mechanic_checks") or []
        if (check.get("observable") or {}).get("requires_player_action") is False
    }
    return expected - automatic


def extended_action_count_finding(declared: int, rubric: dict[str, Any]) -> str:

    count = required_extended_action_count(rubric)
    if declared >= count:
        return ""
    detail = (
        f"declares {declared} extended action(s), task requires at least {count}"
    )
    hints = extended_action_hints(rubric)
    if hints:
        detail += " covering: " + "; ".join(hints)
    return detail


def interface_rubric_findings(
    interface: Any, rubric: dict[str, Any], *, mode: str = ""
) -> list[str]:


    findings: list[str] = []
    levels = tuple(getattr(interface, "levels", ()) or ())
    minimum = int(rubric.get("min_levels") or 1)
    if len(levels) < minimum:
        findings.append(f"levels {len(levels)} < required {minimum}")

    bound = set(getattr(getattr(interface, "actions", None), "bound", ()) or ())
    unread = set(getattr(getattr(interface, "actions", None), "unread", ()) or ())
    for action in rubric.get("required_actions") or []:
        if action not in bound:
            findings.append(f"required action {action} is unbound")
        elif action in unread:
            findings.append(f"required action {action} is bound but never read")

    extended = set(getattr(interface, "extended_action_ids", ()) or ())
    count_finding = extended_action_count_finding(len(extended), rubric)
    if count_finding:
        findings.append(count_finding)
    if mode != "brief":
        for action in rubric.get("required_extended_actions") or []:
            if action not in extended:
                findings.append(f"required extended action {action} is not declared")

    axes = set(getattr(interface, "analog_axis_ids", ()) or ())
    for axis in rubric.get("required_analog_axes") or []:
        if axis not in axes:
            findings.append(f"required analog axis {axis} is not declared")

    present_groups = set(getattr(getattr(interface, "groups", None), "present", ()) or ())
    for group in rubric.get("required_groups") or []:
        if group not in present_groups:
            findings.append(f"required group {group} is absent")

    numeric = set(dict(getattr(interface, "numeric", {}) or {}))
    for slot in rubric.get("required_numeric_slots") or []:
        if slot not in numeric:
            findings.append(f"required numeric slot {slot} is not declared")

    endings = getattr(interface, "endings", None)
    if rubric.get("require_failure_ending") and not tuple(getattr(endings, "failure", ()) or ()):
        findings.append("a distinct failure ending is required")
    return findings


def require_reference_interface(project: str | Path, rubric: dict[str, Any]) -> None:

    from ...interface import load_submission_interface

    findings = interface_rubric_findings(load_submission_interface(project), rubric, mode="gdd")
    if findings:
        raise RubricError("reference rubric_interface failed: " + "; ".join(findings))
