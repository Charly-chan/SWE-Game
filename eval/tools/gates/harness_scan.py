#!/usr/bin/env python3


from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


UNREADABLE_DIR_NAMES = frozenset({".godot", ".git"})

FOLLOW_SUFFIXES = (".gd", ".cs", ".tscn", ".tres", ".scn", ".json")
SCRIPT_SUFFIXES = (".gd", ".cs")
RES_PATH_RE = re.compile(r"res://[A-Za-z0-9_./+-]+")
QUOTED_DIR_RE = re.compile(r"""['"](res://[^'"]+/)['"]""")
CLASS_NAME_RE = re.compile(r"(?m)^class_name\s+(\w+)")
CS_CLASS_RE = re.compile(r"\bclass\s+(\w+)\b")

HARNESS_FLAGS = ("--gb-route-plan", "--gb-route-out")

# Env twin of --gb-route-*. Always harness-class, same grant/concession/unknown


HARNESS_ENV_ROUTE_PREFIX = "GB_ROUTE_"


HARNESS_ENV_EVAL_SET_KEYS = frozenset({"GB_ASSET_SAMPLING"})


HARNESS_ENV_GRANTABLE = frozenset({"GB_AUTOPILOT"})
HARNESS_ENV_GRANTABLE_PREFIXES = ("GB_CAPTURE_",)


INJECT_MODULE = (
    Path(__file__).resolve().parents[2] / "evalsys" / "evalsys" / "probe" / "inject.py"
)
INJECT_REGISTRY_NAME = "INJECTED_AUTOLOADS"


@lru_cache(maxsize=4)
def injected_autoload_names(source: Path = INJECT_MODULE) -> frozenset[str]:


    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise RuntimeError(
            f"cannot read the injected-autoload registry from {source}: {exc}"
        ) from exc
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if not any(
            isinstance(t, ast.Name) and t.id == INJECT_REGISTRY_NAME for t in targets
        ):
            continue
        value = node.value
        if value is None:
            break
        names = {
            child.value
            for child in ast.walk(value)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        }
        if names:
            return frozenset(names)
        break
    raise RuntimeError(
        f"{source} does not define a non-empty {INJECT_REGISTRY_NAME}; the "
        "node-probe check cannot know which autoloads the evaluator injects"
    )


@lru_cache(maxsize=4)
def _node_probe_re(names: frozenset[str]) -> re.Pattern[str]:
    alternation = "|".join(re.escape(n) for n in sorted(names))
    return re.compile(rf"\b(?:{alternation})\b")


NODE_LOOKUP_RE = re.compile(
    r"\b(?:get_node_or_null|get_node|has_node|find_child|find_children"
    r"|GetNodeOrNull|GetNode|HasNode|FindChild|FindChildren)"
    r"(?:\s*<[^<>()]*>)?\s*\("
)


NODE_LOOKUP_LOOKBACK = 3


def _strip_string_literals(code: str) -> str:


    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(code):
        ch = code[i]
        if quote:
            if ch == "\\" and i + 1 < len(code):
                out.append("  ")
                i += 2
                continue
            if ch == quote:
                quote = None
            out.append(" ")
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


PRESENTATION_RE = re.compile(
    r"""
    DisplayServer\.
    |RenderingServer\.
    |AudioServer\.
    |get_viewport\s*\(
    |ViewportTexture
    |save_png|save_jpg|save_webp|SavePng|SaveJpg
    |Engine\.max_fps
    |window_set_|window_get_
    |set_bus_mute|set_bus_volume_db
    |queue_redraw
    |vsync|VSync
    |has_feature\s*\(\s*["']headless["']
    """,
    re.VERBOSE,
)


PROBE_INTERFACE_RE = re.compile(r"\baxis_value\s*\(")


PROBE_SAMPLING_MARKERS = (
    "_asset_sampling_enabled",
    "sample_asset_use",
    "ASSET_SAMPLE_INTERVAL_FRAMES",
)
PROBE_CENSUS_MARKERS = (
    "_scan_collectibles",
    "_observe_setting_resources",
    "group_census",
    "world_bounds",
    "observed_assets",
)


ASSET_SPOOF_RE = re.compile(
    r"""
    \.texture\s*=
    |\.mesh\s*=
    |\.material(?:_override)?\s*=
    |\.sprite_frames\s*=
    |preload\s*\(
    |ResourceLoader\.load
    |load\s*\(\s*["']res://
    |\.set_texture
    |\.set_mesh
    """,
    re.VERBOSE | re.IGNORECASE,
)

