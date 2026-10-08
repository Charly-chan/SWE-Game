


from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from ...interface.contract import (
    ANALOG_AXES_MAX,
    EXTENDED_ACTIONS_MAX,
    GROUPS,
    NUMERIC_SLOTS,
)
from ...interface.model import AnalogAxis
from ..modes import UNITY_ACTIONS


MANIFEST_RELATIVE = Path("Assets/GameBenchmark/gb_interface.json")
SCHEMA_VERSION = 1
LEGACY_DECLARATIVE_SCHEMA_VERSION = 2
LATEST_SCHEMA_VERSION = 3
UNITY_EDITOR_VERSION = "6000.3.23f1"
Status = Literal["pass", "fail", "unverified"]

_LEVEL_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_COMPONENT_MEMBER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.+`]*$")
_OBJECT_ROLE = re.compile(r"^gb_[a-z][a-z0-9_]{0,31}$")
_NUMERIC_SLOT = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_MANIFEST_FIELDS = frozenset(
    {"schema_version", "engine", "levels", "actions", "objects", "numeric", "endings"}
)
_MANIFEST_FIELDS_V2 = frozenset({
    "schema_version", "engine", "unity_editor_version", "entry_scene", "levels",
    "supported_actions", "required_actions", "semantic_roles", "numeric_slots",
    "outcome_capabilities", "scaffold_profile_id", "scaffold_digest",
})
_MANIFEST_FIELDS_V3 = _MANIFEST_FIELDS_V2 | frozenset({"analog_axes"})
_OUTCOME_CAPABILITIES = frozenset({"success", "failure", "checkpoint"})
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class UnityInterfaceError(ValueError):
    pass


@dataclass(frozen=True)
class UnityLevel:
    id: str
    scene: str


@dataclass(frozen=True)
class UnityObjectLocator:
    role: str
    level: str
    path: str


@dataclass(frozen=True)
class UnityNumericBinding:
    slot: str
    level: str
    path: str
    component: str
    member: str


@dataclass(frozen=True)
class UnityEnding:
    kind: str
    scene: str


@dataclass(frozen=True)
class UnityInterfaceManifest:


    schema_version: int
    engine: str
    levels: tuple[UnityLevel, ...]
    actions: Mapping[str, str]
    objects: tuple[UnityObjectLocator, ...]
    numeric: tuple[UnityNumericBinding, ...]
    endings: tuple[UnityEnding, ...]
    source: str
    entry_scene: str = ""
    supported_actions: tuple[str, ...] = ()
    required_actions: tuple[str, ...] = ()
    semantic_roles: tuple[str, ...] = ()
    numeric_slots: tuple[str, ...] = ()
    outcome_capabilities: tuple[str, ...] = ()
    scaffold_profile_id: str = ""
    scaffold_digest: str = ""
    extended_actions: tuple[str, ...] = ()
    analog_axes: tuple[AnalogAxis, ...] = ()

    @property
    def declared_roles(self) -> set[str]:
        return (
            set(self.semantic_roles)
            if self.schema_version >= LEGACY_DECLARATIVE_SCHEMA_VERSION
            else {item.role for item in self.objects}
        )

    @property
    def declared_numeric_slots(self) -> set[str]:
        return (
            set(self.numeric_slots)
            if self.schema_version >= LEGACY_DECLARATIVE_SCHEMA_VERSION
            else {item.slot for item in self.numeric}
        )

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "engine": self.engine,
            "levels": [asdict(item) for item in self.levels],
            "actions": dict(self.actions),
            "objects": [asdict(item) for item in self.objects],
            "numeric": [asdict(item) for item in self.numeric],
            "endings": [asdict(item) for item in self.endings],
            "source": self.source,
        }
        if self.schema_version >= LEGACY_DECLARATIVE_SCHEMA_VERSION:
            value.update({
                "entry_scene": self.entry_scene,
                "supported_actions": list(self.supported_actions),
                "required_actions": list(self.required_actions),
                "semantic_roles": list(self.semantic_roles),
                "numeric_slots": list(self.numeric_slots),
                "outcome_capabilities": list(self.outcome_capabilities),
                "scaffold_profile_id": self.scaffold_profile_id,
                "scaffold_digest": self.scaffold_digest,
            })
            if self.schema_version >= LATEST_SCHEMA_VERSION:
                value["analog_axes"] = [item.to_dict() for item in self.analog_axes]
        return value


@dataclass(frozen=True)
class UnityDiagnostic:
    id: str
    status: Status
    detail: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "detail": self.detail,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class UnityInterfaceReport:


    project: str
    manifest_path: str
    diagnostics: tuple[UnityDiagnostic, ...]
    manifest: UnityInterfaceManifest | None = None

    @property
    def static_diagnostics(self) -> tuple[UnityDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if not item.id.startswith("runtime/"))

    @property
    def static_status(self) -> Literal["pass", "fail"]:
        return "fail" if any(item.status == "fail" for item in self.static_diagnostics) else "pass"

    @property
    def status(self) -> Status:
        if self.static_status == "fail":
            return "fail"
        if any(item.status == "unverified" for item in self.diagnostics):
            return "unverified"
        return "pass"

    @property
    def ready_for_runtime(self) -> bool:
        return self.static_status == "pass" and self.manifest is not None

    def by_id(self, diagnostic_id: str) -> UnityDiagnostic:
        for item in self.diagnostics:
            if item.id == diagnostic_id:
                return item
        raise KeyError(diagnostic_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "gamebench.mode5.unity_interface.v1",
            "project": self.project,
            "manifest_path": self.manifest_path,
            "status": self.status,
            "static_status": self.static_status,
            "ready_for_runtime": self.ready_for_runtime,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "manifest": self.manifest.to_dict() if self.manifest else None,
        }


def _diag(
    diagnostic_id: str,
    status: Status,
    detail: str,
    **evidence: Any,
) -> UnityDiagnostic:
    return UnityDiagnostic(diagnostic_id, status, detail, evidence)


def _looks_like_unity_root(path: Path) -> bool:
    return (
        (path / "Assets").is_dir()
        and (path / "Packages").is_dir()
        and (path / "ProjectSettings" / "ProjectVersion.txt").is_file()
    )


def find_unity_project(path: str | Path) -> Path:

    candidate = Path(path).resolve()
    if _looks_like_unity_root(candidate):
        return candidate
    if candidate.is_dir():
        nested = sorted(
            child for child in candidate.iterdir()
            if child.is_dir() and _looks_like_unity_root(child)
        )
        if len(nested) == 1:
            return nested[0]

    return candidate


def _asset_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().replace("\\", "/")
    parts = text.split("/")
    if not text.startswith("Assets/") or ".." in parts or text.startswith("/"):
        return None
    return text


def _scene_path(value: Any) -> str | None:
    text = _asset_path(value)
    return text if text and text.lower().endswith(".unity") else None


def _object_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().replace("\\", "/").strip("/")
    if not text or ".." in text.split("/"):
        return None
    return text


def _existing_scene(project: Path, scene: str) -> bool:
    return (project / Path(scene)).is_file()


def _runtime_unverified() -> list[UnityDiagnostic]:
    reason = "static MVP did not invoke Unity"
    return [
        _diag("runtime/build", "unverified", reason),
        _diag("runtime/observation", "unverified", reason),
        _diag("runtime/route_replay", "unverified", reason),
        _diag("runtime/evaluator_capture", "unverified", reason),
        _diag("runtime/vlm", "unverified", "no evaluator-owned candidate video was captured"),
    ]


def _read_raw(path: Path) -> tuple[dict[str, Any] | None, UnityDiagnostic]:
    if not path.is_file():
        return None, _diag(
            "static/manifest",
            "fail",
            f"missing {MANIFEST_RELATIVE.as_posix()}",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, _diag("static/manifest", "fail", f"manifest is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        return None, _diag("static/manifest", "fail", "manifest root must be a JSON object")
    return payload, _diag("static/manifest", "pass", "Unity GB manifest parsed")


def _validate_levels(
    project: Path, raw: Any
) -> tuple[list[UnityLevel], UnityDiagnostic]:
    if not isinstance(raw, list) or not raw:
        return [], _diag("static/levels", "fail", "levels must be a non-empty array")
    levels: list[UnityLevel] = []
    problems: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            problems.append(f"levels[{index}] must be an object")
            continue
        level_id = item.get("id")
        scene = _scene_path(item.get("scene"))
        if not isinstance(level_id, str) or not _LEVEL_ID.fullmatch(level_id):
            problems.append(f"levels[{index}].id is invalid")
            continue
        if level_id in seen:
            problems.append(f"duplicate level id {level_id!r}")
            continue
        if scene is None:
            problems.append(f"levels[{index}].scene must be Assets/**/*.unity")
            continue
        if not _existing_scene(project, scene):
            problems.append(f"scene does not exist: {scene}")
            continue
        seen.add(level_id)
        levels.append(UnityLevel(level_id, scene))
    if problems:
        return levels, _diag("static/levels", "fail", "; ".join(problems), problems=problems)
    return levels, _diag(
        "static/levels", "pass", f"{len(levels)} addressable Unity scene(s)",
        levels=[item.id for item in levels],
    )


def _validate_actions(raw: Any) -> tuple[dict[str, str], UnityDiagnostic]:
    if not isinstance(raw, dict):
        return {}, _diag("static/actions", "fail", "actions must be an object")
    mappings: dict[str, str] = {}
    problems: list[str] = []
    for action in UNITY_ACTIONS:
        value = raw.get(action)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"missing non-empty mapping for {action}")
        else:
            mappings[action] = value.strip()
    extras = sorted(str(key) for key in raw if key not in UNITY_ACTIONS)
    if extras:
        problems.append(
            "schema v1 does not support extra actions: " + ", ".join(extras)
        )
    if problems:
        return mappings, _diag(
            "static/actions", "fail", "; ".join(problems),
            missing=[action for action in UNITY_ACTIONS if action not in mappings],
            extras=extras,
        )
    return mappings, _diag(
        "static/actions",
        "pass",
        f"all {len(UNITY_ACTIONS)} canonical Unity actions are mapped",
        extras=extras,
    )


def _validate_objects(
    raw: Any, level_ids: set[str]
) -> tuple[list[UnityObjectLocator], UnityDiagnostic]:
    if not isinstance(raw, dict):
        return [], _diag("static/objects", "fail", "objects must be an object keyed by GB role")
    locators: list[UnityObjectLocator] = []
    problems: list[str] = []
    for role, entries in raw.items():
        if not _OBJECT_ROLE.fullmatch(str(role)):
            problems.append(
                f"invalid object role {role!r}; expected gb_[a-z][a-z0-9_]*"
            )
            continue
        if not isinstance(entries, list) or not entries:
            problems.append(f"{role} must contain at least one locator")
            continue
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                problems.append(f"{role}[{index}] must be an object")
                continue
            level = item.get("level")
            path = _object_path(item.get("path"))
            if not isinstance(level, str) or level not in level_ids:
                problems.append(f"{role}[{index}].level does not name a declared level")
                continue
            if path is None:
                problems.append(f"{role}[{index}].path is invalid")
                continue
            locators.append(UnityObjectLocator(str(role), level, path))
    player_levels = {item.level for item in locators if item.role == "gb_player"}
    missing_player = sorted(level_ids - player_levels)
    if missing_player:
        problems.append("gb_player has no locator for levels: " + ", ".join(missing_player))
    if problems:
        return locators, _diag("static/objects", "fail", "; ".join(problems), problems=problems)
    return locators, _diag(
        "static/objects", "pass", f"{len(locators)} object locator(s) across {len(level_ids)} level(s)",
        roles=sorted({item.role for item in locators}),
        extension_roles=sorted({item.role for item in locators} - set(GROUPS)),
    )


def _validate_numeric(
    raw: Any, level_ids: set[str]
) -> tuple[list[UnityNumericBinding], UnityDiagnostic]:
    if raw in (None, {}):
        return [], _diag(
            "static/numeric", "pass",
            "no optional numeric slots declared; task-specific requirements may require them later",
        )
    if not isinstance(raw, dict):
        return [], _diag("static/numeric", "fail", "numeric must be an object")
    bindings: list[UnityNumericBinding] = []
    problems: list[str] = []
    for slot, item in raw.items():
        if not _NUMERIC_SLOT.fullmatch(str(slot)):
            problems.append(
                f"invalid numeric slot {slot!r}; expected [a-z][a-z0-9_]*"
            )
            continue
        if not isinstance(item, dict):
            problems.append(f"numeric.{slot} must be an object")
            continue
        level = item.get("level")
        path = _object_path(item.get("path"))
        component = item.get("component")
        member = item.get("member")
        if not isinstance(level, str) or level not in level_ids:
            problems.append(f"numeric.{slot}.level does not name a declared level")
            continue
        if path is None:
            problems.append(f"numeric.{slot}.path is invalid")
            continue
        if not isinstance(component, str) or not _COMPONENT_MEMBER.fullmatch(component.strip()):
            problems.append(f"numeric.{slot}.component is invalid")
            continue
        if not isinstance(member, str) or not _COMPONENT_MEMBER.fullmatch(member.strip()):
            problems.append(f"numeric.{slot}.member is invalid")
            continue
        bindings.append(
            UnityNumericBinding(slot, level, path, component.strip(), member.strip())
        )
    if problems:
        return bindings, _diag("static/numeric", "fail", "; ".join(problems), problems=problems)
    return bindings, _diag(
        "static/numeric", "pass", f"{len(bindings)} component/member binding(s)",
        slots=[item.slot for item in bindings],
        extension_slots=sorted({item.slot for item in bindings} - set(NUMERIC_SLOTS)),
    )


def _validate_endings(
    project: Path, raw: Any
) -> tuple[list[UnityEnding], UnityDiagnostic]:
    if not isinstance(raw, dict):
        return [], _diag("static/endings", "fail", "endings must be an object")
    endings: list[UnityEnding] = []
    problems: list[str] = []
    for kind in ("success", "failure"):
        item = raw.get(kind)
        if not isinstance(item, dict):
            problems.append(f"endings.{kind} must be an object with a scene")
            continue
        scene = _scene_path(item.get("scene"))
        if scene is None:
            problems.append(f"endings.{kind}.scene must be Assets/**/*.unity")
            continue
        if not _existing_scene(project, scene):
            problems.append(f"ending scene does not exist: {scene}")
            continue
        endings.append(UnityEnding(kind, scene))
    by_kind = {item.kind: item.scene for item in endings}
    if by_kind.get("success") and by_kind.get("success") == by_kind.get("failure"):
        problems.append("success and failure endings must name distinct scenes")
    if problems:
        return endings, _diag("static/endings", "fail", "; ".join(problems), problems=problems)
    return endings, _diag(
        "static/endings", "pass", "distinct success and failure scenes exist",
        success=by_kind["success"], failure=by_kind["failure"],
    )


def _validate_identifier_list(
    raw: Any,
    *,
    field: str,
    pattern: re.Pattern[str],
    allow_empty: bool,
) -> tuple[tuple[str, ...], list[str]]:
    if not isinstance(raw, list) or (not allow_empty and not raw):
        return (), [f"{field} must be {'an' if allow_empty else 'a non-empty'} array"]
    values: list[str] = []
    problems: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, str) or not pattern.fullmatch(value):
            problems.append(f"{field}[{index}] is invalid")
        elif value in values:
            problems.append(f"{field} contains duplicate {value!r}")
        else:
            values.append(value)
    return tuple(values), problems


def _validate_v2(
    project: Path,
    raw: dict[str, Any],
    manifest_file: Path,
) -> tuple[list[UnityDiagnostic], UnityInterfaceManifest | None]:
    diagnostics: list[UnityDiagnostic] = []
    schema_version = int(raw.get("schema_version") or 0)
    fields = _MANIFEST_FIELDS_V3 if schema_version >= LATEST_SCHEMA_VERSION else _MANIFEST_FIELDS_V2
    ignored = sorted(str(key) for key in raw if key not in fields)
    engine = raw.get("engine")
    if not isinstance(engine, str) or engine.strip().lower() != "unity":
        diagnostics.append(_diag("static/schema", "fail", "engine must be 'unity'"))
    elif ignored:
        diagnostics.append(_diag("static/schema", "fail", "schema v2 rejects unknown top-level fields", extras=ignored))
    else:
        diagnostics.append(_diag(
            "static/schema", "pass", f"Unity Mode-5 schema v{schema_version}", ignored_fields=[]
        ))

    declared_editor = raw.get("unity_editor_version")
    if declared_editor != UNITY_EDITOR_VERSION:
        diagnostics.append(_diag(
            "static/editor_version", "fail",
            f"unity_editor_version must be {UNITY_EDITOR_VERSION}",
            observed=declared_editor,
        ))
    else:
        diagnostics.append(_diag("static/editor_version", "pass", f"frozen editor {UNITY_EDITOR_VERSION}"))

    levels, level_diag = _validate_levels(project, raw.get("levels"))
    diagnostics.append(level_diag)
    entry_scene = _scene_path(raw.get("entry_scene"))
    if entry_scene is None or not _existing_scene(project, entry_scene):
        diagnostics.append(_diag("static/entry_scene", "fail", "entry_scene must name an existing Assets/**/*.unity scene"))
    else:
        diagnostics.append(_diag(
            "static/entry_scene",
            "pass",
            "entry scene exists; bootstrap scenes may be separate from declared gameplay levels",
            scene=entry_scene,
            is_declared_level=entry_scene in {item.scene for item in levels},
        ))

    action_pattern = (
        re.compile(r"^[a-z][a-z0-9_]{1,31}$")
        if schema_version >= LATEST_SCHEMA_VERSION
        else re.compile(r"^gb_[a-z][a-z0-9_]*$")
    )
    supported, supported_problems = _validate_identifier_list(
        raw.get("supported_actions"), field="supported_actions",
        pattern=action_pattern, allow_empty=False,
    )
    required, required_problems = _validate_identifier_list(
        raw.get("required_actions"), field="required_actions",
        pattern=action_pattern, allow_empty=True,
    )
    action_problems = [*supported_problems, *required_problems]
    unsupported = sorted(set(supported) - set(UNITY_ACTIONS)) if schema_version < 3 else []
    invalid_extensions = sorted(
        action for action in set(supported) - set(UNITY_ACTIONS)
        if action.startswith("gb_") or not re.fullmatch(r"^[a-z][a-z0-9_]{1,31}$", action)
    )
    extensions = tuple(action for action in supported if action not in UNITY_ACTIONS)
    missing_required = sorted(set(required) - set(supported))
    if unsupported:
        action_problems.append("unsupported canonical actions: " + ", ".join(unsupported))
    if invalid_extensions:
        action_problems.append("invalid extended actions: " + ", ".join(invalid_extensions))
    if len(extensions) > EXTENDED_ACTIONS_MAX:
        action_problems.append(
            f"extended actions exceed the maximum of {EXTENDED_ACTIONS_MAX}"
        )
    if missing_required:
        action_problems.append("required_actions not in supported_actions: " + ", ".join(missing_required))
    actions = {action: f"Player/{action}" for action in supported}
    diagnostics.append(_diag(
        "static/actions", "fail" if action_problems else "pass",
        "; ".join(action_problems) if action_problems else f"{len(actions)} action channel(s) declared",
        supported=list(supported), required=list(required),
        extensions=sorted(set(supported) - set(UNITY_ACTIONS)), problems=action_problems,
    ))

    analog_axes: list[AnalogAxis] = []
    axis_problems: list[str] = []
    raw_axes = raw.get("analog_axes", []) if schema_version >= 3 else []
    if not isinstance(raw_axes, list):
        axis_problems.append("analog_axes must be an array")
    else:
        seen_axes: set[str] = set()
        for index, raw_axis in enumerate(raw_axes):
            axis = AnalogAxis.from_dict(raw_axis)
            if axis is None:
                axis_problems.append(f"analog_axes[{index}] is invalid")
                continue
            if not re.fullmatch(r"^[a-z][a-z0-9_]{1,31}$", axis.id):
                axis_problems.append(f"analog_axes[{index}].id is invalid")
            elif axis.id in seen_axes or axis.id in supported:
                axis_problems.append(f"analog axis {axis.id!r} is duplicate or collides with an action")
            elif (
                not axis.why
                or not math.isfinite(axis.min)
                or not math.isfinite(axis.max)
                or axis.min >= axis.max
                or not 2 <= axis.steps <= 33
            ):
                axis_problems.append(f"analog_axes[{index}] has an invalid reason, range, or steps")
            else:
                seen_axes.add(axis.id)
                analog_axes.append(axis)
    if len(analog_axes) > ANALOG_AXES_MAX:
        axis_problems.append(f"analog_axes exceeds the maximum of {ANALOG_AXES_MAX}")
    diagnostics.append(_diag(
        "static/analog_axes", "fail" if axis_problems else "pass",
        "; ".join(axis_problems) if axis_problems else f"{len(analog_axes)} analog axis channel(s) declared",
        axes=[item.to_dict() for item in analog_axes], problems=axis_problems,
    ))

    roles, role_problems = _validate_identifier_list(
        raw.get("semantic_roles"), field="semantic_roles", pattern=_OBJECT_ROLE, allow_empty=False,
    )
    if "gb_player" not in roles:
        role_problems.append("semantic_roles must include gb_player")
    diagnostics.append(_diag(
        "static/objects", "fail" if role_problems else "pass",
        "; ".join(role_problems) if role_problems else f"{len(roles)} marker-discovered semantic role(s)",
        roles=list(roles), extension_roles=sorted(set(roles) - set(GROUPS)), problems=role_problems,
    ))

    numeric_slots, numeric_problems = _validate_identifier_list(
        raw.get("numeric_slots"), field="numeric_slots", pattern=_NUMERIC_SLOT, allow_empty=True,
    )
    diagnostics.append(_diag(
        "static/numeric", "fail" if numeric_problems else "pass",
        "; ".join(numeric_problems) if numeric_problems else f"{len(numeric_slots)} telemetry slot(s)",
        slots=list(numeric_slots), extension_slots=sorted(set(numeric_slots) - set(NUMERIC_SLOTS)),
        problems=numeric_problems,
    ))

    capabilities, capability_problems = _validate_identifier_list(
        raw.get("outcome_capabilities"), field="outcome_capabilities",
        pattern=re.compile(r"^[a-z][a-z0-9_]*$"), allow_empty=False,
    )
    extra_capabilities = sorted(set(capabilities) - _OUTCOME_CAPABILITIES)
    if extra_capabilities:
        capability_problems.append("unsupported outcome capabilities: " + ", ".join(extra_capabilities))
    if "success" not in capabilities:
        capability_problems.append("outcome_capabilities must include success")
    diagnostics.append(_diag(
        "static/endings", "fail" if capability_problems else "pass",
        "; ".join(capability_problems) if capability_problems else "event-based outcome capabilities declared",
        capabilities=list(capabilities), problems=capability_problems,
    ))

    profile_id = raw.get("scaffold_profile_id")
    digest = raw.get("scaffold_digest")
    scaffold_problems: list[str] = []
    if not isinstance(profile_id, str) or not profile_id.strip():
        scaffold_problems.append("scaffold_profile_id is required")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        scaffold_problems.append("scaffold_digest must be sha256:<64 lowercase hex>")
    diagnostics.append(_diag(
        "static/scaffold", "fail" if scaffold_problems else "pass",
        "; ".join(scaffold_problems) if scaffold_problems else "scaffold profile and digest declared",
        profile_id=profile_id, digest=digest,
    ))

    if any(item.status == "fail" for item in diagnostics):
        return diagnostics, None
    return diagnostics, UnityInterfaceManifest(
        schema_version=schema_version,
        engine="unity",
        levels=tuple(levels),
        actions=actions,
        objects=(),
        numeric=(),
        endings=(),
        source=str(manifest_file),
        entry_scene=entry_scene or "",
        supported_actions=supported,
        required_actions=required,
        semantic_roles=roles,
        numeric_slots=numeric_slots,
        outcome_capabilities=capabilities,
        scaffold_profile_id=str(profile_id),
        scaffold_digest=str(digest),
        extended_actions=extensions,
        analog_axes=tuple(analog_axes),
    )


def validate_unity_interface(
    project: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> UnityInterfaceReport:

    root = find_unity_project(project)
    manifest_file = (
        Path(manifest_path).resolve()
        if manifest_path is not None
        else root / MANIFEST_RELATIVE
    )
    diagnostics: list[UnityDiagnostic] = []

    required = {
        "Assets": (root / "Assets", "directory"),
        "Packages": (root / "Packages", "directory"),
        "ProjectVersion.txt": (
            root / "ProjectSettings" / "ProjectVersion.txt",
            "file",
        ),
    }
    missing = [
        name
        for name, (path, kind) in required.items()
        if not (path.is_dir() if kind == "directory" else path.is_file())
    ]
    version_path = required["ProjectVersion.txt"][0]
    version = ""
    if version_path.is_file():
        for line in version_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("m_EditorVersion:"):
                version = line.partition(":")[2].strip()
                break
    if missing:
        diagnostics.append(
            _diag("static/project_layout", "fail", "missing Unity project items: " + ", ".join(missing))
        )
    elif not version:
        diagnostics.append(
            _diag("static/project_layout", "fail", "ProjectVersion.txt has no m_EditorVersion")
        )
    elif version != UNITY_EDITOR_VERSION:
        diagnostics.append(
            _diag(
                "static/project_layout",
                "fail",
                f"Unity editor must be {UNITY_EDITOR_VERSION}, got {version}",
                unity_version=version,
                required_unity_version=UNITY_EDITOR_VERSION,
            )
        )
    else:
        diagnostics.append(
            _diag("static/project_layout", "pass", f"Unity project layout present ({version})", unity_version=version)
        )

    raw, manifest_diag = _read_raw(manifest_file)
    diagnostics.append(manifest_diag)
    if raw is None:
        diagnostics.extend(_runtime_unverified())
        return UnityInterfaceReport(str(root), str(manifest_file), tuple(diagnostics))

    schema_version = raw.get("schema_version")
    engine = raw.get("engine")
    if schema_version in {LEGACY_DECLARATIVE_SCHEMA_VERSION, LATEST_SCHEMA_VERSION}:
        v2_diagnostics, manifest = _validate_v2(root, raw, manifest_file)
        diagnostics.extend(v2_diagnostics)
        if any(item.status == "fail" and item.id.startswith("static/") for item in diagnostics):
            manifest = None
        diagnostics.extend(_runtime_unverified())
        return UnityInterfaceReport(
            str(root), str(manifest_file), tuple(diagnostics), manifest=manifest
        )
    ignored_fields = sorted(str(key) for key in raw if key not in _MANIFEST_FIELDS)
    if schema_version != SCHEMA_VERSION:
        diagnostics.append(
            _diag(
                "static/schema", "fail",
                f"schema_version must be {SCHEMA_VERSION}, got {schema_version!r}",
            )
        )
    elif not isinstance(engine, str) or engine.strip().lower() != "unity":
        diagnostics.append(_diag("static/schema", "fail", "engine must be 'unity'"))
    else:
        detail = "Unity Mode-5 schema v1"
        if ignored_fields:
            detail += "; unknown top-level fields are ignored and non-scoring"
        diagnostics.append(
            _diag(
                "static/schema",
                "pass",
                detail,
                ignored_fields=ignored_fields,
            )
        )

    levels, level_diag = _validate_levels(root, raw.get("levels"))
    diagnostics.append(level_diag)
    level_ids = {item.id for item in levels}
    actions, action_diag = _validate_actions(raw.get("actions"))
    diagnostics.append(action_diag)
    objects, object_diag = _validate_objects(raw.get("objects"), level_ids)
    diagnostics.append(object_diag)
    numeric, numeric_diag = _validate_numeric(raw.get("numeric"), level_ids)
    diagnostics.append(numeric_diag)
    endings, ending_diag = _validate_endings(root, raw.get("endings"))
    diagnostics.append(ending_diag)

    static_failed = any(
        item.status == "fail" and item.id.startswith("static/") for item in diagnostics
    )
    manifest = None
    if not static_failed:
        manifest = UnityInterfaceManifest(
            schema_version=SCHEMA_VERSION,
            engine="unity",
            levels=tuple(levels),
            actions=actions,
            objects=tuple(objects),
            numeric=tuple(numeric),
            endings=tuple(endings),
            source=str(manifest_file),
        )
    diagnostics.extend(_runtime_unverified())
    return UnityInterfaceReport(
        str(root), str(manifest_file), tuple(diagnostics), manifest=manifest
    )


def load_unity_interface(
    project: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> UnityInterfaceManifest:

    report = validate_unity_interface(project, manifest_path=manifest_path)
    if not report.ready_for_runtime or report.manifest is None:
        details = "; ".join(
            item.detail for item in report.static_diagnostics if item.status == "fail"
        )
        raise UnityInterfaceError(details or "Unity interface is not statically valid")
    return report.manifest


__all__ = [
    "MANIFEST_RELATIVE",
    "LATEST_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "UNITY_EDITOR_VERSION",
    "UNITY_ACTIONS",
    "UnityDiagnostic",
    "UnityEnding",
    "UnityInterfaceError",
    "UnityInterfaceManifest",
    "UnityInterfaceReport",
    "UnityLevel",
    "UnityNumericBinding",
    "UnityObjectLocator",
    "find_unity_project",
    "load_unity_interface",
    "validate_unity_interface",
]
