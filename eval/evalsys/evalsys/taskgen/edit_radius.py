

from __future__ import annotations

import difflib
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from ..verdict import Item, inconclusive, passed
from .submission import EVAL_SMUGGLE_NAMES, SUBMISSION_DELIVERABLE_NAMES


def is_noise(path: str) -> bool:
    relative = PurePosixPath(path)
    return (
        any(part in {".godot", ".git"} for part in relative.parts)
        or relative.suffix in {".import", ".uid"}
        or relative.name in EVAL_SMUGGLE_NAMES
        or relative.as_posix() in SUBMISSION_DELIVERABLE_NAMES
    )


def fault_sites(oracle: Mapping[str, Any]) -> dict[str, list[int]]:


    sites: dict[str, list[int]] = {}
    for mutation in oracle.get("mutations") or []:
        site = mutation.get("site") or {}
        paths = [site["path"]] if site.get("path") else [
            hunk.split(":", 1)[0].strip()
            for hunk in mutation.get("hunks") or [] if ":" in hunk
        ]
        for path in paths:
            if path:
                sites.setdefault(PurePosixPath(path).as_posix(), []).append(
                    int(site.get("line") or 0)
                )
    return sites


def enclosing_function(lines: Sequence[str], line: int) -> str:

    for text in reversed(lines[:max(0, line)]):
        match = re.match(r"\s*(?:static\s+)?func\s+(\w+)\s*\(", text)
        if match:
            return match.group(1)
    return "<toplevel>"


def hunk_functions(faulty: str, submitted: str) -> list[str]:


    before, after = faulty.splitlines(keepends=True), submitted.splitlines(keepends=True)
    functions: list[str] = []
    for group in difflib.SequenceMatcher(None, before, after).get_grouped_opcodes(3):
        first = next(op for op in group if op[0] != "equal")
        line = first[1] if first[0] == "insert" else first[1] + 1
        functions.append(enclosing_function(before, line))
    return functions


def measure_edit_radius(
    faulty: Mapping[str, bytes], submitted: Mapping[str, bytes], oracle: Mapping[str, Any],
) -> Item:

    sites = fault_sites(oracle)
    patch = set(sites)
    differed = {
        path for path in faulty.keys() | submitted.keys()
        if faulty.get(path) != submitted.get(path)
    }
    touched = {path for path in differed if not is_noise(path)}
    patched = {
        path: sorted({enclosing_function(
            faulty.get(path, b"").decode("utf-8", errors="replace").splitlines(), line
        ) for line in lines})
        for path, lines in sorted(sites.items()) if path.endswith(".gd")
    }
    functions = {
        path: hunk_functions(
            faulty.get(path, b"").decode("utf-8", errors="replace"),
            submitted.get(path, b"").decode("utf-8", errors="replace"),
        )
        for path in sorted(touched & patch) if path.endswith(".gd")
    }
    total = sum(len(names) for names in functions.values()) if functions else None
    focused = sum(
        name in patched[path] for path, names in functions.items() for name in names
    ) if functions else None
    file_focus = len(touched & patch) / len(touched) if touched and patch else None
    func_focus = focused / total if total else None
    evidence = {
        "edit_radius": None, "file_focus": file_focus, "func_focus": func_focus,
        "files_touched": sorted(touched), "files_in_patch_set": sorted(patch),
        "unrelated_files": sorted(touched - patch), "patched_functions": patched,
        "hunks_in_patch_files": total, "hunks_in_patched_functions": focused,
        "hunk_functions": functions,
        "deliverables_ignored": sorted(
            path for path in differed if PurePosixPath(path).as_posix() in SUBMISSION_DELIVERABLE_NAMES
        ),
    }
    if not patch:
        return inconclusive("edit_radius", detail="oracle names no fault-site file", evidence=evidence)
    if not touched:
        return inconclusive(
            "edit_radius",
            detail="submission is byte-identical to the faulty build; no edit to measure",
            evidence=evidence,
        )
    radius = 0.5 + 0.5 * (
        0.6 * file_focus + 0.4 * func_focus if func_focus is not None else file_focus
    )
    evidence["edit_radius"] = radius
    return passed("edit_radius", credit=radius, evidence=evidence,
                  detail="edit locality relative to the faulty build (reported with weight 0)")


def edit_radius_item(faulty: Path, submitted: Path, oracle: Mapping[str, Any]) -> Item:
    def read_project(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and (
                not is_noise(path.relative_to(root).as_posix())
                or path.relative_to(root).as_posix() in SUBMISSION_DELIVERABLE_NAMES
            )
        }

    return measure_edit_radius(read_project(faulty), read_project(submitted), oracle)