CMDLINE_RE = re.compile(
    r"OS\.(?:get_cmdline_user_args|get_cmdline_args|"
    r"GetCmdlineUserArgs|GetCmdlineArgs)\s*\("
)
ENV_RE = re.compile(
    r"OS\.(?:get_environment|has_environment|GetEnvironment|HasEnvironment)\s*\("
    r"|Environment\.GetEnvironmentVariable\s*\("
)
ENV_KEY_RE = re.compile(r"""["']([A-Z][A-Z0-9_]*)["']""")

READY_GD_RE = re.compile(
    r"func\s+_ready\s*\([^)]*\)\s*(?:->\s*[\w.\[\] ,]+)?\s*:(.*?)(?=\nfunc\s|\Z)",
    re.S,
)
CS_READY_SIG_RE = re.compile(r"\bvoid\s+_Ready\s*\(\s*\)\s*\{")
CHANGE_SCENE_RE = re.compile(
    r"(?:change_scene_to_file|ChangeSceneToFile)\s*(?:\.call_deferred)?\s*\(\s*([^)]*)\)",
    re.I,
)
CONST_PATH_RE = re.compile(
    r"(?m)^\s*(?:public\s+|private\s+|protected\s+|internal\s+|static\s+|"
    r"const\s+|readonly\s+|partial\s+)*"
    r"(?:const\s+|static\s+readonly\s+)?"
    r"(?:[\w.<>\[\]]+\s+)?"
    r"(\w+)\s*(?::=\s*|=\s*)[\"'](res://[^\"']+)[\"']"
)

VICTORY_ENDING_KEYS = frozenset({
    "victory", "win", "success", "run_complete", "game_complete", "complete",
})
DEFEAT_ENDING_KEYS = frozenset({
    "defeat", "failed", "fail", "failure", "game_over", "death", "lose", "loss",
})

# Nearby-line heuristic. Imperfect on purpose: a miss becomes `unknown` and


GRANT_RE = re.compile(
    r"""
    \bhealth\s*=
    |invulnerab
    |POINTS_TO_WIN
    |player_points\s*=
    |(?:^|[^.\w])score\s*=
    |1000000
    |take_hit\s*\(\s*999
    |hurt_enemy
    |weapon_level\s*=
    |cumulative_kills
    |_begin_finish
    |_finish\s*\(\s*true
    |global_position\s*[+=]
    |_on_teleporter_entered
    |_on_checkpoint
    |_on_goal_reached
    |_on_body_entered
    |satchel\.add
    |completed\[
    |\.call\(\s*"_complete"
    |shift_time_left\s*=
    |stage_time_left\s*=
    |JUMP_VELOCITY
    |motor\.invulnerable
    |_route_physics
    |kill\(
    """,
    re.VERBOSE,
)
CONCESSION_RE = re.compile(
    r"""
    seed
    |finish_delay
    |change_scene_to_file
    |ChangeSceneToFile
    |has_next_level
    |unlock_next_slot
    |route_origin_physics
    |route_elapsed_seed
    |route_shift_seed
    |silent-audio
    |lock.scroll
    |offline.clock
    |--bot
    |--gb-level=
    |--seed=
    |--pl-
    |--th-run
    |--audio-log
    |--no-trim
    |--only=
    |GB_ROUTE_OUT
    |GB_ROUTE_VERSION
    |capture_dir
    |capture_mode
    |GB_CAPTURE_
    |GB_AUTOPILOT
    |GB_MUTE
    """,
    re.VERBOSE | re.IGNORECASE,
)

CONTEXT_LINES = 16


@dataclass
class Hit:
    path: str
    line: int
    kind: str
    classification: str  # grant | concession | unknown | n/a
    snippet: str
    why: str


