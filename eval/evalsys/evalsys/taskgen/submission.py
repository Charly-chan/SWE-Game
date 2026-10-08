

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Sequence

from ..interface.loader import project_root
from ..routes.agent import Op, validate_ops
from .modes import UNITY_ACTIONS
from .unity.unity_interface import find_unity_project


_UNITY_VALIDATION_ALIASES = {
    "gb_attack": "unity_attack",
    "gb_dash": "unity_dash",
}
_UNITY_VALIDATION_REVERSE = {
    value: key for key, value in _UNITY_VALIDATION_ALIASES.items()
}

EVAL_SMUGGLE_NAMES = (
    "certificate.rt1.json",
    "expectations.json",
    "route.json",
    "registry.json",
)
SUBMISSION_DELIVERABLE_NAMES = ("GDD.md", "ops.json", "demos.json", "BUILD.md")


@dataclass
class Submission:
    root: Path
    project: Path
    engine: str = "godot"
    gdd_path: Path | None = None
    ops_path: Path | None = None
    build_path: Path | None = None
    ops: list[Op] = field(default_factory=list)
    ops_error: str = ""
    smuggled: tuple[str, ...] = ()
    demos_path: Path | None = None
    demos: list[dict[str, Any]] = field(default_factory=list)
    demos_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "project": str(self.project),
            "engine": self.engine,
            "gdd": str(self.gdd_path) if self.gdd_path else None,
            "ops_path": str(self.ops_path) if self.ops_path else None,
            "build_path": str(self.build_path) if self.build_path else None,
            "ops": [op.to_dict() for op in self.ops],
            "ops_error": self.ops_error,
            "smuggled": list(self.smuggled),
            "demos_path": str(self.demos_path) if self.demos_path else None,
            "demos": [{**d, "ops": [op.to_dict() for op in d["ops"]]} for d in self.demos],
            "demos_error": self.demos_error,
        }


def load_submission(path: str | Path) -> Submission:
    root = Path(path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"submission path does not exist: {root}")
    project = project_root(root)
    if not (project / "project.godot").is_file():
        raise FileNotFoundError(
            f"no project.godot under {root}; expected it at the root or in a "
            "single nested engine directory"
        )
    gdd_name, ops_name, demos_name, _ = SUBMISSION_DELIVERABLE_NAMES
    gdd = _first_file(root / gdd_name, project / gdd_name)
    ops_path = _first_file(root / ops_name, project / ops_name)
    extras: tuple[str, ...] = ()
    axes: tuple[Any, ...] = ()
    try:
        from ..interface import load_submission_interface

        interface = load_submission_interface(project)
        extras = tuple(interface.extended_action_ids)
        axes = tuple(interface.analog_axes)
    except (OSError, ValueError):
        extras = ()
    ops: list[Op] = []
    ops_error = ""
    if ops_path is not None:
        try:
            ops = parse_ops(ops_path, extra_actions=extras, extra_axes=axes)
        except (OSError, ValueError) as exc:
            ops_error = str(exc)
    smuggled = tuple(_find_smuggled(root))
    demos_path = _first_file(root / demos_name, project / demos_name)
    demos: list[dict[str, Any]] = []
    demos_error = ""
    if demos_path is not None:
        try:
            demos = parse_demos(demos_path, extra_actions=extras, extra_axes=axes)
        except (OSError, ValueError) as exc:
            demos_error = str(exc)
    return Submission(
        root=root,
        project=project,
        engine="godot",
        gdd_path=gdd,
        ops_path=ops_path,
        ops=ops,
        ops_error=ops_error,
        smuggled=smuggled,
        demos_path=demos_path,
        demos=demos,
        demos_error=demos_error,
    )


def parse_demos(path: Path, extra_actions: Sequence[str] = (), extra_axes: Sequence[Any] = ()) -> list[dict[str, Any]]:

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("demos.json requires schema_version: 1")
    rows = raw.get("demos")
    if not isinstance(rows, list) or not rows:
        raise ValueError("demos.json requires a non-empty demos list")
    out: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"demo {index}: expected an object")
        name = row.get("id")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError(f"demo {index}: id must be non-empty and unique")

        unknown = set(row) - {"id", "description", "ops"}
        if unknown:
            raise ValueError(f"demo {name}: unsupported fields {sorted(unknown)}")
        ops = row.get("ops")
        if not isinstance(ops, list) or not ops:
            raise ValueError(f"demo {name}: ops must be a non-empty list")
        validated = validate_ops(ops, extra_actions=extra_actions, extra_axes=extra_axes)
        if validated.refusals:
            raise ValueError(f"demo {name}: " + "; ".join(r.detail for r in validated.refusals))
        if not ops_have_player_action(list(validated.accepted)):
            raise ValueError(f"demo {name}: demonstrate player input, not only wait/noop")
        names.add(name)
        out.append({"id": name, "description": str(row.get("description") or ""),
                    "ops": list(validated.accepted)})
    return out


