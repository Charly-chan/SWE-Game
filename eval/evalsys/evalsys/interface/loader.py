

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .conformance import ConformanceReport, with_requirements
from .contract import (
    ACTIONS, ANALOG_AXIS_ID_PATTERN, ANALOG_AXIS_STEPS_MAX,
    ANALOG_AXIS_STEPS_MIN, ANALOG_AXES_MAX, DEVICE_ID_PATTERN, DEVICE_KINDS,
    EXTENDED_ACTION_ID_PATTERN, EXTENDED_ACTION_RESERVED_NAMES,
    EXTENDED_ACTION_RESERVED_PREFIXES, EXTENDED_ACTION_WHY_MIN_LENGTH,
    EXTENDED_ACTIONS_MAX, FAILURE_ENDINGS, GROUPS,
    INTERFACE_VERSION, NUMERIC_SLOTS, REQUIRED_GROUPS, SUCCESS_ENDINGS,
)
from .model import (
    ActionDeclaration, AnalogAxis, CameraAddress, EndingVocabulary,
    ExtendedAction, GroupDeclaration, LevelAddress, NodeAddress, Predicate,
    SubmissionInterface,
)
from .requirements import TaskInterfaceRequirements

DEVICE_ID_RE = re.compile(DEVICE_ID_PATTERN)
EXTENDED_ACTION_ID_RE = re.compile(EXTENDED_ACTION_ID_PATTERN)
ANALOG_AXIS_ID_RE = re.compile(ANALOG_AXIS_ID_PATTERN)
HUMAN_BIND_RE = re.compile(
    r'"(?:keycode|physical_keycode|button_index)"\s*:\s*([1-9]\d*)'
)
AXIS_VALUE_RE = re.compile(
    r'"axis_value"\s*:\s*(-?(?:[1-9]\d*(?:\.\d+)?|0\.\d*[1-9]\d*))'
)


RESERVED_EXTENDED_NAMES = frozenset(ACTIONS) | frozenset(EXTENDED_ACTION_RESERVED_NAMES)
RESERVED_EXTENDED_PREFIXES = tuple(EXTENDED_ACTION_RESERVED_PREFIXES)


def _reserved_extended_id(ident: str) -> bool:
    return ident in RESERVED_EXTENDED_NAMES or ident.startswith(RESERVED_EXTENDED_PREFIXES)


def project_root(project: str | Path) -> Path:

    candidate = Path(project).resolve()
    if (candidate / "project.godot").is_file():
        return candidate
    if candidate.is_dir():
        nested = sorted(
            child for child in candidate.iterdir()
            if child.is_dir() and (child / "project.godot").is_file()
        )
        if len(nested) == 1:
            return nested[0]
    return candidate


def _check(status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "detail": detail, **extra}


def _source_text(root: Path) -> str:
    parts: list[str] = []
    for pattern in ("*.gd", "*.tscn"):
        for path in root.rglob(pattern):
            if any(part in {".godot", ".git", "inputs"} for part in path.parts):
                continue
            try:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(parts)


def _action_sections(project_text: str) -> dict[str, bool]:
    found: dict[str, bool] = {}
    for action in ACTIONS:
        body = _action_body(project_text, action)
        found[action] = bool(re.search(r'["\']?events["\']?\s*[:=]\s*\[(?!\s*\])', body))
    return found