@dataclass
class ScanResult:
    project: str
    scanned_files: int = 0
    hits: list[Hit] = field(default_factory=list)
    limits: str = (
        "Static GDScript and C# on the project.godot load graph (main scene, "
        "autoloads, res:// paths those files reach including JSON manifests, "
        "and GDScript class_name scripts they mention). Directory name is not "
        "an exclusion. C# using/class references without res:// are not "
        "followed. Cannot see grants that avoid OS.get_cmdline_* / "
        "OS.get_environment / Environment.GetEnvironmentVariable / a named "
        "evaluator autoload, a _ready auto-win whose victory scene is not in "
        "gb_levels.json, or whether L5 enters a static hit (gap G-L5-REACH; "
        "same-statement guards are a reviewer hint, not a verdict). A clean "
        "scan is not proof of a fair playthrough, and not proof the "
        "certificate was issued after a grant was removed."
    )
    inclusion: str = "load_graph"
    source_digest: str = ""

    @property
    def harness_flag_hits(self) -> list[Hit]:
        return [h for h in self.hits if h.kind == "harness_flag"]

    @property
    def auto_win_hits(self) -> list[Hit]:
        return [h for h in self.hits if h.kind == "auto_win_ready"]

    @property
    def node_probe_hits(self) -> list[Hit]:
        return [h for h in self.hits if h.kind == "node_probe"]

    @property
    def node_probe_grant_hits(self) -> list[Hit]:


        return [
            h
            for h in self.hits
            if h.kind == "node_probe" and h.classification in ("grant", "unknown")
        ]

    @property
    def grant_hits(self) -> list[Hit]:
        return [
            h
            for h in self.hits
            if h.kind == "harness_flag" and h.classification in ("grant", "unknown")
        ]

    @property
    def concession_hits(self) -> list[Hit]:
        return [
            h
            for h in self.hits
            if h.kind == "harness_flag" and h.classification == "concession"
        ]

    @property
    def blocking_hits(self) -> list[Hit]:
        return self.grant_hits + self.auto_win_hits + self.node_probe_grant_hits

    @property
    def blocks_film(self) -> bool:
        return bool(self.blocking_hits)

    def authenticity(self) -> str:
        if self.grant_hits:
            return "harness_granted"
        if self.auto_win_hits:
            return "auto_win_ready"
        if self.node_probe_grant_hits:
            return "node_probe_granted"
        if self.harness_flag_hits:
            return "harness_concessions_only"
        return "no_harness_flag_in_gameplay"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "scanned_files": self.scanned_files,
            "authenticity": self.authenticity(),
            "blocks_film": self.blocks_film,
            "harness_flag_count": len(self.harness_flag_hits),
            "grant_or_unknown_count": len(self.grant_hits),
            "concession_count": len(self.concession_hits),
            "auto_win_ready_count": len(self.auto_win_hits),
            "node_probe_count": len(self.node_probe_hits),
            "node_probe_grant_or_unknown_count": len(self.node_probe_grant_hits),
            "other_cli_or_env_count": sum(
                1 for h in self.hits if h.kind in ("other_cli", "env_read")
            ),
            "hits": [asdict(h) for h in self.hits],
            "inclusion": self.inclusion,
            "source_digest": self.source_digest,
            "limits": self.limits,
        }


def _engine_root(project: Path) -> Path:
    if (project / "project.godot").is_file():
        return project
    nested = sorted(
        p.parent for p in project.glob("*/project.godot") if p.is_file()
    )
    return nested[0] if len(nested) == 1 else project


def _clean_res(raw: str) -> str:
    path = raw.strip()
    if path.startswith("*"):
        path = path[1:]
    path = path.split("?", 1)[0].split("#", 1)[0]
    return path.rstrip(".,;)]}>")


def _res_to_path(engine: Path, res: str) -> Path | None:
    res = _clean_res(res)
    if not res.startswith("res://"):
        return None
    rel = res[6:]
    if not rel or rel.endswith("/"):
        return None
    path = (engine / rel).resolve()
    try:
        path.relative_to(engine.resolve())
    except ValueError:
        return None
    if any(part in UNREADABLE_DIR_NAMES for part in path.parts):
        return None
    return path if path.is_file() else None


def _dir_from_res(engine: Path, res_dir: str) -> Path | None:
    res_dir = _clean_res(res_dir)
    if not res_dir.startswith("res://"):
        return None
    rel = res_dir[6:].strip("/")
    if not rel:
        return None
    path = (engine / rel).resolve()
    try:
        path.relative_to(engine.resolve())
    except ValueError:
        return None
    if any(part in UNREADABLE_DIR_NAMES for part in path.parts):
        return None
    return path if path.is_dir() else None


def _followable(path: Path) -> bool:
    return path.suffix.lower() in FOLLOW_SUFFIXES


def _is_script(path: Path) -> bool:
    return path.suffix.lower() in SCRIPT_SUFFIXES


def _language(path: Path) -> str:
    return "cs" if path.suffix.lower() == ".cs" else "gd"


def _extract_res_refs(text: str) -> list[str]:
    return [_clean_res(m.group(0)) for m in RES_PATH_RE.finditer(text)]


