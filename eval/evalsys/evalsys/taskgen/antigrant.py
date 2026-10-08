


from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

SOURCE_SUFFIXES = {".gd", ".cs"}

SKIP_DIRS = frozenset({
    ".godot", ".git", "__pycache__", "inputs", "recording", "verify",
    "compare", "shots", "screenshots", "web", "builds", "export", "hidden",
    "interface",
})

HEALTH_NAMES: tuple[str, ...] = ("health", "hp", "hit_points", "shields")


_DECLARATION_LINE = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:var|const|static\s+var)\b", re.I)


def health_decrease_pattern(names: Iterable[str] = HEALTH_NAMES) -> "re.Pattern[str]":


    alt = "|".join(re.escape(n) for n in names if n)
    head = rf"\b(?:{alt})\b[\"']?\]?\s*"
    return re.compile(
        rf"(?:{head}(?:-=|\+=\s*-|=\s*[^=]{{0,40}}\bminus\b|=\s*[^=]{{0,40}}-))"
        rf"|(?:{head}-\s*[A-Za-z0-9_.(])"
        rf"|(?:{head}=(?!=)\s*[^=\n]*?(?<![\d.])0(?:\.0+)?(?![\d.]))",
        re.I,
    )


HEALTH_DECREASE_RE = health_decrease_pattern()

LIMITS = (
    "concatenated or computed flag names (`'--' + 'gb-route-plan'`)",
    "PackedByteArray / encrypted predicates",
    "checking that a plan JSON exists on disk",


    "a node probe whose autoload name is computed rather than spelled out, or "
    "which recognises the evaluator by something other than its autoload names",
    "unconditional invulnerability or zero damage with no harness test",
    "a victory predicate that is simply always true",
    "timed auto-clear outside the idle grant-probe budget (1100 frames)",
    "spawn overlapping gb_goal without an immediate scene change",
    "a grant in a file the project.godot load graph does not reach",
    "C# using/class references without res:// are not followed",
    "a _ready auto-win whose victory scene is not in gb_levels.json",
    "concessions (vendored probe GB_ASSET_SAMPLING, acknowledged GB_ROUTE_* "
    "reads) do not fail ScanReport.ok or anti_grant_static",
    "mash holds declared extras from frame 0; a delayed is_action_just_pressed win is missed",
    "mash at spawn misses a win button that requires being somewhere else first",
    "mash budget is 1100 frames, same gap as the idle grant probe",
)

_SHARED_MODULE: Any = None


def harness_scan_path() -> Path:


    return Path(__file__).resolve().parents[3] / "tools" / "gates" / "harness_scan.py"


def load_harness_scan() -> Any:

    global _SHARED_MODULE
    if _SHARED_MODULE is not None:
        return _SHARED_MODULE
    path = harness_scan_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"scan-grant source of truth missing: {path}. "
            "Do not reimplement the detector here."
        )
    import sys

    spec = importlib.util.spec_from_file_location("gb_harness_scan", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load harness_scan from {path}")
    module = importlib.util.module_from_spec(spec)


    sys.modules["gb_harness_scan"] = module
    spec.loader.exec_module(module)
    for name in ("scan_project", "scan_fleet", "ScanResult", "scan_auto_win_ready"):
        if not hasattr(module, name):
            raise ImportError(
                f"{path} is mid-change or a different API: missing {name}. "
                "taskgen depends on scan_project / scan_fleet / "
                "ScanResult / scan_auto_win_ready."
            )
    _SHARED_MODULE = module
    return module


def scan_project(root: str | Path) -> Any:

    return load_harness_scan().scan_project(Path(root))


def scan_fleet(games_root: str | Path, *, skip_ids: Iterable[str] = ()) -> list[Any]:

    return load_harness_scan().scan_fleet(Path(games_root), skip_ids=skip_ids)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    detail: str
    snippet: str
    classification: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line,
            "kind": self.kind,
            "detail": self.detail,
            "snippet": self.snippet,
            "classification": self.classification,
        }