def _balanced_dictionary(text: str, opening: int) -> str:


    depth = 0
    quote = ""
    escaped = False
    for index in range(opening, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[opening:index + 1]
    return ""


def _input_dictionary(project_text: str, name: str) -> str:
    match = re.search(r"(?ms)^\[input\]\s*$.*?(?=^\[(?!input/)|\Z)", project_text)
    block = match.group(0) if match else ""
    assignment = re.search(
        rf"(?m)^[ \t]*{re.escape(name)}[ \t]*=[ \t]*(\{{)", block
    )
    if not assignment:
        return ""
    dictionary = _balanced_dictionary(block, assignment.start(1))
    return block[assignment.start():assignment.start(1)] + dictionary if dictionary else ""


def _action_body(project_text: str, name: str) -> str:
    section = re.search(
        rf"(?ms)^\[input/{re.escape(name)}\]\s*$.*?(?=^\[|\Z)",
        project_text,
    )
    if section:
        return section.group(0)
    return _input_dictionary(project_text, name)


def action_has_human_binding(project_text: str, name: str) -> bool:


    return bool(HUMAN_BIND_RE.search(_action_body(project_text, name)))


def action_has_axis_binding(project_text: str, name: str) -> bool:


    body = _action_body(project_text, name)
    return "InputEventJoypadMotion" in body and bool(AXIS_VALUE_RE.search(body))


def analog_zero_on_grid(lo: float, hi: float, steps: int) -> bool:

    if not (lo < 0.0 < hi):
        return True
    if steps < 2:
        return False
    span = hi - lo
    for index in range(steps):
        if abs(lo + index * span / (steps - 1)) <= 1e-9:
            return True
    return False


def action_read_in_source(source: str, name: str) -> bool:


    return f'"{name}"' in source or f"'{name}'" in source


def _parse_extended_actions(
    raw: Any,
    *,
    project_text: str,
    source: str,
) -> tuple[tuple[ExtendedAction, ...], list[str], str]:

    if raw in (None, [], ()):
        return (), [], "no extended actions declared"
    if not isinstance(raw, list):
        return (), ["gb_levels.json.extended_actions"], "extended_actions must be an array"
    if len(raw) > EXTENDED_ACTIONS_MAX:
        return (
            (),
            ["gb_levels.json.extended_actions"],
            f"extended_actions has {len(raw)} entries; max is {EXTENDED_ACTIONS_MAX}",
        )
    seen: set[str] = set()
    out: list[ExtendedAction] = []
    problems: list[str] = []
    for index, item in enumerate(raw):
        prefix = f"extended_actions[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{prefix} must be an object with id and why")
            continue
        ident = str(item.get("id") or "").strip()
        why = str(item.get("why") or "").strip()
        if not EXTENDED_ACTION_ID_RE.fullmatch(ident):
            problems.append(f"{prefix}.id {ident!r} must match {EXTENDED_ACTION_ID_PATTERN}")
            continue
        if _reserved_extended_id(ident):
            problems.append(
                f"{prefix}.id {ident!r} is reserved (canonical, forbidden, gb_*, or ui_*)"
            )
            continue
        if ident in seen:
            problems.append(f"{prefix}.id {ident!r} is duplicated")
            continue
        if len(why) < EXTENDED_ACTION_WHY_MIN_LENGTH:
            problems.append(f"{prefix}.why must justify why six verbs cannot do this")
            continue
        if not action_has_human_binding(project_text, ident):
            problems.append(
                f"{prefix}.id {ident!r} is not bound to a real key/button in InputMap"
            )
            continue
        if not action_read_in_source(source, ident):
            problems.append(
                f"{prefix}.id {ident!r} is never read as a string literal in "
                "gameplay source; the declared set must be minimal"
            )
            continue
        seen.add(ident)
        out.append(ExtendedAction(ident, why))
    if problems:
        return (), ["gb_levels.json.extended_actions"], "; ".join(problems[:8])
    return tuple(out), [], f"{len(out)} declared extra action(s) on the load-bearing InputMap"