def _extract_quoted_dirs(text: str) -> list[str]:
    return [_clean_res(m.group(1)) for m in QUOTED_DIR_RE.finditer(text)]


def _iter_dir_resources(folder: Path) -> Iterable[Path]:
    for path in folder.rglob("*"):
        if not path.is_file() or not _followable(path):
            continue
        if any(part in UNREADABLE_DIR_NAMES or part.startswith(".") for part in path.parts):
            continue
        yield path


def _seed_paths(engine: Path) -> list[Path]:
    godot = engine / "project.godot"
    if not godot.is_file():
        return []
    text = godot.read_text(encoding="utf-8", errors="replace")
    seeds: list[Path] = []
    for res in _extract_res_refs(text):
        path = _res_to_path(engine, res)
        if path is not None and _followable(path):
            seeds.append(path)
    return seeds


def _index_class_names(engine: Path) -> dict[str, Path]:


    index: dict[str, Path] = {}
    for path in engine.rglob("*.gd"):
        if any(part in UNREADABLE_DIR_NAMES for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = CLASS_NAME_RE.search(text)
        if match:
            index[match.group(1)] = path.resolve()
    return index


def _class_name_refs(text: str, index: dict[str, Path]) -> list[Path]:
    found = []
    for name, path in index.items():
        if re.search(rf"\b{re.escape(name)}\b", text):
            found.append(path)
    return found


def _iter_script_files(root: Path) -> Iterable[Path]:
    for suffix in SCRIPT_SUFFIXES:
        for path in root.rglob(f"*{suffix}"):
            yield path


def _loaded_scripts(scan_root: Path) -> Iterable[Path]:


    engine = _engine_root(scan_root)
    seeds = _seed_paths(engine)
    if not seeds:
        for path in _iter_script_files(scan_root):
            if any(part in UNREADABLE_DIR_NAMES for part in path.relative_to(scan_root).parts):
                continue
            yield path
        return

    class_index = _index_class_names(engine)
    seen: set[Path] = set()
    queue: list[Path] = list(seeds)
    while queue:
        current = queue.pop()
        current = current.resolve()
        if current in seen:
            continue
        seen.add(current)
        if _is_script(current):
            yield current
        try:
            text = current.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for res in _extract_res_refs(text):
            path = _res_to_path(engine, res)
            if path is not None and _followable(path) and path not in seen:
                queue.append(path)
        for res_dir in _extract_quoted_dirs(text):
            folder = _dir_from_res(engine, res_dir)
            if folder is None:
                continue
            for path in _iter_dir_resources(folder):
                if path not in seen:
                    queue.append(path)
        for path in _class_name_refs(text, class_index):
            if path not in seen:
                queue.append(path)


def _window(lines: list[str], index: int) -> str:
    end = min(len(lines), index + 1 + CONTEXT_LINES)
    return "\n".join(lines[index:end])


def _classify_harness_window(window: str) -> tuple[str, str]:
    grant = bool(GRANT_RE.search(window))
    concession = bool(CONCESSION_RE.search(window))
    if grant:
        return "grant", "nearby lines mutate progress, combat, placement, or victory"
    if concession:
        return (
            "concession",
            "nearby lines look like seed/timing/UI skip, not a win injection",
        )
    return "unknown", "harness flag tested; could not classify the body as grant or concession"


def _code_part(line: str, *, language: str = "gd") -> str:

    in_str = None
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\" and i + 1 < len(line):
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "'\"":
            in_str = ch
        elif language == "cs" and ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            return line[:i]
        elif language != "cs" and ch == "#":
            return line[:i]
        i += 1
    return line


def _strip_cs_block_comments(text: str) -> str:

    out: list[str] = []
    i = 0
    n = len(text)
    in_str = None
    in_block = False
    while i < n:
        ch = text[i]
        if in_block:
            if ch == "*" and i + 1 < n and text[i + 1] == "/":
                out.append("  ")
                i += 2
                in_block = False
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in "'\"":
            in_str = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            in_block = True
            out.append("  ")
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _env_keys_on_line(code: str) -> list[str]:
    return ENV_KEY_RE.findall(code)


def _is_route_env_key(key: str) -> bool:
    return key.startswith(HARNESS_ENV_ROUTE_PREFIX)


def _is_eval_set_harness_env_key(key: str) -> bool:

    if key in HARNESS_ENV_EVAL_SET_KEYS:
        return True
    return _is_route_env_key(key)


def _is_grantable_env_key(key: str) -> bool:
    if key in HARNESS_ENV_GRANTABLE:
        return True
    return any(key.startswith(prefix) for prefix in HARNESS_ENV_GRANTABLE_PREFIXES)


def _is_harness_probe_source(text: str) -> bool:


    sampling = sum(1 for marker in PROBE_SAMPLING_MARKERS if marker in text)
    census = sum(1 for marker in PROBE_CENSUS_MARKERS if marker in text)
    return sampling >= 2 and census >= 2


def _classify_asset_sampling(window: str, source: str) -> tuple[str, str]:


    classification, why = _classify_harness_window(window)
    if classification == "grant":
        return classification, why
    if ASSET_SPOOF_RE.search(window):
        return (
            "grant",
            "GB_ASSET_SAMPLING (eval asset probe sets this) gates "
            "texture/mesh/material/resource selection; that is fake asset "
            "quality aimed at the conformance gate, not a fake win",
        )
    if _is_harness_probe_source(source):
        return (
            "concession",
            "harness probe reading its own GB_ASSET_SAMPLING flag "
            "(eval path sets this during the asset observation pass); "
            "not gameplay asset selection",
        )
    if classification == "concession":
        return classification, why
    return (
        "unknown",
        "GB_ASSET_SAMPLING is set by the eval asset probe; this read is "
        "not in a probe-shaped file and the nearby body is not a grant "
        "or asset swap we recognise",
    )


def _classify_node_probe(window: str) -> tuple[str, str]:


    if GRANT_RE.search(window):
        return (
            "grant",
            "gameplay branches on the presence of an evaluator-injected "
            "autoload and the nearby lines mutate progress, combat, placement "
            "or victory",
        )
    if PROBE_INTERFACE_RE.search(window):
        return (
            "concession",
            "reads the declared analog-axis interface off the injected probe "
            "(docs/reference/ANALOG_AXES.md); the rules are the same for a human, "
            "only the input device differs",
        )
    if PRESENTATION_RE.search(window):
        return (
            "concession",
            "the branch reaches presentation-layer engine APIs only "
            "(display/audio/screenshot); gameplay rules are unchanged",
        )
    return (
        "unknown",
        "gameplay tests whether an evaluator-injected autoload is in the tree. "
        "An honest no-flag replay cannot clear this: the harness has to inject "
        "those nodes to observe and drive at all, so the branch is live in "
        "every replay",
    )


def _is_node_probe(code: str, lookback: str) -> bool:


    names = _node_probe_re(injected_autoload_names())
    if not names.search(code):
        return False
    if names.search(_strip_string_literals(code)):
        return True
    return bool(NODE_LOOKUP_RE.search(lookback))


def _hit_kind(line: str, *, language: str = "gd", lookback: str = "") -> str | None:
    code = _code_part(line, language=language)
    if any(flag in code for flag in HARNESS_FLAGS):
        return "harness_flag"
    env_keys = _env_keys_on_line(code) if ENV_RE.search(code) else []
    if any(_is_eval_set_harness_env_key(key) for key in env_keys):
        return "harness_flag"
    if _is_node_probe(code, lookback or code):
        return "node_probe"
    if CMDLINE_RE.search(code):
        return "other_cli"
    if ENV_RE.search(code):
        return "env_read"
    return None


def _promote_grantable_env(
    kind: str, line: str, window: str, *, language: str, source: str = ""
) -> tuple[str, str, str]:


    code = _code_part(line, language=language)
    keys = _env_keys_on_line(code)
    if kind == "harness_flag" and "GB_ASSET_SAMPLING" in keys:
        classification, why = _classify_asset_sampling(window, source)
        return kind, classification, why
    if kind == "node_probe":
        classification, why = _classify_node_probe(window)
        return kind, classification, why
    if kind != "env_read":
        classification, why = _classify_for_kind(kind, window)
        return kind, classification, why
    if not any(_is_grantable_env_key(key) for key in keys):
        return kind, "n/a", "process environment read in gameplay"
    classification, why = _classify_harness_window(window)
    if classification == "grant":
        return "harness_flag", classification, why
    return kind, "n/a", "process environment read in gameplay"


def _classify_for_kind(kind: str, window: str) -> tuple[str, str]:
    if kind == "harness_flag":
        return _classify_harness_window(window)
    if kind == "node_probe":
        return _classify_node_probe(window)
    if kind == "env_read":
        return "n/a", "process environment read in gameplay"
    return "n/a", "command-line read that is not --gb-route-plan"


def _joined_statement(lines: list[str], index: int, *, language: str) -> str:

    start = index
    if language != "cs":
        while start > 0:
            prev = _code_part(lines[start - 1], language=language).rstrip()
            if prev.endswith("\\"):
                start -= 1
                continue
            break
    buf: list[str] = []
    depth = 0
    end = min(len(lines), max(index + 8, start + 10))
    for i in range(start, end):
        code = _code_part(lines[i], language=language)
        buf.append(code.rstrip("\\"))
        depth += code.count("(") - code.count(")")
        stripped = code.rstrip()
        if language == "cs" and (";" in code or "{" in code):
            break
        if language != "cs" and not stripped.endswith("\\") and depth <= 0:
            break
    return " ".join(" ".join(buf).split())


def _top_level_and_parts(expr: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    depth = 0
    while i < len(expr):
        ch = expr[i]
        if ch in "([{":
            depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            buf.append(ch)
            i += 1
            continue
        if depth == 0 and expr.startswith("&&", i):
            parts.append("".join(buf).strip())
            buf = []
            i += 2
            continue
        if (
            depth == 0
            and expr.lower().startswith("and", i)
            and (i == 0 or not (expr[i - 1].isalnum() or expr[i - 1] == "_"))
            and (i + 3 >= len(expr) or not (expr[i + 3].isalnum() or expr[i + 3] == "_"))
        ):
            parts.append("".join(buf).strip())
            buf = []
            i += 3
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf).strip())
    return [p for p in parts if p]