def is_blocking_finding(finding: Finding) -> bool:


    if finding.kind == "auto_win_ready":
        return True
    if finding.kind not in ("harness_flag", "node_probe"):
        return False
    return (finding.classification or "unknown") != "concession"


@dataclass
class ScanReport:


    root: str
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    limits: tuple[str, ...] = LIMITS
    authenticity: str = ""
    blocks_film: bool = False
    inclusion: str = "load_graph"
    shared: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:


        return not any(is_blocking_finding(f) for f in self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "ok": self.ok,
            "files_scanned": self.files_scanned,
            "findings": [f.to_dict() for f in self.findings],
            "counts_by_kind": _counts(self.findings),
            "limits": list(self.limits),
            "authenticity": self.authenticity,
            "blocks_film": self.blocks_film,
            "inclusion": self.inclusion,
            "source": "eval/tools/gates/harness_scan.py",
            "shared": self.shared,
        }


def _counts(findings: Iterable[Finding]) -> dict[str, int]:
    out: dict[str, int] = {}
    for finding in findings:
        out[finding.kind] = out.get(finding.kind, 0) + 1
    return out


def _hit_to_finding(hit: Any) -> Finding:
    return Finding(
        path=str(getattr(hit, "path", "")),
        line=int(getattr(hit, "line", 0) or 0),
        kind=str(getattr(hit, "kind", "")),
        detail=(
            f"{getattr(hit, 'classification', '')}: "
            f"{getattr(hit, 'why', '')}"
        ).strip(": "),
        snippet=str(getattr(hit, "snippet", "")),
        classification=str(getattr(hit, "classification", "") or ""),
    )


def _finding_from_baseline(raw: dict[str, Any]) -> Finding:
    return Finding(
        path=str(raw.get("path") or ""),
        line=int(raw.get("line") or 0),
        kind=str(raw.get("kind") or ""),
        detail=str(raw.get("detail") or raw.get("why") or ""),
        snippet=str(raw.get("snippet") or ""),
        classification=str(raw.get("classification") or ""),
    )


def baseline_raw_hits(baseline: dict[str, Any]) -> list[dict[str, Any]]:
    raw_base = list(baseline.get("findings") or [])
    if not raw_base and isinstance(baseline.get("shared"), dict):
        raw_base = list((baseline.get("shared") or {}).get("hits") or [])
    if not raw_base and isinstance(baseline.get("hits"), list):
        raw_base = list(baseline.get("hits") or [])
    return [item for item in raw_base if isinstance(item, dict)]


def new_blocking_sites(
    report: ScanReport, baseline: dict[str, Any]
) -> list[Finding]:


    keys = {
        (item.path, item.line, item.kind)
        for item in (_finding_from_baseline(raw) for raw in baseline_raw_hits(baseline))
        if is_blocking_finding(item)
    }
    return [
        finding
        for finding in report.findings
        if is_blocking_finding(finding)
        and (finding.path, finding.line, finding.kind) not in keys
    ]


def scan_tree(root: str | Path) -> ScanReport:

    base = Path(root).resolve()
    report = ScanReport(root=str(base))
    if not base.is_dir():
        report.findings.append(
            Finding(".", 0, "missing_tree", "path is not a directory", "")
        )
        return report
    result = scan_project(base)
    report.files_scanned = int(getattr(result, "scanned_files", 0) or 0)
    report.authenticity = str(result.authenticity()) if hasattr(result, "authenticity") else ""
    report.blocks_film = bool(getattr(result, "blocks_film", False))
    report.inclusion = str(getattr(result, "inclusion", "load_graph") or "load_graph")
    if hasattr(result, "to_dict"):
        report.shared = result.to_dict()
    for hit in getattr(result, "hits", []) or []:
        report.findings.append(_hit_to_finding(hit))
    return report