def _parse_analog_axes(
    raw: Any,
    *,
    project_text: str,
    source: str,
    taken_ids: set[str],
) -> tuple[tuple[AnalogAxis, ...], list[str], str]:

    if raw in (None, [], ()):
        return (), [], "no analog axes declared"
    if not isinstance(raw, list):
        return (), ["gb_levels.json.analog_axes"], "analog_axes must be an array"
    if len(raw) > ANALOG_AXES_MAX:
        return (
            (),
            ["gb_levels.json.analog_axes"],
            f"analog_axes has {len(raw)} entries; max is {ANALOG_AXES_MAX}",
        )
    seen: set[str] = set()
    out: list[AnalogAxis] = []
    problems: list[str] = []
    for index, item in enumerate(raw):
        prefix = f"analog_axes[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{prefix} must be an object with id, why, min, max, steps")
            continue
        ident = str(item.get("id") or "").strip()
        why = str(item.get("why") or "").strip()
        if not ANALOG_AXIS_ID_RE.fullmatch(ident):
            problems.append(f"{prefix}.id {ident!r} must match {ANALOG_AXIS_ID_PATTERN}")
            continue
        if _reserved_extended_id(ident):
            problems.append(
                f"{prefix}.id {ident!r} is reserved (canonical, forbidden, gb_*, or ui_*)"
            )
            continue
        if ident in taken_ids or ident in seen:
            problems.append(f"{prefix}.id {ident!r} collides with an extra action or another axis")
            continue
        if len(why) < EXTENDED_ACTION_WHY_MIN_LENGTH:
            problems.append(f"{prefix}.why must justify why six verbs cannot do this")
            continue
        lo_raw, hi_raw, steps_raw = item.get("min"), item.get("max"), item.get("steps")
        if isinstance(lo_raw, bool) or isinstance(hi_raw, bool) or isinstance(steps_raw, bool):
            problems.append(f"{prefix} min/max/steps must be numbers, not booleans")
            continue
        try:
            lo = float(lo_raw)
            hi = float(hi_raw)
            steps = int(steps_raw)
        except (TypeError, ValueError):
            problems.append(f"{prefix} needs numeric min, max, and integer steps")
            continue
        if lo != lo or hi != hi or lo >= hi:
            problems.append(f"{prefix} needs finite min < max")
            continue
        if type(steps_raw) is float and steps_raw != int(steps_raw):
            problems.append(f"{prefix}.steps must be an integer")
            continue
        if not (ANALOG_AXIS_STEPS_MIN <= steps <= ANALOG_AXIS_STEPS_MAX):
            problems.append(
                f"{prefix}.steps {steps} is outside "
                f"[{ANALOG_AXIS_STEPS_MIN}, {ANALOG_AXIS_STEPS_MAX}]"
            )
            continue
        if not analog_zero_on_grid(lo, hi, steps):
            problems.append(
                f"{prefix} signed range must include 0 on the grid "
                "(use odd steps when min < 0 < max)"
            )
            continue
        if not action_has_axis_binding(project_text, ident):
            problems.append(
                f"{prefix}.id {ident!r} is not bound to a real JoypadMotion in InputMap"
            )
            continue
        if not action_read_in_source(source, ident):
            problems.append(
                f"{prefix}.id {ident!r} is never read as a string literal in "
                "gameplay source; the declared set must be minimal"
            )
            continue
        seen.add(ident)
        out.append(AnalogAxis(ident, why, lo, hi, steps))
    if problems:
        return (), ["gb_levels.json.analog_axes"], "; ".join(problems[:8])
    return tuple(out), [], f"{len(out)} declared analog axis/axes on the load-bearing InputMap"


NUMERIC_FORM_PROPERTY = "property_name"
NUMERIC_FORM_DOTTED = "dotted_owner_path"
NUMERIC_FORM_NODE_PATH = "node_path"
NUMERIC_FORM_SCENE_ADDRESS = "scene_address"
NUMERIC_FORM_OTHER = "other"

_PROPERTY_NAME_RE = re.compile(r"^[A-Za-z_]\w*$")