def _condition_expr(statement: str) -> str:
    s = statement.strip().rstrip(":").rstrip("{").strip()
    match = re.match(r"(?:elif|else\s+if|if)\s+(.+)$", s, re.I | re.S)
    if match:
        return match.group(1).strip()

    match = re.match(r"[A-Za-z_][\w.]*\s*(?::=|=(?!=))\s*(.+)$", s, re.S)
    if match:
        return match.group(1).strip()
    return s


def _is_harness_conjunct(part: str) -> bool:
    if any(flag in part for flag in HARNESS_FLAGS):
        return True
    if HARNESS_ENV_ROUTE_PREFIX in part:
        return True
    if any(key in part for key in HARNESS_ENV_EVAL_SET_KEYS):
        return True
    if CMDLINE_RE.search(part) or ENV_RE.search(part):
        return True
    return False


def _same_statement_guards(statement: str) -> str:


    expr = _condition_expr(statement)
    parts = _top_level_and_parts(expr)
    if len(parts) < 2:
        return ""
    rest = [p for p in parts if not _is_harness_conjunct(p)]
    if not rest:
        return ""
    return " and ".join(rest)


def _append_guard_note(why: str, statement: str) -> str:
    guards = _same_statement_guards(statement)
    if not guards:
        return why
    return (
        f"{why}; same-statement guards (not an L5 reachability verdict, "
        f"gap G-L5-REACH): {guards}"
    )


