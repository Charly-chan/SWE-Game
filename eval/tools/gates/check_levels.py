#!/usr/bin/env python3


from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_EVALSYS_ROOT = str(Path(__file__).resolve().parents[2] / "evalsys")
if _EVALSYS_ROOT not in sys.path:
    sys.path.insert(0, _EVALSYS_ROOT)
from evalsys.engine import hostenv
from evalsys.interface.loader import project_root


GODOT = hostenv.find_godot()

FRAMES = 45
TIMEOUT = 300


EXCLUDE_DIRS = frozenset({
    ".godot", ".git", "__pycache__", "inputs", "reference", "staging",
    "_archives", "web", "builds", "export",
})
EVIDENCE_DIRS = frozenset({"compare", "verify", "recording", "shots", "screenshots"})
HEAVY_EXT = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".avi", ".mp4", ".mov", ".gif",
})


FATAL_PREFIXES = (
    "SCRIPT ERROR",
    "ERROR: Failed loading resource",
    "ERROR: Cannot instantiate",
    "ERROR: Cannot open file",
    "ERROR: Error importing",
    "ERROR: glTF",
)


def scratch_root() -> Path:


    override = os.environ.get("GB_LEVELS_SCRATCH")
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(os.environ.get("SystemDrive", "C:") + os.sep) / "gb_levels_scratch"
    if Path("/data2").is_dir():
        return Path("/tmp/swe-game/gb_levels_scratch")
    return Path(tempfile.gettempdir()) / "gb_levels_scratch"


def _copy_ignore(source: Path):
    source_real = source.resolve()

    def ignore(dirpath: str, names: list[str]) -> set[str]:
        drop = {name for name in names if name in EXCLUDE_DIRS}
        rel = Path(os.path.relpath(os.path.realpath(dirpath), source_real))
        if set(rel.parts) & EVIDENCE_DIRS:
            drop |= {
                name for name in names
                if Path(name).suffix.lower() in HEAVY_EXT or name.endswith(".png.import")
            }
        return drop

    return ignore


def make_scratch(project: Path) -> Path:

    root = scratch_root()
    root.mkdir(parents=True, exist_ok=True)
    dest = Path(tempfile.mkdtemp(prefix=f"{project.name}-", dir=root))

    dest.rmdir()
    shutil.copytree(project.resolve(), dest, ignore=_copy_ignore(project))
    return dest


def run_godot(project: Path, args: list[str]) -> tuple[int, str]:


    rc, out, err = hostenv.run_captured(
        [GODOT, "--headless", "--path", hostenv.godot_arg_path(str(project.resolve())), *args],
        os.getcwd(), TIMEOUT,
    )
    if rc == -9:
        return -9, f"timed out after {TIMEOUT}s"
    if rc == -2:
        print(hostenv.godot_not_found_hint(GODOT), file=sys.stderr)
        raise SystemExit(2)
    return rc, f"{out}\n{err}"


def first_fatal(log: str) -> str:

    return next(
        (line for line in log.splitlines() if line.startswith(FATAL_PREFIXES)),
        "",
    )


def import_project(project: Path) -> tuple[bool, str]:


    attempts: list[tuple[int, str]] = []
    for attempt in range(1, 4):
        rc, log = run_godot(project, ["--import"])
        fatal = first_fatal(log)
        attempts.append((rc, fatal))
        if attempt >= 2 and rc == 0 and not fatal:
            earlier = [
                f"pass {i}: rc={old_rc}" + (f", {old_fatal}" if old_fatal else "")
                for i, (old_rc, old_fatal) in enumerate(attempts[:-1], start=1)
                if old_rc != 0 or old_fatal
            ]
            return True, (
                "; ".join(earlier) + f"; pass {attempt} clean"
                if earlier else ""
            )

    rc, fatal = attempts[-1]
    if rc != 0:
        return False, f"cold import exit {rc} on verification pass"
    return False, fatal or "cold import did not reach a clean verification pass"