def classify_numeric_value(value: str, source: str = "") -> dict[str, Any]:


    text = str(value or "").strip()
    if _PROPERTY_NAME_RE.fullmatch(text):
        form = NUMERIC_FORM_PROPERTY
    elif text.startswith("res://") and "::" in text:
        form = NUMERIC_FORM_SCENE_ADDRESS
    elif text.startswith(("/", "$", "%", "../", "./")) or ":" in text:
        form = NUMERIC_FORM_NODE_PATH
    elif "." in text and all(_PROPERTY_NAME_RE.fullmatch(part) for part in text.split(".")):
        form = NUMERIC_FORM_DOTTED
    else:
        form = NUMERIC_FORM_OTHER
    tail = re.split(r"[.:/]", text)[-1] if text else ""
    prop = tail if _PROPERTY_NAME_RE.fullmatch(tail or "") else ""
    declared = bool(prop) and bool(
        re.search(rf"^\s*(?:@\w+(?:\([^)]*\))?\s+)*var\s+{re.escape(prop)}\b", source, re.M)
    )
    return {
        "value": text,
        "form": form,
        "property": prop,
        "driver_resolves": form == NUMERIC_FORM_PROPERTY,
        "declared_in_source": declared,
        "note": (
            "read verbatim by Object.get() on autoloads / current scene root / gb_* nodes / "
            "run|state|run_state|game holders"
            if form == NUMERIC_FORM_PROPERTY else
            f"the driver does not parse this form; declare the bare property name "
            f"{prop!r} instead" if prop else
            "the driver does not parse this form; declare a bare property name"
        ),
    }


LEVELS_ACCEPTED_FORM = (
    'each levels entry must be a "res://…" scene-path string naming a shipped scene '
    "(no objects, no {id, name, scene} records)"
)


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def describe_bad_level(index: int, value: Any) -> str:

    if not isinstance(value, str):
        return f"levels[{index}] is a JSON {_json_type_name(value)}, not a string"

    if not value.startswith("res://"):
        return f"levels[{index}] '{value}' is not a res:// path"
    return f"levels[{index}] '{value}' does not name a shipped scene file"


def unresolved_levels_detail(bad_levels: list[str]) -> str:
    return "unresolved entries: " + ", ".join(bad_levels) + "; " + LEVELS_ACCEPTED_FORM