def _file_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_digest(entries: list[tuple[str, str]]) -> str:

    blob = "".join(f"{rel}\0{digest}\n" for rel, digest in sorted(entries))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _victory_scenes(engine: Path, scan_root: Path) -> set[str]:


    scenes: set[str] = set()
    candidates = [
        engine / "gb_levels.json",
        scan_root / "gb_levels.json",
        engine.parent / "gb_levels.json",
    ]
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        payload = _load_json(path)
        if not payload:
            continue
        endings = payload.get("endings")
        if not isinstance(endings, dict):
            continue
        for key, value in endings.items():
            key_l = str(key).lower()
            if key_l in DEFEAT_ENDING_KEYS or key_l not in VICTORY_ENDING_KEYS:
                continue
            if isinstance(value, str) and value.startswith("res://"):
                scenes.add(_clean_res(value))
    return scenes


def _victory_aliases(
    loaded: list[tuple[Path, str, str]], victory: set[str]
) -> dict[str, str]:

    aliases: dict[str, str] = {}
    if not victory:
        return aliases
    for _path, _rel, text in loaded:
        cls = None
        gd_cls = CLASS_NAME_RE.search(text)
        if gd_cls:
            cls = gd_cls.group(1)
        else:
            cs_cls = CS_CLASS_RE.search(text)
            if cs_cls:
                cls = cs_cls.group(1)
        for match in CONST_PATH_RE.finditer(text):
            name, raw = match.group(1), _clean_res(match.group(2))
            if raw not in victory:
                continue
            aliases[name] = raw
            if cls:
                aliases[f"{cls}.{name}"] = raw
    return aliases


