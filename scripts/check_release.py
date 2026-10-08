#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import unicodedata
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DOCS = (
    "README.md", "GAMES.md", "CONTRIBUTING.md", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md",
    "docs/README.md", "docs/quickstart.md", "docs/running.md", "docs/tasks.md",
    "docs/evaluation.md", "docs/evaluation-status.md", "docs/releasing.md",
    "docs/reference-data.md",
)
ENTRY_POINTS = (
    "setup.sh", "run_benchmark.sh", "evaluate.sh", "catalog.json",
    "eval/evalsys/bin/bench", "eval/evalsys/requirements.txt",
    ".gb_api.env.example", ".gitattributes", "CITATION.cff",
    "data/task-data.json", "scripts/fetch_reference_data.py",
)



def tracked_paths(root: Path) -> list[str]:
    return subprocess.check_output(
        ["git", "ls-files", "--cached", "-z"], cwd=root,
    ).decode("utf-8").split("\0")[:-1]


def portable_path_errors(paths: list[str]) -> list[str]:
    errors = []
    seen: dict[str, str] = {}
    for path in paths:
        for prefix in [str(p) for p in PurePosixPath(path).parents if str(p) != "."] + [path]:
            key = unicodedata.normalize("NFC", prefix).casefold()
            previous = seen.setdefault(key, prefix)
            if previous != prefix:
                errors.append(f"case/Unicode collision: {previous} <> {prefix}")
        if "\\" in path or any(part.endswith((".", " ")) for part in path.split("/")):
            errors.append(f"non-portable path: {path}")
        if any(part.startswith("._") for part in path.split("/")):
            errors.append(f"macOS metadata file: {path}")
        if path.startswith(("results/", ".venv/", ".scratch/")) or path == ".gb_api.env":
            errors.append(f"local-only file is tracked: {path}")
    return sorted(set(errors))


def local_link_errors(root: Path, documents: tuple[str, ...]) -> list[str]:
    errors = []
    for name in documents:
        file = root / name
        if not file.is_file():
            errors.append(f"missing document: {name}")
            continue


        text = re.sub(r"```.*?```", "", file.read_text(encoding="utf-8"), flags=re.S)
        for raw in re.findall(r"!?\[[^\]\n]*\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)", text):
            link = urlsplit(raw.strip("<>"))
            if link.scheme or link.netloc or not link.path:
                continue
            target = (file.parent / unquote(link.path)).resolve()
            if not target.is_relative_to(root.resolve()) or not target.exists():
                errors.append(f"broken local link in {name}: {raw}")
    return errors


def catalog_errors(root: Path, *, with_data: bool = False) -> list[str]:
    errors = []
    rows = json.loads((root / "catalog.json").read_text())
    sys.path.insert(0, str(root / "eval/evalsys"))
    from evalsys.frozen_data import load_manifest
    manifest = load_manifest(root)
    seen = set()
    for row in rows:
        game_id = row["id"]
        if game_id in seen:
            errors.append(f"duplicate catalog ID: {game_id}")
        seen.add(game_id)
        record = manifest['games'].get(game_id)
        if record is None or record['path'] != row['path']:
            errors.append(f"catalog entry absent from task-data manifest: {game_id}")
        if not (root / 'docs/reference-games' / game_id / 'assets/ASSET_SOURCES.md').is_file():
            errors.append(f"missing preserved asset source record: {game_id}")
        if not with_data:
            continue
        game = root / row["path"]
        if not game.is_dir() or not game.resolve().is_relative_to(root.resolve()):
            errors.append(f"missing or external game directory: {row['path']}")
            continue
        projects = [game / "project.godot"] if (game / "project.godot").is_file() else list(game.glob("*/project.godot"))
        if len(projects) != 1:
            errors.append(f"catalog game needs one resolvable project.godot: {game_id}")
        if not (game / "assets/ASSET_SOURCES.md").is_file():
            errors.append(f"missing asset source record: {game_id}")
    if seen != set(manifest['games']):
        errors.append('task-data manifest and catalog IDs differ')
    if len(rows) != 41 or sum(len(g['cases']) for g in manifest['games'].values()) != 82:
        errors.append('release requires 41 games and 82 repair cases')
    if not rows:
        errors.append("catalog is empty")
    return errors



def attribute_errors(root: Path) -> list[str]:


    names = [f"release-check.{suffix}" for suffix in ("gltf", "svg", "import")]
    fields = subprocess.check_output(
        ["git", "check-attr", "-z", "text", "--", *names], cwd=root,
    ).decode().split("\0")[:-1]
    return [f"frozen asset bytes need -text: {fields[i]}"
            for i in range(0, len(fields), 3) if fields[i + 2] != "unset"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-license", action="store_true",
                        help="also require a nonempty root LICENSE or LICENSE.txt")
    parser.add_argument("--with-data", action="store_true", help="also check downloaded reference projects")
    args = parser.parse_args()
    errors = []
    try:
        paths = tracked_paths(ROOT)
        if "catalog.json" not in paths:
            errors.append("run this check from a complete SWE-Game Git checkout")
        errors.extend(portable_path_errors(paths))
        for name in ENTRY_POINTS:
            if not (ROOT / name).is_file():
                errors.append(f"missing entry point: {name}")
        documents = tuple(str(p.relative_to(ROOT)) for p in ROOT.rglob('*.md')
                          if '.git' not in p.parts and 'games' not in p.parts
                          and p.name not in {'UPSTREAM_README.md', 'UPSTREAM_CREDITS.md'})
        errors.extend(local_link_errors(ROOT, documents))
        errors.extend(catalog_errors(ROOT, with_data=args.with_data))
        
        errors.extend(attribute_errors(ROOT))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        errors.append(f"could not complete checkout checks: {exc}")
    licensed = any((ROOT / name).is_file() and (ROOT / name).stat().st_size > 100
                   for name in ("LICENSE", "LICENSE.txt"))
    if args.require_license and not licensed:
        errors.append("root license grant is missing; see docs/releasing.md")
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        print(f"{len(errors)} repository check(s) failed")
        return 1
    print(f"PASS: repository checks ({len(paths)} tracked paths)")
    if not licensed:
        print("PENDING: root license grant; --require-license fails until selected")
    print("Dataset integrity and runtime checks are described in docs/releasing.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
