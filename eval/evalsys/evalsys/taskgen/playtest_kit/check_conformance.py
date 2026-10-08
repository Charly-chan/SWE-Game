#!/usr/bin/env python3


from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, List, Optional


class Ctx:


    def __init__(self, package_root: Path, submission_root: Path) -> None:
        self.package_root = package_root
        self.submission_root = submission_root


        nested = package_root / "visible"
        self.visible = nested if nested.is_dir() else package_root
        self.interface_dir = self.visible / "interface"
        self.warnings: List[str] = []

        self.levels_doc: Optional[dict] = None
        self.manifest_path: Optional[Path] = None
        self.project_dir: Optional[Path] = None
        self.iface: Any = None

    def visible_text(self) -> str:

        parts = []
        seen: set[Path] = set()
        for p in (self.visible / "statement.md", self.visible / "PROMPT.md", self.visible / "GDD.md",
                  self.package_root / "PROMPT.md"):
            if p.is_file() and p.resolve() not in seen:
                seen.add(p.resolve())
                parts.append(p.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(parts)

    def source_files(self) -> List[Path]:
        root = self.project_dir or self.submission_root
        return [p for ext in ("*.gd", "*.tscn", "*.tres", "*.godot")
                for p in root.rglob(ext) if ".godot" not in p.parts]

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"WARN {msg}")


def _repo_evalsys_dir() -> Path | None:

    here = Path(__file__).resolve()
    for parent in here.parents:
        if parent.name == "evalsys" and (parent / "evalsys" / "interface" / "loader.py").is_file():
            return parent
    return None


def _import_loader():
    evalsys_dir = _repo_evalsys_dir()
    if evalsys_dir is not None and str(evalsys_dir) not in sys.path:
        sys.path.insert(0, str(evalsys_dir))
    from evalsys.interface import loader

    return loader


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_fallback(ctx: Ctx, doc: dict) -> List[str]:
    schema_path = ctx.interface_dir / "gb_interface.schema.json"
    if not schema_path.is_file():
        return [f"cannot validate: loader import failed and no schema at {schema_path}"]
    try:
        import jsonschema
    except ImportError:
        return ["cannot validate: neither evalsys.interface.loader nor jsonschema importable"]
    validator = jsonschema.Draft202012Validator(_load_json(schema_path))
    errs = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    return [f"schema: {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errs]


def check_levels_json(ctx: Ctx) -> List[str]:

    in_game = ctx.submission_root / "game" / "gb_levels.json"
    at_root = ctx.submission_root / "gb_levels.json"
    candidates = [p for p in (in_game, at_root) if p.is_file()]
    if not candidates:
        return [f"gb_levels.json not found in {in_game.parent} or {at_root.parent}"]
    if len(candidates) == 2 and in_game.read_bytes() != at_root.read_bytes():
        ctx.warn(f"gb_levels.json differs between {in_game} and {at_root}; using game/")
    manifest = candidates[0]
    ctx.manifest_path = manifest
    ctx.project_dir = manifest.parent

    try:
        doc = _load_json(manifest)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"gb_levels.json unreadable: {exc}"]
    if not isinstance(doc, dict):
        return ["gb_levels.json is not a JSON object"]
    ctx.levels_doc = doc

    try:
        loader = _import_loader()
    except Exception as exc:
        ctx.warn(f"evalsys.interface.loader unavailable ({exc}); falling back to jsonschema")
        return _schema_fallback(ctx, doc)

    iface = loader.load_submission_interface(manifest.parent)
    ctx.iface = iface
    report = iface.report
    failures: List[str] = []
    for name, chk in sorted(report.checks.items()):
        if chk.get("status") == "fail":
            failures.append(f"loader.{name}: {chk.get('detail', '')}")
    ok = report.ok() if callable(report.ok) else bool(report.ok)
    if not failures and not ok:
        failures.append(f"loader verdict {report.verdict}; missing={list(report.missing)}")
    return failures


FORBIDDEN_OP_ACTIONS = ("gb_pause", "gb_reset")


def _declared_extended_ids(ctx: Ctx) -> List[str]:
    doc = ctx.levels_doc or {}
    out = []
    for key in ("extended_actions", "analog_axes"):
        for item in doc.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                out.append(item["id"])
    return out