def _argument_victory_scene(
    argument: str, victory: set[str], aliases: dict[str, str]
) -> str | None:
    for lit in re.findall(r"""["'](res://[^"']+)["']""", argument):
        cleaned = _clean_res(lit)
        if cleaned in victory:
            return cleaned
    for name, path in sorted(aliases.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(name)}\b", argument):
            return path
    return None


def _mask_gd_nested_funcs(body: str) -> str:

    lines = body.splitlines(keepends=True)
    out: list[str] = []
    nested_indent: int | None = None
    for line in lines:
        stripped = line.lstrip("\t ")
        if not stripped.strip() or stripped.startswith("#"):
            out.append(line)
            continue
        indent = len(line) - len(stripped)
        if nested_indent is not None:
            if indent > nested_indent:
                kept = "\n" if line.endswith("\n") else ""
                out.append((" " * (len(line) - len(kept))) + kept)
                continue
            nested_indent = None

        if stripped.startswith("func ") or stripped.startswith("func(") or re.search(
            r"\bfunc\s*\(", stripped
        ):
            nested_indent = indent

            kept = "\n" if line.endswith("\n") else ""
            out.append((" " * (len(line) - len(kept))) + kept)
            continue
        out.append(line)
    return "".join(out)


def _mask_cs_lambdas(body: str) -> str:

    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        if body.startswith("=>", i):
            j = i + 2
            while j < n and body[j] in " \t":
                j += 1
            if j < n and body[j] == "{":
                depth = 1
                k = j + 1
                while k < n and depth:
                    if body[k] == "{":
                        depth += 1
                    elif body[k] == "}":
                        depth -= 1
                    k += 1
                chunk = body[i:k]
                out.append("".join("\n" if ch == "\n" else " " for ch in chunk))
                i = k
                continue
            k = j
            while k < n and body[k] not in ";\n":
                k += 1
            chunk = body[i:k]
            out.append("".join("\n" if ch == "\n" else " " for ch in chunk))
            i = k
            continue
        out.append(body[i])
        i += 1
    return "".join(out)


def _iter_ready_bodies(text: str, *, language: str) -> Iterable[tuple[int, str]]:

    if language == "cs":
        for match in CS_READY_SIG_RE.finditer(text):
            start = match.end()
            depth = 1
            i = start
            while i < len(text) and depth:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            yield start, text[start : i - 1 if i > start else i]
        return
    for match in READY_GD_RE.finditer(text):
        yield match.start(1), match.group(1)


def _auto_win_hits_in_text(
    rel: str,
    text: str,
    *,
    language: str,
    victory: set[str],
    aliases: dict[str, str],
) -> list[Hit]:
    if not victory:
        return []
    hits: list[Hit] = []
    for body_start, body in _iter_ready_bodies(text, language=language):
        masked = _mask_cs_lambdas(body) if language == "cs" else _mask_gd_nested_funcs(body)
        for scene_match in CHANGE_SCENE_RE.finditer(masked):
            argument = scene_match.group(1) or ""
            scene = _argument_victory_scene(argument, victory, aliases)
            if scene is None:
                continue
            abs_pos = body_start + scene_match.start()
            line_no = text[:abs_pos].count("\n") + 1
            snippet_src = text.splitlines()[line_no - 1].strip() if line_no <= len(text.splitlines()) else scene_match.group(0)
            hits.append(
                Hit(
                    path=rel,
                    line=line_no,
                    kind="auto_win_ready",
                    classification="grant",
                    snippet=snippet_src[:240],
                    why=(
                        f"_ready jumps to declared victory ending {scene} before "
                        "input; this is the S4 auto-win negative control"
                    ),
                )
            )
    return hits