def _iter_sources(root: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def scan_auto_win_ready(root: str | Path) -> list[Finding]:


    base = Path(root).resolve()
    if not base.is_dir():
        return []
    return [
        _hit_to_finding(hit)
        for hit in load_harness_scan().scan_auto_win_ready(base)
    ]


def declared_health_property(slot: Any) -> str:


    text = str(slot or "").strip()
    if not text:
        return ""
    tail = re.split(r"[.:/]", text)[-1]
    return tail if re.fullmatch(r"[A-Za-z_]\w*", tail) else ""


def health_decrease_evidence(
    root: str | Path,
    *,
    extra_names: Iterable[str] = (),
) -> list[Finding]:


    names = tuple(dict.fromkeys((*HEALTH_NAMES, *(n for n in extra_names if n))))
    pattern = HEALTH_DECREASE_RE if names == HEALTH_NAMES else health_decrease_pattern(names)
    base = Path(root).resolve()
    hits: list[Finding] = []
    for path in _iter_sources(base):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(base)).replace("\\", "/")
        for index, raw in enumerate(text.splitlines()):
            if raw.lstrip().startswith(("#", "//")):
                continue
            if _DECLARATION_LINE.match(raw) and "-" not in raw and "minus" not in raw.lower():
                continue
            if pattern.search(raw):
                hits.append(
                    Finding(
                        rel,
                        index + 1,
                        "health_decrease",
                        "health-like field is assigned a decreasing update",
                        raw.strip()[:200],
                    )
                )
    return hits


OWNER = "owner"
UNATTRIBUTED = "unattributed"
OTHER_OBJECT = "other_object"

_AUTOLOAD_LINE = re.compile(r'^\s*([A-Za-z_]\w*)\s*=\s*"\*?(res://[^"]+)"', re.M)


_EXT_RESOURCE_HEADER = re.compile(r'\[ext_resource\b([^\]]*)\]')
_EXT_RESOURCE_ATTR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|[^\s\]]+)')


def _ext_resources(text: str) -> list[tuple[str, str, str]]:

    out: list[tuple[str, str, str]] = []
    for header in _EXT_RESOURCE_HEADER.finditer(text):
        attrs = {key: value.strip('"') for key, value in _EXT_RESOURCE_ATTR.findall(header.group(1))}
        kind, path, ident = attrs.get("type", ""), attrs.get("path", ""), attrs.get("id", "")
        if kind in {"Script", "PackedScene"} and path.startswith("res://") and ident:
            out.append((kind, path, ident))
    return out


_NODE_HEADER = re.compile(r'^\[node\b((?:[^\[\]"]|"(?:[^"\\]|\\.)*"|\[[^\]]*\])*)\]', re.M)
_NODE_ATTR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|ExtResource\("?[^)]*"?\)|\[[^\]]*\])')
_SCRIPT_PROP = re.compile(r'^\s*script\s*=\s*ExtResource\("?([^")]+)"?\)', re.M)
_ADD_TO_GROUP = re.compile(r'add_to_group\(\s*&?"(gb_\w+)"')


def _split_declared_owner(slot: Any) -> tuple[str, str]:

    text = str(slot or "").strip()
    parts = [p for p in re.split(r"[.:/]", text) if p]
    prop = declared_health_property(text)
    owner = parts[-2] if prop and len(parts) >= 2 else ""
    return (owner if re.fullmatch(r"[A-Za-z_]\w*", owner or "") else ""), prop