def _source_hash(project_file: Path, manifest_file: Path) -> str:
    digest = hashlib.sha256()
    for label, path in (("project.godot", project_file), ("gb_levels.json", manifest_file)):
        digest.update(label.encode("ascii") + b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def _normalized_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_submission_interface(
    project: str | Path,
    *,
    requirements: TaskInterfaceRequirements | None = None,
) -> SubmissionInterface:

    root = project_root(project)
    project_file = root / "project.godot"
    manifest_file = root / "gb_levels.json"
    checks: dict[str, dict[str, Any]] = {}
    missing: list[str] = []

    if project_file.is_file():
        project_text = project_file.read_text(encoding="utf-8", errors="replace")
        checks["project_godot"] = _check("pass", "project.godot exists")
    else:
        project_text = ""
        checks["project_godot"] = _check("fail", "project.godot is missing")
        missing.append("project.godot")

    source = _source_text(root) if root.is_dir() else ""
    group_presence = {name: name in source for name in GROUPS}
    absent_groups = [name for name, present in group_presence.items() if not present]
    missing_required_groups = [name for name in REQUIRED_GROUPS if not group_presence[name]]
    groups = GroupDeclaration(
        present=tuple(name for name, present in group_presence.items() if present),
        absent=tuple(absent_groups),
    )
    checks["groups"] = _check(
        "fail" if missing_required_groups else "pass",
        "fixed group vocabulary scanned; non-player categories may be empty",
        present=list(groups.present), absent=list(groups.absent),
        required_player="missing" if missing_required_groups else "present",
    )
    missing.extend(missing_required_groups)

    action_bindings = _action_sections(project_text)
    absent_actions = [name for name, bound in action_bindings.items() if not bound]
    unread_actions = [
        name for name, bound in action_bindings.items() if bound and name not in source
    ]
    actions = ActionDeclaration(
        bound=tuple(name for name, bound in action_bindings.items() if bound),
        missing=tuple(absent_actions), unread=tuple(unread_actions),
    )
    actions_ok = not absent_actions and not unread_actions
    checks["actions"] = _check(
        "pass" if actions_ok else "fail",
        (
            "all eight fixed aliases have an InputMap event and appear in source"
            if actions_ok else
            "missing or empty InputMap aliases: " + ", ".join(absent_actions)
            if absent_actions else
            "InputMap aliases bound but never read in source: " + ", ".join(unread_actions)
        ),
        bound=list(actions.bound), missing=absent_actions, unread=unread_actions,
    )
    missing.extend(absent_actions)
    missing.extend(unread_actions)

    manifest: dict[str, Any] = {}
    manifest_error = ""
    if manifest_file.is_file():
        try:
            raw = json.loads(manifest_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                manifest = raw
            else:
                manifest_error = "top level is not an object"
        except (OSError, json.JSONDecodeError) as exc:
            manifest_error = str(exc)
    else:
        manifest_error = "file is missing"

    levels_raw = manifest.get("levels")
    level_values = levels_raw if isinstance(levels_raw, list) else []
    levels: list[LevelAddress] = []
    bad_levels: list[str] = []
    for index, value in enumerate(level_values):
        scene = value if isinstance(value, str) else str(value)
        resolved = root / scene.removeprefix("res://") if scene.startswith("res://") else None
        valid = isinstance(value, str) and scene.startswith("res://") and bool(resolved and resolved.is_file())
        levels.append(LevelAddress(scene, resolved if valid else None, valid))
        if not valid:
            bad_levels.append(describe_bad_level(index, value))
    levels_ok = bool(levels) and not bad_levels and not manifest_error
    checks["gb_levels"] = _check(
        "pass" if levels_ok else "fail",
        f"{len(levels)} independently addressable level(s)" if levels_ok else
        f"gb_levels.json invalid: {manifest_error or unresolved_levels_detail(bad_levels)}",
        levels=[level.scene for level in levels], unresolved=bad_levels,
        accepted_form=LEVELS_ACCEPTED_FORM,
    )
    if not levels_ok:
        missing.append("gb_levels.json")

    endings_raw = manifest.get("endings")
    endings_map = {
        str(key): value for key, value in endings_raw.items()
        if isinstance(key, str) and isinstance(value, str)
    } if isinstance(endings_raw, dict) else {}
    success = tuple(sorted(key for key in endings_map if key in SUCCESS_ENDINGS))
    failure = tuple(sorted(key for key in endings_map if key in FAILURE_ENDINGS))
    allowed_endings = SUCCESS_ENDINGS | FAILURE_ENDINGS
    keys_ok = isinstance(endings_raw, dict) and all(key in allowed_endings for key in endings_raw)
    paths_ok = bool(endings_map) and len(endings_map) == len(endings_raw or {}) and all(
        value.startswith("res://") and (root / value.removeprefix("res://")).is_file()
        for value in endings_map.values()
    )
    success_scenes = {endings_map[key] for key in success}
    failure_scenes = {endings_map[key] for key in failure}
    shared = tuple(sorted(success_scenes & failure_scenes))
    discriminating = success_scenes - failure_scenes
    endings_ok = bool(success) and keys_ok and paths_ok and bool(discriminating) and not shared
    endings = EndingVocabulary(MappingProxyType(endings_map), success, failure, shared)
    checks["endings"] = _check(
        "pass" if endings_ok else "fail",
        "success ending vocabulary is declared and distinguishes success from failure"
        if endings_ok else
        "gb_levels.json.endings routes success and failure to the same scene(s) " + ", ".join(shared)
        if shared else "gb_levels.json.endings has no valid success mapping",
        declared=sorted(endings_map), success=list(success), failure=list(failure),
        shared_scenes=list(shared), discriminating_scenes=sorted(discriminating),
    )
    if not endings_ok:
        missing.append("gb_levels.json.endings")

    numeric_raw = manifest.get("numeric", {})
    numeric_ok = isinstance(numeric_raw, dict) and all(
        key in NUMERIC_SLOTS and isinstance(value, str) and bool(value.strip())
        for key, value in numeric_raw.items()
    )
    numeric = MappingProxyType({
        str(key): value.strip() for key, value in numeric_raw.items()
        if key in NUMERIC_SLOTS and isinstance(value, str) and value.strip()
    } if isinstance(numeric_raw, dict) else {})
    numeric_values = {
        slot: classify_numeric_value(value, source) for slot, value in numeric.items()
    }
    unresolvable = sorted(
        slot for slot, info in numeric_values.items() if not info["driver_resolves"]
    )
    numeric_forms_ok = numeric_ok and not unresolvable
    checks["numeric"] = _check(
        "pass" if numeric_forms_ok else "fail",
        (
            "generic numeric mapping is valid; every value is a bare property name the route "
            "driver reads with Object.get() on autoloads, the current scene root, gb_* nodes and "
            "their run/state/run_state/game holders"
            if numeric_forms_ok else
            "numeric value(s) are not bare property names, so the route driver cannot resolve "
            "them as declared: " + ", ".join(
                f"{slot}={numeric[slot]!r} ({numeric_values[slot]['form']})" for slot in unresolvable
            ) + "; write the property name only (e.g. \"health\"), not a node path or owner prefix"
            if numeric_ok else
            "numeric keys must be progress/health/timer/score and values non-empty property names"
        ),
        mapping=dict(numeric) if numeric_ok else numeric_raw, optional=True,
        values=numeric_values,
        accepted_form=(
            "bare property name (^[A-Za-z_]\\w*$) of an int/float readable on an autoload, the "
            "current scene root, a gb_* node, or a run/state/run_state/game object held by one"
        ),
    )

    device_raw = manifest.get("device_ids", {})
    bad_devices: list[str] = []
    device_ids: dict[str, NodeAddress] = {}
    if isinstance(device_raw, dict):
        for key, value in device_raw.items():
            match = DEVICE_ID_RE.fullmatch(str(key))
            kind = str(key).split("/", 1)[-1].split("#", 1)[0]
            if match is None or kind not in DEVICE_KINDS or not isinstance(value, str) or not value.strip():
                bad_devices.append(str(key))
            else:
                device_ids[str(key)] = NodeAddress(str(key), value.strip())
    else:
        bad_devices.append("<not an object>")
    device_ok = not bad_devices
    checks["device_ids"] = _check(
        "pass" if device_ok else "fail",
        "optional generic device ids map to submitted NodePaths" if device_ok else
        "device_ids keys must use the fixed generic vocabulary and values must be non-empty NodePaths",
        optional=True, declared=sorted(device_ids), invalid=sorted(bad_devices),
        vocabulary=sorted(DEVICE_KINDS),
    )

    anchor_raw = manifest.get("anchor_camera")
    anchor: CameraAddress | None = None
    anchor_ok = anchor_raw is None
    if isinstance(anchor_raw, str) and "::" in anchor_raw:
        scene, node = anchor_raw.split("::", 1)
        anchor_ok = scene.startswith("res://") and bool(scene.strip() and node.strip())
        if anchor_ok:
            anchor = CameraAddress(scene.strip(), node.strip())
    checks["anchor_camera"] = _check(
        "pass" if anchor_ok else "fail",
        "optional anchor camera is a scene::NodePath address" if anchor_ok else
        "anchor_camera must be res://scene.tscn::NodePath",
        optional=True, address=anchor_raw,
    )

    audio_raw = manifest.get("audio_buses")
    audio_ok = audio_raw is None or (
        isinstance(audio_raw, list) and bool(audio_raw)
        and all(isinstance(bus, str) and bool(bus.strip()) for bus in audio_raw)
    )
    audio_buses = tuple(bus.strip() for bus in audio_raw) if audio_ok and audio_raw else ()
    checks["audio_buses"] = _check(
        "pass" if audio_ok else "fail",
        "optional audio bus names are valid evaluator addresses" if audio_ok else
        "audio_buses must be a non-empty list of non-empty bus names",
        optional=True, buses=list(audio_buses) if audio_ok else audio_raw,
    )

    clear_raw = manifest.get("level_clear")
    predicate_text = ""
    if isinstance(clear_raw, str):
        predicate_text = clear_raw.strip()
    elif isinstance(clear_raw, dict) and isinstance(clear_raw.get("predicate"), str):
        predicate_text = clear_raw["predicate"].strip()
    clear_ok = clear_raw in (None, "", {})
    findings: list[str] = []
    if not clear_ok:
        from ..routes.schema import validate_predicate
        parsed = validate_predicate(predicate_text) if predicate_text else None
        clear_ok = bool(parsed and parsed.ok)
        findings = list(parsed.findings) if parsed else ["missing predicate"]


    level_clear = Predicate(predicate_text) if (clear_ok and predicate_text) else None
    checks["level_clear"] = _check(
        "pass" if clear_ok else "fail",
        "level_clear is optional; default is gb_goal transition" if clear_raw in (None, "", {}) else
        "level_clear predicate is a legal PREDICATE_FUNCTIONS expression" if clear_ok else
        "level_clear.predicate must be a PREDICATE_FUNCTIONS expression",
        optional=True, predicate=predicate_text, findings=findings,
    )

    entry_raw = manifest.get("level_entry")
    entry_ok = entry_raw is None or (
        isinstance(entry_raw, dict)
        and isinstance(entry_raw.get("kind"), str)
        and bool(entry_raw["kind"].strip())
        and isinstance(entry_raw.get("scene"), str)
        and entry_raw["scene"].startswith("res://")
        and (root / entry_raw["scene"].removeprefix("res://")).is_file()
        and (
            entry_raw.get("selector") is None
            or isinstance(entry_raw.get("selector"), str)
            and bool(entry_raw["selector"].strip())
        )
    )
    level_entry = MappingProxyType(dict(entry_raw)) if entry_ok and entry_raw else MappingProxyType({})
    checks["level_entry"] = _check(
        "pass" if entry_ok else "fail",
        "optional level-entry address is valid" if entry_ok else
        "level_entry requires non-empty kind and a resolvable res:// scene",
        optional=True,
    )

    extended, extended_missing, extended_detail = _parse_extended_actions(
        manifest.get("extended_actions"),
        project_text=project_text,
        source=source,
    )
    checks["extended_actions"] = _check(
        "fail" if extended_missing else "pass",
        extended_detail,
        optional=True,
        declared=[item.to_dict() for item in extended],
    )
    missing.extend(extended_missing)

    analog, analog_missing, analog_detail = _parse_analog_axes(
        manifest.get("analog_axes"),
        project_text=project_text,
        source=source,
        taken_ids=set(item.id for item in extended),
    )
    checks["analog_axes"] = _check(
        "fail" if analog_missing else "pass",
        analog_detail,
        optional=True,
        declared=[item.to_dict() for item in analog],
    )
    missing.extend(analog_missing)

    source_sha = _source_hash(project_file, manifest_file)
    normalized_payload = {
        "interface_version": INTERFACE_VERSION,
        "levels": [level.scene for level in levels], "endings": dict(endings.scenes),
        "numeric": dict(numeric),
        "device_ids": {key: value.node_path for key, value in device_ids.items()},
        "anchor_camera": anchor.text if anchor else None, "audio_buses": list(audio_buses),
        "level_clear": {"predicate": predicate_text} if level_clear else None,
        "level_entry": dict(level_entry),
    }

    if extended:
        normalized_payload["extended_actions"] = [item.to_dict() for item in extended]
    if analog:
        normalized_payload["analog_axes"] = [item.to_dict() for item in analog]
    report = ConformanceReport(str(root), "PASS" if not missing else "FAIL", checks, tuple(missing))
    interface = SubmissionInterface(
        version=INTERFACE_VERSION, project_root=root, project_file=project_file,
        levels=tuple(levels), endings=endings, numeric=numeric,
        device_ids=MappingProxyType(device_ids), anchor_camera=anchor,
        audio_buses=audio_buses, level_clear=level_clear, level_entry=level_entry,
        groups=groups, actions=actions, extended_actions=extended,
        analog_axes=analog,
        source_sha256=source_sha, normalized_sha256=_normalized_hash(normalized_payload),
        report=report,
    )
    if requirements is not None:
        interface = replace(interface, report=with_requirements(report, interface, requirements))
    return interface