def scan_project(project: Path) -> ScanResult:
    project = project.resolve()
    result = ScanResult(project=project.name)
    engine = _engine_root(project)
    loaded: list[tuple[Path, str, str]] = []
    digest_entries: list[tuple[str, str]] = []
    for path in _loaded_scripts(project):
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            rel = str(path.resolve().relative_to(project)).replace("\\", "/")
        except ValueError:
            rel = path.name
        loaded.append((path, rel, raw))
        digest_entries.append((rel, _file_digest(raw)))

    victory = _victory_scenes(engine, project)
    aliases = _victory_aliases(loaded, victory)
    probe_names = _node_probe_re(injected_autoload_names())

    for path, rel, raw in loaded:
        result.scanned_files += 1
        language = _language(path)
        text = _strip_cs_block_comments(raw) if language == "cs" else raw
        lines = text.splitlines()
        for i, line in enumerate(lines):


            lookback = ""
            if probe_names.search(line):
                lookback = "\n".join(
                    _code_part(ln, language=language)
                    for ln in lines[max(0, i - NODE_LOOKUP_LOOKBACK) : i + 1]
                )
            kind = _hit_kind(line, language=language, lookback=lookback)
            if kind is None:
                continue
            window = _window(lines, i)
            kind, classification, why = _promote_grantable_env(
                kind, line, window, language=language, source=text
            )
            if kind == "harness_flag":
                why = _append_guard_note(why, _joined_statement(lines, i, language=language))
            result.hits.append(
                Hit(
                    path=rel,
                    line=i + 1,
                    kind=kind,
                    classification=classification,
                    snippet=line.strip()[:240],
                    why=why,
                )
            )
        result.hits.extend(
            _auto_win_hits_in_text(
                rel, text, language=language, victory=victory, aliases=aliases
            )
        )
    result.source_digest = _source_digest(digest_entries)
    return result


def scan_auto_win_ready(project: Path) -> list[Hit]:


    return [h for h in scan_project(project).hits if h.kind == "auto_win_ready"]


def scan_fleet(games_root: Path, *, skip_ids: Iterable[str] = ()) -> list[ScanResult]:
    skip = set(skip_ids)
    results = []
    for child in sorted(games_root.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name in skip:
            continue
        if not (child / "project.godot").is_file() and not any(
            child.glob("*/project.godot")
        ):
            continue


        results.append(scan_project(child))
    return results


def film_refusal(scan: ScanResult, *, acknowledged_reason: str = "") -> str | None:

    if not scan.blocks_film:
        return None
    if acknowledged_reason.strip():
        return None
    blocking = scan.blocking_hits
    sites = ", ".join(f"{h.path}:{h.line}" for h in blocking[:8])
    extra = "" if len(blocking) <= 8 else f" (+{len(blocking) - 8} more)"
    notes = []
    if scan.auto_win_hits:
        notes.append("a _ready jump to a declared victory ending")
    if scan.node_probe_grant_hits:
        notes.append("a branch on an injected evaluator autoload being in the tree")
    auto = f" including {' and '.join(notes)}" if notes else ""
    return (
        "gameplay contains harness-conditional grants"
        f"{auto} "
        f"({len(blocking)} site(s): {sites}{extra}). "
        "Filming that run would produce a clip of the granted route, not of a "
        "genuine playthrough — the 2026-08-27 incident. Pass "
        "--acknowledge-harness-grants with a written reason if you still need "
        "a route-replay film, and the sidecar will say so. Removing "
        "--gb-route-plan from the recorder is not a fix; the game has to stop "
        "granting when it sees the flag."
    )


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", type=Path, help="one game directory")
    ap.add_argument("--fleet", type=Path, help="games/ root")
    ap.add_argument(
        "--skip",
        action="append",
        default=[],
        help="task id to skip (repeatable); use for trees owned by other agents",
    )
    ap.add_argument("--json", action="store_true")
    return ap


def main() -> int:
    args = parser().parse_args()
    if bool(args.project) == bool(args.fleet):
        print("harness_scan: give exactly one of --project or --fleet", flush=True)
        return 2
    if args.project:
        results = [scan_project(args.project)]
    else:
        results = scan_fleet(args.fleet, skip_ids=args.skip)
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
        return 0
    blocked = 0
    for result in results:
        mark = "BLOCK" if result.blocks_film else "ok   "
        print(
            f"{mark}  {result.project:<24} "
            f"flags={len(result.harness_flag_hits):2d}  "
            f"grants={len(result.grant_hits):2d}  "
            f"concessions={len(result.concession_hits):2d}  "
            f"auto_win={len(result.auto_win_hits):2d}  "
            f"node_probe={len(result.node_probe_grant_hits):2d}  "
            f"other={sum(1 for h in result.hits if h.kind in ('other_cli', 'env_read')):2d}  "
            f"files={result.scanned_files}"
        )
        if result.blocks_film:
            blocked += 1
            for hit in result.blocking_hits:
                print(f"         {hit.path}:{hit.line}  [{hit.classification}/{hit.kind}] {hit.snippet}")
    print(f"{blocked} of {len(results)} projects would refuse a playthrough film")
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