def check_ops(ctx: Ctx) -> List[str]:

    ops_path = ctx.submission_root / "ops.json"
    table_path = ctx.interface_dir / "op_table.json"
    if not ops_path.is_file():
        return [f"ops.json missing at {ops_path}"]
    if not table_path.is_file():
        return [f"op_table.json missing at {table_path}"]
    try:
        raw = _load_json(ops_path)
        table = _load_json(table_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"unreadable json: {exc}"]

    ops = raw.get("ops") if isinstance(raw, dict) else raw
    if not isinstance(ops, list):
        return ["ops.json: expected a list under `ops` (or a top-level list)"]
    op_names = set((table.get("ops") or {}).keys())
    lo, hi = (table.get("frames_range") or [1, 600])[:2]
    allowed_actions = set(table.get("actions") or []) | set(table.get("extended_actions") or [])
    allowed_actions |= {a.get("id") if isinstance(a, dict) else a for a in table.get("analog_axes") or []}
    allowed_actions |= set(_declared_extended_ids(ctx))

    failures: List[str] = []
    unknown_ops, unknown_actions, forbidden, bad_frames = set(), set(), set(), []
    for i, entry in enumerate(ops):
        if not isinstance(entry, dict):
            failures.append(f"ops[{i}] is not an object")
            continue
        name = entry.get("op")
        if name not in op_names:
            unknown_ops.add(str(name))
        acts = list(entry.get("actions") or [])
        if entry.get("action") is not None:
            acts.append(entry["action"])
        for a in acts:
            if a in FORBIDDEN_OP_ACTIONS:
                forbidden.add(a)
            elif a not in allowed_actions:
                unknown_actions.add(str(a))
        frames = entry.get("frames")
        if frames is not None and not (isinstance(frames, int) and lo <= frames <= hi):
            bad_frames.append(f"ops[{i}].frames={frames!r}")
    if unknown_ops:
        failures.append(f"op names not in op_table: {sorted(unknown_ops)}")
    if forbidden:
        failures.append(f"forbidden actions used: {sorted(forbidden)}")
    if unknown_actions:
        failures.append(f"actions not in op_table or declared extended_actions: {sorted(unknown_actions)}")
    if bad_frames:
        failures.append(f"frames outside [{lo}, {hi}]: {bad_frames[:5]}{' …' if len(bad_frames) > 5 else ''}")
    return failures


def _project_text(ctx: Ctx) -> str:
    root = ctx.project_dir or ctx.submission_root / "game"
    for cand in (root / "project.godot", ctx.submission_root / "project.godot"):
        if cand.is_file():
            return cand.read_text(encoding="utf-8", errors="replace")
    return ""


def _gd_text(ctx: Ctx) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in ctx.source_files() if p.suffix == ".gd"
    )


def _action_bound(project_text: str, name: str) -> bool:

    try:
        loader = _import_loader()
        body = loader._action_body(project_text, name)
    except Exception:


        m = re.search(rf"(?ms)^\[input/{re.escape(name)}\]\s*$.*?(?=^\[|\Z)", project_text)
        body = m.group(0) if m else ""
        if not body:
            m = re.search(rf"(?ms)^[ \t]*{re.escape(name)}[ \t]*=[ \t]*\{{.*?^\}}", project_text)
            body = m.group(0) if m else ""
        if not body:
            m = re.search(rf'(?s)"{re.escape(name)}"\s*[:=]\s*\{{.*?\}}', project_text)
            body = m.group(0) if m else ""
    return bool(re.search(r'["\']?events["\']?\s*[:=]\s*\[(?!\s*\])', body))


def check_extended_actions(ctx: Ctx) -> List[str]:

    if ctx.levels_doc is None:
        return []
    items = ctx.levels_doc.get("extended_actions") or []
    if not isinstance(items, list):
        return ["extended_actions is not a list"]
    project_text = _project_text(ctx)
    gd = _gd_text(ctx)
    failures: List[str] = []
    for i, item in enumerate(items):
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict):
            failures.append(f"extended_actions[{i}] is not an object")
            continue
        ident = item.get("id")
        label = f"extended_actions[{i}]" + (f" ({ident})" if ident else "")
        if not isinstance(ident, str) or not ident:
            failures.append(f"{label}: missing id")
            continue
        why = item.get("why")
        if not isinstance(why, str) or not why.strip():
            failures.append(f"{label}: missing `why`")
        if ident.startswith("gb_"):
            failures.append(f"{label}: uses reserved gb_* prefix")
        if not _action_bound(project_text, ident):
            failures.append(f"{label}: not bound in project.godot [input]")
        if f'"{ident}"' not in gd and f"'{ident}'" not in gd:
            failures.append(f"{label}: never referenced as a string literal in any .gd")
    return failures


def _ending_vocabulary(ctx: Ctx) -> tuple[List[str], List[str]]:

    iface_dir = ctx.interface_dir
    contract = iface_dir / "contract.v2.json"
    if contract.is_file():
        try:
            end = _load_json(contract).get("endings") or {}
            if end.get("success"):
                return list(end["success"]), list(end.get("failure") or [])
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    schema = iface_dir / "gb_interface.schema.json"
    if schema.is_file():
        try:
            node = _load_json(schema)["properties"]["endings"]
            names = list(node["propertyNames"]["enum"])
            success = [r for alt in node.get("anyOf", []) for r in alt.get("required", [])]
            return success, [n for n in names if n not in success]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass
    return [], []