def _autoloads(root: Path) -> dict[str, str]:

    project = root / "project.godot"
    try:
        text = project.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    section = re.search(r"^\[autoload\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if section is None:
        return {}
    return {name: path for name, path in _AUTOLOAD_LINE.findall(section.group(1))}


_SceneNodes = dict[str, dict[str, str]]


def _scene_index(
    root: Path, nodes_out: _SceneNodes | None = None
) -> tuple[dict[str, str], dict[str, set[str]]]:


    root_script: dict[str, str] = {}
    scene_groups: dict[str, list[tuple[str, set[str], str]]] = {}
    scene_nodes: dict[str, list[tuple[str, str, str]]] = {}
    for path in root.rglob("*.tscn"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        res_path = "res://" + str(path.relative_to(root)).replace("\\", "/")
        resources = {ident: (kind, target) for kind, target, ident in _ext_resources(text)}
        entries: list[tuple[str, set[str], str]] = []
        node_entries: list[tuple[str, str, str]] = []
        headers = list(_NODE_HEADER.finditer(text))
        for index, header in enumerate(headers):
            attrs = {key: value for key, value in _NODE_ATTR.findall(header.group(1))}
            body_end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
            body = text[header.end():body_end]
            groups = set(re.findall(r'"(gb_\w+)"', attrs.get("groups", "")))
            script = ""
            script_match = _SCRIPT_PROP.search(body)
            if script_match:
                kind, target = resources.get(script_match.group(1), ("", ""))
                script = target if kind == "Script" else ""
            instance = ""
            instance_match = re.search(r'ExtResource\("?([^")]+)"?\)', attrs.get("instance", ""))
            if instance_match:
                kind, target = resources.get(instance_match.group(1), ("", ""))
                instance = target if kind == "PackedScene" else ""
            is_root = "parent" not in attrs
            if is_root and script:
                root_script[res_path] = script
            entries.append((script, groups, instance))
            name = attrs.get("name", "").strip('"')
            parent = attrs.get("parent", "").strip('"')
            node_path = "" if is_root else (name if parent == "." else f"{parent}/{name}")
            node_entries.append((node_path, script, instance))
        scene_groups[res_path] = entries
        scene_nodes[res_path] = node_entries
    script_groups: dict[str, set[str]] = {}
    for entries in scene_groups.values():
        for script, groups, instance in entries:
            if not groups:
                continue
            target = script or root_script.get(instance, "")
            if target:
                script_groups.setdefault(target, set()).update(groups)
    if nodes_out is not None:
        for scene, node_entries in scene_nodes.items():
            table: dict[str, str] = {}
            for node_path, script, instance in node_entries:
                target = script or root_script.get(instance, "")
                if target:
                    table[node_path] = target
            nodes_out[scene] = table
    return root_script, script_groups


MIRROR = "mirror"

_CAST_WRAPPERS = ("float", "int", "floor", "ceil", "round", "floori", "ceili", "roundi", "absf", "absi")
_CAST_RE = re.compile(r"^(?:%s)\(\s*(.*?)\s*\)$" % "|".join(_CAST_WRAPPERS))
_ALIAS_SOURCE_RE = re.compile(r"^(?:([A-Za-z_]\w*)\s*\.\s*)?([A-Za-z_]\w*)$")
_CLASS_NAME_RE = re.compile(r"^\s*class_name\s+([A-Za-z_]\w*)", re.M)
_SKIP_SOURCES = frozenset({"self", "true", "false", "null", "PI", "INF", "NAN"})
_HEALTH_OWNER_GROUPS = frozenset({"gb_player", "gb_state"})


def _getter_sources(text: str, prop: str) -> dict[int, str]:


    lines = text.splitlines()
    declaration = re.compile(rf"^\s*var\s+{re.escape(prop)}\s*(?::\s*\w+)?\s*:\s*$")
    out: dict[int, str] = {}
    for index, line in enumerate(lines):
        if not declaration.match(line):
            continue
        indent = len(line.expandtabs()) - len(line.expandtabs().lstrip())
        getter_indent = None
        for offset in range(index + 1, len(lines)):
            raw = lines[offset]
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            depth = len(raw.expandtabs()) - len(raw.expandtabs().lstrip())
            if depth <= indent:
                break
            if stripped == "get:":
                getter_indent = depth
                continue
            if getter_indent is not None and depth <= getter_indent:
                getter_indent = None
            if getter_indent is not None and stripped.startswith("return "):
                expr = stripped[7:].split("#", 1)[0].strip()

                expr = re.sub(r"\s+if\s+is_instance_valid\([^)]*\)\s+else\s+0(?:\.0)?$", "", expr)
                out[offset] = expr
    return out


def _alias_line_re(prop: str) -> "re.Pattern[str]":
    return re.compile(
        rf"^\s*(?:([A-Za-z_]\w*)\s*[.:]\s*)?{re.escape(prop)}\s*=(?!=)\s*(.+?)\s*(?:#.*)?$"
    )


def _peel_casts(expr: str) -> str:
    text = expr.strip()
    while True:
        match = _CAST_RE.match(text)
        if match is None:
            return text
        text = match.group(1).strip()


def _var_target_re(name: str) -> "re.Pattern[str]":
    return re.compile(
        rf"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:var\s+)?{re.escape(name)}\b(?:\s*:\s*[\w.]+)?\s*:?=\s*(.+?)\s*$",
        re.M,
    )


def _resolve_receiver_scripts(
    receiver: str,
    *,
    script_text: str,
    script_path: str,
    autoloads: dict[str, str],
    root_script: dict[str, str],
    scene_nodes: _SceneNodes,
    player_scripts: set[str],
    class_scripts: dict[str, str],
) -> set[str]:


    if receiver in autoloads:
        target = autoloads[receiver]
        if target.endswith(".tscn"):
            target = root_script.get(target, "")
        return {target} if target else set()
    if "player" in receiver.lower():
        return set(player_scripts)
    out: set[str] = set()
    own_scenes = [scene for scene, script in root_script.items() if script == script_path]
    for match in _var_target_re(receiver).finditer(script_text):
        rhs = match.group(1)
        node_match = re.match(r'^\$(?:"([^"]+)"|([\w/]+))', rhs) or re.match(
            r'^get_node\(\s*"([^"]+)"\s*\)', rhs
        )
        if node_match:
            node_path = next(g for g in node_match.groups() if g is not None)
            for scene in own_scenes:
                script = scene_nodes.get(scene, {}).get(node_path, "")
                if script:
                    out.add(script)
            continue
        unique = re.match(r"^%(\w+)", rhs)
        if unique:
            for scene in own_scenes:
                for node_path, script in scene_nodes.get(scene, {}).items():
                    if node_path.rsplit("/", 1)[-1] == unique.group(1) and script:
                        out.add(script)
            continue
        preload = re.match(r'^preload\(\s*"(res://[^"]+\.gd)"\s*\)\s*\.new\(', rhs)
        if preload:
            out.add(preload.group(1))
            continue
        ctor = re.match(r"^([A-Za-z_]\w*)\.new\(", rhs)
        if ctor and ctor.group(1) in class_scripts:
            out.add(class_scripts[ctor.group(1)])
    return out


def _mirror_sources(
    base: Path,
    prop: str,
    *,
    owner_scripts: set[str],
    non_owner_scripts: set[str],
    owner_names: set[str],
    autoloads: dict[str, str],
    root_script: dict[str, str],
    scene_nodes: _SceneNodes,
    player_scripts: set[str],
) -> list[dict[str, Any]]:


    alias_re = _alias_line_re(prop)
    decrease_re = health_decrease_pattern((prop,))
    class_scripts: dict[str, str] = {}
    texts: dict[str, tuple[str, str]] = {}
    for path in _iter_sources(base):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(base)).replace("\\", "/")
        texts[rel] = ("res://" + rel, text)
        for match in _CLASS_NAME_RE.finditer(text):
            class_scripts.setdefault(match.group(1), "res://" + rel)
    out: list[dict[str, Any]] = []
    for rel, (script_path, text) in texts.items():
        self_groups = set(_ADD_TO_GROUP.findall(text))
        is_owner_script = script_path in owner_scripts or bool(_HEALTH_OWNER_GROUPS & self_groups)
        is_other_script = (
            script_path in non_owner_scripts
            or (bool(self_groups) and not _HEALTH_OWNER_GROUPS & self_groups)
        ) and not is_owner_script
        getter_sources = _getter_sources(text, prop)
        for index, raw in enumerate(text.splitlines()):
            if _DECLARATION_LINE.match(raw) or decrease_re.search(raw):
                continue
            match = alias_re.match(raw)
            if index in getter_sources:
                receiver, rhs = None, _peel_casts(getter_sources[index])
            elif match is not None:
                receiver, rhs = match.group(1), _peel_casts(match.group(2))
            else:
                continue
            source = _ALIAS_SOURCE_RE.match(rhs)
            if source is None or rhs in _SKIP_SOURCES:
                continue
            src_receiver, src_prop = source.group(1), source.group(2)
            if src_receiver is None and src_prop == prop:
                continue
            if src_receiver in _SKIP_SOURCES:
                src_receiver = None

            if receiver is None or receiver == "self":
                if not is_owner_script or is_other_script:
                    continue
            elif receiver.lower() not in owner_names and "player" not in receiver.lower():
                continue
            if src_receiver is None:
                source_scripts = set() if is_other_script else {script_path}
            else:
                source_scripts = _resolve_receiver_scripts(
                    src_receiver,
                    script_text=text, script_path=script_path, autoloads=autoloads,
                    root_script=root_script, scene_nodes=scene_nodes,
                    player_scripts=player_scripts, class_scripts=class_scripts,
                )
            out.append({
                "path": rel,
                "line": index + 1,
                "snippet": raw.strip()[:200],
                "source_receiver": src_receiver or "",
                "source_property": src_prop,
                "source_scripts": sorted(source_scripts),
            })
    return out


def _mirror_hits(
    base: Path,
    mirrors: list[dict[str, Any]],
    *,
    owner_scripts: set[str],
    non_owner_scripts: set[str],
    already: set[tuple[str, int]],
) -> list[Finding]:

    out: list[Finding] = []
    seen = set(already)
    evidence_cache: dict[str, list[Finding]] = {}


    ordered_mirrors = sorted(
        mirrors,
        key=lambda item: (
            bool(item.get("source_receiver")),
            str(item.get("path") or ""),
            int(item.get("line") or 0),
        ),
    )
    for mirror in ordered_mirrors:
        src_prop = mirror["source_property"]
        src_receiver = mirror["source_receiver"]
        source_scripts = set(mirror["source_scripts"])
        alias_script = "res://" + mirror["path"]
        if src_prop not in evidence_cache:
            evidence_cache[src_prop] = health_decrease_evidence(base, extra_names=(src_prop,))
        raw_hits = evidence_cache[src_prop]
        src_re = re.compile(rf"\b{re.escape(src_prop)}\b")
        for hit in raw_hits:
            key = (hit.path, hit.line)
            if key in seen or not src_re.search(hit.snippet):
                continue
            hit_script = "res://" + hit.path
            receiver = _receiver_before(hit.snippet, src_prop)
            accepted = False
            if receiver is None or receiver == "self":
                accepted = hit_script in source_scripts and hit_script not in non_owner_scripts
            elif src_receiver and receiver == src_receiver:
                accepted = hit_script == alias_script or hit_script in owner_scripts
            elif "player" in receiver.lower() and src_receiver and "player" in src_receiver.lower():
                accepted = True
            if not accepted:
                continue
            seen.add(key)
            via = f"{src_receiver}.{src_prop}" if src_receiver else src_prop
            out.append(Finding(
                hit.path, hit.line, "health_decrease_mirror",
                f"decreasing write to `{via}`, which the owner mirrors into the declared "
                f"property at {mirror['path']}:{mirror['line']} (`{mirror['snippet']}`)",
                hit.snippet, OWNER,
            ))
    return out


def _receiver_before(snippet: str, prop: str) -> str | None:


    match = re.search(
        rf"(?:([A-Za-z_]\w*)|(\)|\]))(?:\s*\.\s*|:(?=\w)|\s*\[\s*[\"'])\b{re.escape(prop)}\b",
        snippet,
    )
    if match is None:
        return None
    if match.group(2):
        return "<expr>"
    return match.group(1)


def health_write_binding(
    root: str | Path,
    declared_slot: Any,
    *,
    levels: Iterable[str] = (),
) -> dict[str, Any]:


    base = Path(root).resolve()
    owner_hint, prop = _split_declared_owner(declared_slot)
    names: tuple[str, ...] = (prop,) if prop else HEALTH_NAMES
    autoloads = _autoloads(base)
    scene_nodes: _SceneNodes = {}
    root_script, script_groups = _scene_index(base, scene_nodes)
    owner_scripts = set(autoloads.values())
    for scene in levels:
        script = root_script.get(str(scene), "")
        if script:
            owner_scripts.add(script)
    for autoload_path in list(autoloads.values()):
        if autoload_path.endswith(".tscn") and autoload_path in root_script:
            owner_scripts.add(root_script[autoload_path])
    for script, groups in script_groups.items():
        if _HEALTH_OWNER_GROUPS & groups:
            owner_scripts.add(script)
    non_owner_scripts = {
        script for script, groups in script_groups.items()
        if groups and not _HEALTH_OWNER_GROUPS & groups and script not in owner_scripts
    }
    owner_names = {name.lower() for name in autoloads}
    if owner_hint:
        owner_names.add(owner_hint.lower())

    raw_hits = health_decrease_evidence(base, extra_names=names) if prop else health_decrease_evidence(base)
    prop_re = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(n) for n in names), re.I)
    classified: list[Finding] = []
    for hit in raw_hits:
        if not prop_re.search(hit.snippet):
            continue
        script_path = "res://" + hit.path
        try:
            script_text = (base / hit.path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            script_text = ""
        self_groups = set(_ADD_TO_GROUP.findall(script_text))
        is_owner_script = script_path in owner_scripts or bool(_HEALTH_OWNER_GROUPS & self_groups)
        is_other_script = (
            script_path in non_owner_scripts
            or (bool(self_groups) and not _HEALTH_OWNER_GROUPS & self_groups)
        ) and not is_owner_script
        receiver = _receiver_before(hit.snippet, prop or "")
        if receiver is None or receiver == "self":
            if is_owner_script:
                binding, why = OWNER, "self write in a script attached to an owner (autoload / level root / gb_player / gb_state)"
            elif is_other_script:
                binding, why = OTHER_OBJECT, "self write in a script attached only to non-player gb_* nodes"
            else:
                binding, why = UNATTRIBUTED, "self write in a script no scene attaches (possible holder object)"
        elif receiver.lower() in owner_names:
            binding, why = OWNER, f"write through the declared owner / autoload `{receiver}`"
        elif "player" in receiver.lower() or (receiver == "<expr>" and "gb_player" in hit.snippet):
            binding, why = OWNER, f"write through a player reference `{receiver}`"
        else:
            binding, why = OTHER_OBJECT, f"write through another object `{receiver}`"
        classified.append(Finding(hit.path, hit.line, "health_decrease", why, hit.snippet, binding))
    mirrors: list[dict[str, Any]] = []
    mirror_hits: list[Finding] = []
    if prop:
        player_scripts = {script for script, groups in script_groups.items() if "gb_player" in groups}
        mirrors = _mirror_sources(
            base, prop,
            owner_scripts=owner_scripts, non_owner_scripts=non_owner_scripts,
            owner_names=owner_names, autoloads=autoloads, root_script=root_script,
            scene_nodes=scene_nodes, player_scripts=player_scripts,
        )
        mirror_hits = _mirror_hits(
            base, mirrors,
            owner_scripts=owner_scripts, non_owner_scripts=non_owner_scripts,
            already={(h.path, h.line) for h in classified},
        )
        classified.extend(mirror_hits)
    counts = {
        OWNER: sum(1 for h in classified if h.classification == OWNER),
        UNATTRIBUTED: sum(1 for h in classified if h.classification == UNATTRIBUTED),
        OTHER_OBJECT: sum(1 for h in classified if h.classification == OTHER_OBJECT),
        MIRROR: len(mirror_hits),
    }
    return {
        "property": prop,
        "owner_hint": owner_hint,
        "scanned_names": list(names),
        "owner_scripts": sorted(owner_scripts),
        "hits": classified,
        "mirrors": mirrors,
        **counts,
        "writable": counts[OWNER] + counts[UNATTRIBUTED] > 0,
    }