def declared_paths(manifest: dict | list) -> list[tuple[str, str]]:


    def from_seq(seq: list) -> list[str]:
        plain = [x for x in seq if isinstance(x, str)]
        if plain:
            return plain
        entries = [e for e in seq if isinstance(e, dict) and (e.get("scene") or e.get("path"))]
        entries.sort(key=lambda e: e.get("order", 0))
        return [str(e.get("scene") or e.get("path")) for e in entries]

    if isinstance(manifest, list):
        return [("level", p) for p in from_seq(manifest)]

    out: list[tuple[str, str]] = []
    for key in ("levels", "scenes", "stages"):
        if isinstance(manifest.get(key), list):
            out += [("level", p) for p in from_seq(manifest[key])]
            break
    for key in ("main_menu", "menu_scene", "entry_scene"):
        if isinstance(manifest.get(key), str):
            out.append(("menu", manifest[key]))
    seen: set[str] = set()
    for name, path in (manifest.get("endings") or {}).items():
        if isinstance(path, str) and path not in seen:
            seen.add(path)
            out.append((f"ending:{name}", path))
    return out


def load_scene(project: Path, scene: str) -> tuple[bool, str]:

    rc, log = run_godot(project, [scene, "--quit-after", str(FRAMES)])
    fatal = first_fatal(log)
    if rc != 0:
        return False, f"exit {rc}" + (f"; {fatal}" if fatal else "")
    return (not fatal), fatal


def check_project(project: Path) -> tuple[int, int]:


    given = project.resolve()
    project = project_root(given)
    manifest_path = project / "gb_levels.json"
    if not manifest_path.is_file():
        where = project if project == given else f"{given} or {project}"
        print(f"{given.name}: no gb_levels.json under {where} "
              "— nothing declared, nothing addressable")
        return 0, 1
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"{given.name}: gb_levels.json unreadable: {exc}")
        return 0, 1

    paths = declared_paths(manifest)
    if not paths:
        print(f"{given.name}: gb_levels.json declares no scene paths")
        return 0, 1

    missing = []
    for role, scene in paths:
        rel = scene[len("res://"):] if scene.startswith("res://") else scene
        if not (project / rel).is_file():
            print(f"  FAIL [{role}] {scene} — not in the project")
            missing.append(scene)
    if missing:
        return 0, len(missing)

    scratch: Path | None = None
    try:
        scratch = make_scratch(project)


        print(f"{given.name}: {len(paths)} declared (cold scratch {scratch})")
        imported, detail = import_project(scratch)
        if not imported:
            print(f"  FAIL [import] {detail[:160]}")
            return 0, len(paths)
        if detail:
            print(f"  note [import] {detail[:160]}")

        ok_n = bad_n = 0
        for role, scene in paths:
            ok, detail = load_scene(scratch, scene)
            print(f"  {'ok  ' if ok else 'FAIL'} [{role}] {scene}"
                  + (f" — {detail[:110]}" if detail else ""))
            ok_n, bad_n = (ok_n + 1, bad_n) if ok else (ok_n, bad_n + 1)
        return ok_n, bad_n
    except OSError as exc:
        print(f"{given.name}: scratch copy failed — {exc}")
        return 0, 1
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("projects", nargs="*", type=Path)
    ap.add_argument("--batch", type=Path, help="directory of projects")
    args = ap.parse_args()

    targets = list(args.projects)
    if args.batch:


        targets += [d for d in sorted(args.batch.iterdir())
                    if d.is_dir() and (project_root(d) / "project.godot").is_file()]
    if not targets:
        ap.error("give at least one project, or --batch DIR")

    total_ok = total_bad = 0
    for project in targets:
        ok_n, bad_n = check_project(project)
        total_ok += ok_n
        total_bad += bad_n
    print(f"\n{total_ok} loaded, {total_bad} did not")
    return 1 if total_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