def check_endings(ctx: Ctx) -> List[str]:

    if ctx.levels_doc is None:
        return []
    success, failure = _ending_vocabulary(ctx)
    if not success:
        return ["cannot read ending vocabulary from contract.v2.json or gb_interface.schema.json"]
    endings = ctx.levels_doc.get("endings")
    if not isinstance(endings, dict) or not endings:
        return ["endings missing or empty"]
    canonical = set(success) | set(failure)
    failures: List[str] = []
    bad = sorted(n for n in endings if n not in canonical)
    if bad:
        failures.append(f"non-canonical ending names {bad}; allowed: {sorted(canonical)}")
    if not any(n in success for n in endings):
        failures.append(f"no success ending declared; need one of {success}")
    for name, scene in endings.items():
        if not (isinstance(scene, str) and scene.startswith("res://")):
            failures.append(f"endings.{name}: value is not a res:// scene path ({scene!r})")
    return failures


def _res_to_file(ctx: Ctx, res: str) -> Optional[Path]:
    if not res.startswith("res://"):
        return None
    root = ctx.project_dir or ctx.submission_root / "game"
    return root / res[len("res://"):]


def check_level_paths(ctx: Ctx) -> List[str]:

    if ctx.levels_doc is None:
        return []
    levels = ctx.levels_doc.get("levels")
    if not isinstance(levels, list) or not levels:
        return ["levels missing or empty"]
    failures: List[str] = []
    declared = [(f"levels[{i}]", v) for i, v in enumerate(levels)]
    endings = ctx.levels_doc.get("endings")
    if isinstance(endings, dict):
        declared += [(f"endings.{k}", v) for k, v in endings.items()]
    for label, value in declared:
        if not isinstance(value, str):
            failures.append(f"{label}: not a string ({value!r})")
            continue
        target = _res_to_file(ctx, value)
        if target is None:
            failures.append(f"{label}: not a res:// path ({value})")
        elif not target.is_file():
            failures.append(f"{label}: {value} does not exist ({target})")
    return failures


def _group_vocabulary(ctx: Ctx) -> set[str]:

    contract = ctx.interface_dir / "contract.v2.json"
    if contract.is_file():
        try:
            g = _load_json(contract).get("groups") or {}
            vocab = set(g.get("required") or []) | set(g.get("optional") or [])
            if vocab:
                return vocab
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    try:
        from evalsys.interface import contract as c
        return set(c.GROUPS)
    except Exception:
        return {"gb_player"}


def required_groups(ctx: Ctx) -> List[str]:

    vocab = _group_vocabulary(ctx)
    named = set(re.findall(r"\bgb_[a-z_]+\b", ctx.visible_text()))
    return sorted({"gb_player"} | (named & vocab))


def check_groups(ctx: Ctx) -> List[str]:

    source = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in ctx.source_files() if p.suffix in (".gd", ".tscn")
    )
    missing = [g for g in required_groups(ctx) if not re.search(rf'["\']{re.escape(g)}["\']', source)]
    return [f"group {g} named in visible text but not used in any .gd/.tscn" for g in missing]


_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_MIN_ACTIONS_RE = re.compile(
    r"at\s+least\s+(\d+|" + "|".join(_WORD_NUMBERS) + r")\s+"
    r"(?:new\s+|additional\s+|custom\s+|extra\s+)?extended\s+actions?",
    re.IGNORECASE,
)


def required_extended_action_count(text: str) -> Optional[int]:

    best: Optional[int] = None
    for m in _MIN_ACTIONS_RE.finditer(text):
        tok = m.group(1).lower()
        n = int(tok) if tok.isdigit() else _WORD_NUMBERS[tok]
        best = n if best is None else max(best, n)
    return best


def check_action_count(ctx: Ctx) -> List[str]:

    need = required_extended_action_count(ctx.visible_text())
    if need is None:
        return []
    if ctx.levels_doc is None:
        return []
    items = ctx.levels_doc.get("extended_actions") or []
    have = len(items) if isinstance(items, list) else 0
    if have < need:
        return [f"visible text requires at least {need} extended actions; {have} declared"]
    return []


CHECKS: List[tuple[str, Callable[[Ctx], List[str]]]] = [
    ("levels_json", check_levels_json),
    ("ops", check_ops),
    ("extended_actions", check_extended_actions),
    ("endings", check_endings),
    ("level_paths", check_level_paths),
    ("groups", check_groups),
    ("action_count", check_action_count),
]


def run(package_root: Path, submission_root: Path) -> int:
    ctx = Ctx(package_root, submission_root)
    failures: List[str] = []
    for name, fn in CHECKS:
        try:
            errs = fn(ctx)
        except Exception as exc:
            errs = [f"internal error: {type(exc).__name__}: {exc}"]
        for e in errs:
            print(f"FAIL [{name}] {e}")
        failures.extend(f"[{name}] {e}" for e in errs)
        print(f"CHECK {name} {'ok' if not errs else f'{len(errs)} failure(s)'}")
    ok = not failures
    print(f"CHECK_VERDICT pass={'true' if ok else 'false'} failures={len(failures)}")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("package_root", type=Path)
    ap.add_argument("submission_root", type=Path)
    args = ap.parse_args(argv)
    return run(args.package_root.resolve(), args.submission_root.resolve())


if __name__ == "__main__":
    sys.exit(main())
