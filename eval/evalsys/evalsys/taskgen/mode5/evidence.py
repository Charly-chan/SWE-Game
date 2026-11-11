"""Bounded Unity serialized-reference inspection for the release controller.

This is an artifact graph, not a C# interpreter or an appearance judge. It
deliberately gives no gameplay credit for keywords, comments or file counts.
Dynamic Resources.Load/addressable creation needs independent editor/runtime
evidence; its absence from this conservative graph is not proof of a defect.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Iterable


GRAPH_SCHEMA = "gamebench.mode5.unity-reference-graph.v1"
GUID = re.compile(r"\bguid:\s*([0-9a-fA-F]{32})\b")
HEADER = re.compile(r"^--- !u!(\d+) &(-?\d+)(?: stripped)?\s*$", re.M)
REFERENCE = re.compile(r"\{\s*fileID:\s*(-?\d+)(?:[^}\n]*)\}")
ZERO_GUID = "0" * 32
SERIALIZED_SUFFIXES = {".unity", ".prefab", ".asset", ".mat", ".controller", ".anim"}
SDK_PREFIXES = ("Assets/GameBenchmarkSDK/", "Assets/GameBenchmark/")


@dataclass(frozen=True)
class Limits:
    files: int = 30000
    file_bytes: int = 8 * 1024 * 1024
    total_bytes: int = 64 * 1024 * 1024
    visited_assets: int = 5000


@dataclass
class Graph:
    reachable_files: set[str] = field(default_factory=set)
    authored_files: set[str] = field(default_factory=set)
    attached_scripts: set[str] = field(default_factory=set)
    documents: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    unresolved_references: list[dict[str, str]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    complete: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": GRAPH_SCHEMA, "complete": self.complete,
            "reachable_files": sorted(self.reachable_files),
            "authored_files": sorted(self.authored_files),
            "attached_scripts": sorted(self.attached_scripts),
            "documents": {path: rows for path, rows in sorted(self.documents.items())},
            "unresolved_references": sorted(self.unresolved_references,
                                           key=lambda row: (row["source"], row["guid"])),
            "issues": sorted(set(self.issues)),
            "limitations": [
                "Serialized reachability is indirect evidence, not gameplay verification.",
                "Dynamic loads and prefab instance overrides require separate verification.",
                "Resource inventory does not measure rendered similarity or content fidelity.",
            ],
        }


def collect_reference_graph(
    project: Path, entry_scenes: Iterable[str], *,
    baseline_project: Path | None = None, limits: Limits = Limits(),
) -> Graph:
    """Inspect active serialized references without importing or running code.

    Entry scenes must come from the validated interface/build contract. The
    evaluator's frozen rubric, not this graph, sets scoring denominators.
    Baseline-identical files and SDK files never count as candidate-authored.
    All symlinks/junctions are rejected, including links that stay in project.
    """
    project = Path(project)
    if _link(project) or not project.is_dir():
        raise ValueError("project must be a real directory, not a link")
    project = project.resolve()
    graph = Graph()
    budget = [limits.total_bytes]
    files: dict[str, Path] = {}
    for directory, dirs, names in os.walk(project, followlinks=False):
        kept = []
        for name in sorted(dirs):
            path = Path(directory) / name
            if _link(path):
                _incomplete(graph, f"linked directory excluded: {path.relative_to(project).as_posix()}")
            elif name not in {"Library", "Temp", "Logs", "obj", ".git"}:
                kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(project).as_posix()
            if _link(path) or not path.is_file():
                _incomplete(graph, f"non-regular file excluded: {relative}")
                continue
            files[relative] = path
            if len(files) > limits.files:
                _incomplete(graph, "file enumeration limit exceeded")
                return graph
    guid_paths: dict[str, str] = {}
    conflicted: set[str] = set()
    for relative, path in sorted(files.items()):
        if not relative.startswith("Assets/") or not relative.endswith(".meta"):
            continue
        text = _read(path, graph, budget, limits)
        match = re.search(r"^guid:\s*([0-9a-fA-F]{32})\s*$", text, re.M)
        if not match:
            continue
        guid = match.group(1).lower()
        asset = relative[:-5]
        if guid == ZERO_GUID or asset not in files:
            continue
        if guid in guid_paths:
            conflicted.add(guid)
            _incomplete(graph, f"duplicate asset GUID: {guid}")
        else:
            guid_paths[guid] = asset
    for guid in conflicted:
        guid_paths.pop(guid, None)
    pending: deque[tuple[str, str]] = deque()
    scenes = sorted(set(entry_scenes))
    if not scenes:
        _incomplete(graph, "no entry scenes supplied")
    for scene in scenes:
        if not _safe_asset_path(scene) or not scene.endswith(".unity"):
            _incomplete(graph, "invalid entry scene path")
        elif scene not in files:
            _incomplete(graph, f"missing entry scene: {scene}")
        else:
            pending.append((scene, "entry_scene"))
    while pending:
        relative, source = pending.popleft()
        if relative in graph.reachable_files:
            continue
        if len(graph.reachable_files) >= limits.visited_assets:
            _incomplete(graph, "reachable asset limit exceeded")
            break
        graph.reachable_files.add(relative)
        path = files[relative]
        if not relative.startswith(SDK_PREFIXES) and not _baseline_identical(
            relative, path, baseline_project, graph, budget, limits,
        ):
            graph.authored_files.add(relative)
        suffix = path.suffix.lower()
        if suffix == ".cs":
            # A script referenced by m_Script is actually attached; merely
            # appearing as a resource GUID is not an attached component.
            if source.endswith(":m_Script") and relative in graph.authored_files:
                graph.attached_scripts.add(relative)
            continue
        if suffix not in SERIALIZED_SUFFIXES:
            continue
        text = _read(path, graph, budget, limits)
        try:
            blocks = _blocks(text)
        except ValueError:
            _incomplete(graph, f"invalid serialized document IDs: {relative}")
            continue
        if not blocks:
            _incomplete(graph, f"no readable serialized documents: {relative}")
            continue
        if suffix in {".unity", ".prefab"}:
            selected = _reachable_documents(blocks)
        else:
            selected = set(blocks)
        graph.documents[relative] = []
        for ident in sorted(selected):
            class_id, body = blocks[ident]
            graph.documents[relative].append({
                "file_id": ident, "class_id": class_id,
                "audit_reference": f"{relative}#fileID={ident}",
                "owner_file_id": _owner_file_id(body),
                "scalars": _scalars(body),
                "script_guid": next((GUID.search(ref).group(1).lower()
                                     for ref, name in _reference_maps(body)
                                     if name == "m_Script" and GUID.search(ref)), None),
                "script_path": next((guid_paths.get(GUID.search(ref).group(1).lower())
                                     for ref, name in _reference_maps(body)
                                     if name == "m_Script" and GUID.search(ref)), None),
                "event_methods": re.findall(r"^\s*m_MethodName:\s*([A-Za-z_]\w*)\s*$", body, re.M),
                "parent_file_id": _parent_file_id(body),
                "referenced_assets": sorted({guid_paths[GUID.search(ref).group(1).lower()]
                                             for ref, _ in _reference_maps(body)
                                             if GUID.search(ref) and
                                             GUID.search(ref).group(1).lower() in guid_paths}),
            })
            for reference, field_name in _reference_maps(body):
                match = GUID.search(reference)
                if not match:
                    continue
                guid = match.group(1).lower()
                if guid == ZERO_GUID:
                    continue  # Unity built-in resource, not a missing asset.
                target = guid_paths.get(guid)
                if target is None:
                    graph.unresolved_references.append({"source": relative, "guid": guid})
                    continue
                origin = relative + (":m_Script" if field_name == "m_Script" else ":reference")
                if target in graph.reachable_files:
                    if origin.endswith(":m_Script") and target in graph.authored_files:
                        graph.attached_scripts.add(target)
                else:
                    pending.append((target, origin))
    return graph


def _blocks(text: str) -> dict[int, tuple[int, str]]:
    matches = list(HEADER.finditer(text))
    result = {}
    for index, match in enumerate(matches):
        ident = int(match.group(2))
        if ident in result:
            raise ValueError("duplicate Unity serialized fileID")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result[ident] = (int(match.group(1)), text[match.end():end])
    return result


def _reachable_documents(blocks: dict[int, tuple[int, str]]) -> set[int]:
    roots = [ident for ident, (kind, _) in blocks.items() if kind == 1660057539]
    if not roots:
        # Unity versions without SceneRoots: a root Transform/RectTransform
        # has an explicit zero father. Orphan MonoBehaviours are never roots.
        roots = [ident for ident, (kind, body) in blocks.items()
                 if (kind in {4, 224} and re.search(r"m_Father:\s*\{fileID:\s*0\}", body))
                 or (kind == 1001 and re.search(r"m_TransformParent:\s*\{fileID:\s*0\}", body))]
    pending = deque(roots)
    selected: set[int] = set()
    while pending:
        ident = pending.popleft()
        if ident not in blocks or ident in selected:
            continue
        kind, body = blocks[ident]
        if kind == 1 and re.search(r"^\s*m_IsActive:\s*0\s*$", body, re.M):
            continue
        if kind not in {1, 4, 224, 1001, 1660057539} and re.search(
            r"^\s*m_Enabled:\s*0\s*$", body, re.M,
        ):
            continue
        # A Transform pointing at an inactive GameObject must not leak its
        # children/components into the active content inventory.
        owner = re.search(r"m_GameObject:\s*\{fileID:\s*(-?\d+)\}", body)
        if owner and int(owner.group(1)) in blocks:
            owner_body = blocks[int(owner.group(1))][1]
            if re.search(r"^\s*m_IsActive:\s*0\s*$", owner_body, re.M):
                continue
        selected.add(ident)
        for reference, _ in _reference_maps(body):
            # Cross-asset file IDs do not name a document in this file.
            if GUID.search(reference):
                continue
            match = REFERENCE.fullmatch(reference)
            assert match is not None
            linked = int(match.group(1))
            if linked and linked in blocks:
                pending.append(linked)
    return selected


def _reference_maps(body: str) -> Iterable[tuple[str, str]]:
    # Unity's text serializer uses inline {fileID, guid, type} mappings.
    # Matching a whole unquoted field/list item excludes fake references in
    # m_Name strings, comments and arbitrary embedded script text.
    pattern = re.compile(
        r"^\s*(?:-\s*)?(?:([A-Za-z_][\w.]*)\s*:\s*)?"
        r"(\{\s*fileID:\s*-?\d+[^}\n]*\})\s*(?:#.*)?$",
    )
    for line in body.splitlines():
        match = pattern.fullmatch(line)
        if match:
            yield match.group(2), match.group(1) or ""


def _owner_file_id(body: str) -> int | None:
    match = re.search(r"^\s*m_GameObject:\s*\{fileID:\s*(-?\d+)\}\s*$", body, re.M)
    return int(match.group(1)) if match else None


def _parent_file_id(body: str) -> int | None:
    match = re.search(r"^\s*m_Father:\s*\{fileID:\s*(-?\d+)\}\s*$", body, re.M)
    return int(match.group(1)) if match else None


def _scalars(body: str) -> dict[str, str]:
    # Selected simple serialized fields only. Never infer gameplay behavior
    # from object names or arbitrary nested strings.
    result = {}
    for name in ("role", "m_Name", "m_Text", "m_LocalPosition", "m_AnchoredPosition"):
        match = re.search(r"^  " + name + r":\s*([^\n]*)$", body, re.M)
        if match:
            result[name] = match.group(1).strip().strip('"\'')
    return result


def _link(path: Path) -> bool:
    value = path.lstat()
    return path.is_symlink() or bool(getattr(value, "st_file_attributes", 0)
                                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _safe_asset_path(value: str) -> bool:
    if not isinstance(value, str) or "\\" in value or ":" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and value.startswith("Assets/") and ".." not in path.parts


def _read(path: Path, graph: Graph, budget: list[int], limits: Limits) -> str:
    size = path.stat().st_size
    if size > limits.file_bytes or size > budget[0]:
        _incomplete(graph, f"text inspection budget exceeded: {path.name}")
        return ""
    budget[0] -= size
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        _incomplete(graph, f"unreadable serialized text: {path.name}")
        return ""


def _baseline_identical(relative: str, path: Path, baseline: Path | None,
                        graph: Graph, budget: list[int], limits: Limits) -> bool:
    if baseline is None:
        return False
    original = Path(baseline) / relative
    if not original.is_file() or _link(original):
        return False
    size = path.stat().st_size
    if size != original.stat().st_size:
        return False
    if size > limits.file_bytes or 2 * size > budget[0]:
        _incomplete(graph, "baseline comparison budget exceeded")
        return True  # Never credit an uninspected possible scaffold copy.
    budget[0] -= 2 * size
    return path.read_bytes() == original.read_bytes()


def _incomplete(graph: Graph, reason: str) -> None:
    graph.complete = False
    graph.issues.append(reason)