def load_unity_submission(path: str | Path) -> Submission:

    root = Path(path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"submission path does not exist: {root}")
    project = find_unity_project(root)
    required_layout = (
        (project / "Assets").is_dir()
        and (project / "Packages").is_dir()
        and (project / "ProjectSettings" / "ProjectVersion.txt").is_file()
    )
    if not required_layout:
        raise FileNotFoundError(
            f"no unambiguous Unity project under {root}; expected Assets/, "
            "Packages/, and ProjectSettings/ProjectVersion.txt"
        )
    _, ops_name, _, build_name = SUBMISSION_DELIVERABLE_NAMES
    ops_path = _first_file(root / ops_name, project / ops_name)
    build_path = _first_file(root / build_name, project / build_name)
    ops: list[Op] = []
    ops_error = ""
    extra_actions: tuple[str, ...] = ()
    extra_axes: tuple[Any, ...] = ()
    try:
        from .unity.unity_interface import validate_unity_interface
        report = validate_unity_interface(project)
        if report.manifest is not None:
            extra_actions = report.manifest.extended_actions
            extra_axes = report.manifest.analog_axes
    except (OSError, ValueError):


        pass
    if ops_path is not None:
        try:
            ops = parse_unity_ops(
                ops_path, extra_actions=extra_actions, extra_axes=extra_axes
            )
        except (OSError, ValueError) as exc:
            ops_error = str(exc)
    return Submission(
        root=root,
        project=project,
        engine="unity",
        ops_path=ops_path,
        build_path=build_path,
        ops=ops,
        ops_error=ops_error,
        smuggled=tuple(_find_smuggled(root)),
    )


def parse_ops(path: Path, extra_actions: Sequence[str] = (), extra_axes: Sequence[Any] = ()) -> list[Op]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("ops", raw.get("operations"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a list of ops or an object with an `ops` list")
    validated = validate_ops(raw, extra_actions=extra_actions, extra_axes=extra_axes)
    if validated.refusals:
        reasons = "; ".join(
            f"[{r.index}] {r.reason}: {r.detail}" for r in validated.refusals
        )
        raise ValueError(f"ops refused: {reasons}")
    return list(validated.accepted)


def parse_unity_ops(
    path: Path,
    *,
    extra_actions: Sequence[str] = (),
    extra_axes: Sequence[Any] = (),
) -> list[Op]:

    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("ops", raw.get("operations"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a list of ops or an object with an `ops` list")

    translated: list[Any] = []
    for item in raw:
        if not isinstance(item, dict):
            translated.append(item)
            continue
        row = dict(item)
        supplied: list[str] = []
        if isinstance(row.get("action"), str) and row["action"]:
            supplied.append(row["action"])
        if isinstance(row.get("actions"), list):
            supplied.extend(
                action for action in row["actions"]
                if isinstance(action, str) and action
            )
        unknown = [
            action for action in supplied
            if action not in UNITY_ACTIONS
            and action not in extra_actions
            and action not in {"gb_pause", "gb_reset"}
        ]
        if unknown:
            raise ValueError(
                "ops refused: unknown_action: "
                f"{unknown!r} is outside the frozen Unity action space "
                f"{list(UNITY_ACTIONS) + list(extra_actions)}"
            )
        if isinstance(row.get("action"), str):
            row["action"] = _UNITY_VALIDATION_ALIASES.get(row["action"], row["action"])
        if isinstance(row.get("actions"), list):
            row["actions"] = [
                _UNITY_VALIDATION_ALIASES.get(action, action)
                if isinstance(action, str) else action
                for action in row["actions"]
            ]
        translated.append(row)
    validated = validate_ops(
        translated,
        extra_actions=tuple(_UNITY_VALIDATION_REVERSE) + tuple(extra_actions),
        extra_axes=extra_axes,
    )
    if validated.refusals:
        reasons = "; ".join(
            f"[{item.index}] {item.reason}: {item.detail}"
            for item in validated.refusals
        )
        raise ValueError(f"ops refused: {reasons}")

    restored: list[Op] = []
    for op in validated.accepted:
        restored.append(
            replace(
                op,
                action=_UNITY_VALIDATION_REVERSE.get(op.action, op.action),
                actions=tuple(
                    _UNITY_VALIDATION_REVERSE.get(action, action)
                    for action in op.actions
                ),
            )
        )
    return restored


def ops_have_player_action(ops: list[Op]) -> bool:


    active = {"tap", "hold", "state"}
    for op in ops:
        if op.op not in active:
            continue
        if op.action or op.actions or op.axes:
            return True
    return False


def _first_file(*paths: Path) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def _find_smuggled(root: Path) -> list[str]:
    hits: list[str] = []
    for name in EVAL_SMUGGLE_NAMES:
        for path in root.rglob(name):
            rel = str(path.relative_to(root)).replace("\\", "/")
            hits.append(rel)
    return hits
