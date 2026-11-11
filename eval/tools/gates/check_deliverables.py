#!/usr/bin/env python3


from __future__ import annotations

import argparse
import json
import os
import re
import hashlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
LOCKED_SCANNER = os.path.join(os.path.dirname(HERE), "scan_locked_assets.py")

_EVALSYS_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "evalsys"))
if _EVALSYS_ROOT not in sys.path:
    sys.path.insert(0, _EVALSYS_ROOT)
from evalsys.engine import hostenv
from evalsys.interface.loader import project_root


SKIP_DIRS = {
    ".godot", ".git", ".import", "__pycache__", "node_modules",
    "inputs", "reference", "staging", "_archives", "raw", "_raw", "downloads",
    "web", "build", "builds", "export", ".cache",
}

TOOL_DIRS = {"tools", "tool"}
TEST_DIRS = {"tests", "test", "bot", "harness", "bots"}
UI_DIRS = {"ui", "hud", "menus", "menu", "interface"}

EVIDENCE_SUBDIRS = ("verify", "compare", "recording", "shots", "screenshots")
AUDIO_EXT = {".wav", ".ogg", ".mp3", ".oga", ".flac"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"}
MODEL_EXT = {".glb", ".gltf", ".obj", ".fbx"}
FRAME_EXT = {".png", ".jpg", ".jpeg", ".webp"}
LOG_EXT = {".log", ".txt", ".out", ".json"}
REFERENCING_EXT = {".gd", ".tscn", ".scn", ".tres", ".res", ".json", ".cfg", ".godot"}


WORLD_ART_NODES = {
    "Sprite2D", "Sprite3D", "AnimatedSprite2D", "AnimatedSprite3D",
    "MeshInstance2D", "MeshInstance3D", "MultiMeshInstance2D", "MultiMeshInstance3D",
    "Polygon2D", "Line2D", "NinePatchRect",
    "GPUParticles2D", "GPUParticles3D", "CPUParticles2D", "CPUParticles3D",
    "CSGBox3D", "CSGSphere3D", "CSGCylinder3D", "CSGTorus3D", "CSGPolygon3D", "CSGMesh3D",
    "TileMap", "TileMapLayer", "Decal", "Label3D",
}


UI_NODES = {
    "ColorRect", "TextureRect", "Label", "RichTextLabel", "Button", "TextureButton",
    "Panel", "PanelContainer", "ProgressBar", "TextureProgressBar", "VBoxContainer",
    "HBoxContainer", "MarginContainer", "CenterContainer", "GridContainer", "Control",
    "TextureRect", "CheckBox", "HSlider", "VSlider", "ScrollContainer", "LineEdit",
}
AUDIO_NODES = {"AudioStreamPlayer", "AudioStreamPlayer2D", "AudioStreamPlayer3D"}


DOC_TARGETS = [
    ("NOTES.md", ["NOTES.md", "docs/NOTES.md"]),
    ("GDD.md", ["GDD.md", "docs/GDD.md"]),
    ("SPEC_COMPLIANCE.md", ["SPEC_COMPLIANCE.md", "docs/SPEC_COMPLIANCE.md"]),
    ("README.md", ["README.md", "docs/README.md"]),
    ("assets/ASSET_SOURCES.md", ["assets/ASSET_SOURCES.md", "ASSET_SOURCES.md", "docs/ASSET_SOURCES.md"]),
]


RENAME_HINTS = ["ASSETS.md", "assets/ASSETS.md", "ASSET_PROVENANCE.md",
                "assets/PROVENANCE.json", "PROVENANCE.json"]

MIN_CONTENT_LINES = 10
MIN_CONTENT_CHARS = 400
LONG_FILE_LINES = 800


EVIDENCE_IMAGES_WARN = 120
EVIDENCE_BYTES_WARN = 25 * 1024 * 1024
EVIDENCE_IMAGES_FAIL = 300
EVIDENCE_BYTES_FAIL = 100 * 1024 * 1024


EVIDENCE_IMPORT_FAIL = 50

EVIDENCE_DIRS = ("compare", "verify", "recording", "shots", "screenshots")

CANONICAL_FRAME_DIRS = ("compare",)

_ASSIGN = re.compile(r"^\s*(?:var\s+|const\s+)?([A-Za-z_]\w*)\s*(?::=|:\s*\w+\s*=|=)\s*(.+?)\s*$")
_ADD_CHILD = re.compile(r"add_child\s*\(")
_NEW_CALL = re.compile(r"\b([A-Z]\w*)\s*\.\s*new\s*\(")


def read_text(path: str) -> str | None:

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def iter_files(root: str, exts: set[str] | None = None, include_all_dirs: bool = False):
    for dirpath, dirnames, filenames in os.walk(root):
        if not include_all_dirs:
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
        else:
            dirnames[:] = [d for d in dirnames if d.lower() not in {".godot", ".git", "__pycache__"}]
        for name in sorted(filenames):
            if exts is None or os.path.splitext(name)[1].lower() in exts:
                yield os.path.join(dirpath, name)


def rel_parts(path: str, root: str) -> list[str]:
    return os.path.relpath(path, root).replace("\\", "/").lower().split("/")


def content_weight(text: str) -> tuple[int, int]:

    lines = 0
    chars = 0
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if set(s) <= set("-=*_ "):
            continue
        if set(s) <= set("|-: "):
            continue
        if s.startswith("<!--"):
            continue
        lines += 1
        chars += len(s)
    return lines, chars


def check_docs(root: str, skipped: list) -> dict:


    found, missing, stubs, notes = {}, [], [], []

    for canonical, candidates in DOC_TARGETS:
        hit = None
        for cand in candidates:
            if os.path.isfile(os.path.join(root, cand)):
                hit = cand
                break
        if hit is None:
            missing.append(canonical)
            continue

        text = read_text(os.path.join(root, hit))
        if text is None:
            skipped.append({"path": os.path.join(root, hit), "error": "unreadable during scan"})
            notes.append(f"{hit} unreadable during scan, not judged")
            found[canonical] = hit
            continue

        n_lines, n_chars = content_weight(text)
        found[canonical] = hit
        if n_lines < MIN_CONTENT_LINES or n_chars < MIN_CONTENT_CHARS:
            stubs.append(f"{hit} ({n_lines} content lines, {n_chars} chars)")

    for alt in RENAME_HINTS:
        if os.path.isfile(os.path.join(root, alt)):
            notes.append(f"{alt} exists — rename/fold into assets/ASSET_SOURCES.md")

    status = "pass" if not missing and not stubs else "fail"
    return {
        "id": "docs", "status": status, "gating": True,
        "found": found, "missing": missing, "stubs": stubs, "notes": notes,
        "detail": "; ".join(
            ([f"missing: {', '.join(missing)}"] if missing else [])
            + ([f"stub: {', '.join(stubs)}"] if stubs else [])
            + notes
        ) or "all five present with content",
    }


def classify_expression(expr: str, in_ui: bool) -> tuple[str, str]:

    if ".instantiate(" in expr:
        return "scene_instance", expr
    m = _NEW_CALL.search(expr)
    if m:
        cls = m.group(1)
        if cls in AUDIO_NODES:
            return "audio_player", cls
        if cls in UI_NODES:
            return ("ui_widget" if in_ui else "ui_widget"), cls
        if cls in WORLD_ART_NODES:
            return ("ui_widget" if in_ui and cls in {"NinePatchRect"} else
                    "from_scratch_world_art"), cls
        return "structural_new", cls
    if "duplicate(" in expr:
        return "scene_instance", expr
    return "unresolved", expr


def check_add_child(root: str, skipped: list) -> dict:
    buckets = {
        "scene_instance": 0, "build_tool": 0, "test_scaffold": 0,
        "ui_widget": 0, "audio_player": 0, "structural_new": 0,
        "from_scratch_world_art": 0, "unresolved": 0,
    }
    offenders: list[str] = []
    unresolved_sites: list[str] = []

    for path in iter_files(root, {".gd"}):
        text = read_text(path)
        if text is None:
            skipped.append({"path": path, "error": "unreadable during scan"})
            continue
        parts = rel_parts(path, root)
        dirs = set(parts[:-1])
        rel = "/".join(parts)

        located = None
        if dirs & TOOL_DIRS:
            located = "build_tool"
        elif dirs & TEST_DIRS or parts[-1].startswith("test_") or parts[-1].endswith("_test.gd"):
            located = "test_scaffold"
        in_ui = bool(dirs & UI_DIRS)

        env: dict[str, str] = {}
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0] if not raw.lstrip().startswith("#") else ""
            if not line.strip():
                continue

            m = _ASSIGN.match(line)
            if m and "add_child" not in m.group(2):
                env[m.group(1)] = m.group(2)

            for hit in _ADD_CHILD.finditer(line):
                if located:
                    buckets[located] += 1
                    continue
                arg = line[hit.end():]
                depth, out = 1, []
                for ch in arg:
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    out.append(ch)
                arg = "".join(out).split(",")[0].strip()

                expr = arg
                if re.fullmatch(r"[A-Za-z_]\w*", arg) and arg in env:
                    expr = env[arg]
                kind, what = classify_expression(expr, in_ui)
                buckets[kind] += 1
                if kind == "from_scratch_world_art":
                    offenders.append(f"{rel}:{lineno}  {what}.new() -> add_child")
                elif kind == "unresolved" and len(unresolved_sites) < 12:
                    unresolved_sites.append(f"{rel}:{lineno}  add_child({arg[:48]})")

    total = sum(buckets.values())
    status = "fail" if buckets["from_scratch_world_art"] else "pass"
    return {
        "id": "add_child", "status": status, "gating": True,
        "total": total, "buckets": buckets,
        "offenders": offenders[:40], "unresolved_sites": unresolved_sites,
        "detail": (
            f"{buckets['from_scratch_world_art']} world-art nodes built from scratch"
            if offenders else
            f"{total} calls, none building world art from scratch"
        ),
    }


L3_ACTIONS = ["gb_left", "gb_right", "gb_up", "gb_down", "gb_jump", "gb_action", "gb_pause", "gb_reset"]


_REPORTER_STRONG = re.compile(
    r"dev_dump|nearest_hazard|dev_probe|bridge_dir|bridge_hold|--bot=|dump_state|"
    r"write_state|state\.json|observation")

_AREA_SOURCED = re.compile(
    r"\b(area|hitbox|hurtbox|killbox|kill_area|damage_area|collider|collision|shape|hazard_area)"
    r"\w*\s*(?:\.|\"\]\s*\.|\'\]\s*\.)\s*global_position", re.I)
_ROOT_SOURCED = re.compile(r"\b(?:self|node|dev|device|hazard|saw|body|n)\w*\s*\.\s*(?:global_)?position\b", re.I)
_MOTION = re.compile(
    r"AnimationPlayer|create_tween|\btween\b|move_and_slide|move_and_collide|"
    r"PathFollow[23]D|\bprogress\b|position\s*\+=|position\.[xy]\s*=|\bsin\s*\(|\bcos\s*\(|"
    r"\bvelocity\b|angular_velocity|\brotate\b")
_DAMAGING = re.compile(r"gb_hazard|gb_enemy|take_damage|\bdamage\b|\bkill\b|\bhurt\b|\bdie\b|_on_.*body_entered")

B3_FORMS = ("moving_hazard", "simulation_state")


def load_bridge_declaration(root: str) -> dict:


    p = os.path.join(root, "gb_bridge.json")
    if os.path.isfile(p):
        text = read_text(p)
        if text:
            try:
                data = json.loads(text)
                if isinstance(data, dict) and data.get("b3_form") in B3_FORMS:
                    return {"b3_form": data["b3_form"], "source": "gb_bridge.json",
                            "notes": data.get("notes", "")}
            except ValueError:
                pass
    for doc in ("SPEC_COMPLIANCE.md", "docs/SPEC_COMPLIANCE.md"):
        text = read_text(os.path.join(root, doc))
        if not text:
            continue
        m = re.search(r"b3_form\s*[:=]\s*`?(\w+)`?", text, re.I)
        if m and m.group(1) in B3_FORMS:
            return {"b3_form": m.group(1), "source": doc, "notes": ""}
    return {"b3_form": None, "source": None, "notes": ""}


def check_bridge(root: str, skipped: list, gating: bool) -> dict:


    have_player = False
    have_actions: list[str] = []
    have_outcome: list[str] = []
    have_bridge_entry = False
    certify_entry = False
    reporters: list[str] = []
    area_sourced_in: list[str] = []
    root_sourced_in: list[str] = []
    phase_field = False
    moving_damage = False


    project_text = read_text(os.path.join(engine_root(root), "project.godot")) or ""
    for act in L3_ACTIONS:
        if re.search(rf"^{act}=", project_text, re.M):
            have_actions.append(act)

    for path in iter_files(root, {".gd", ".tscn", ".cfg", ".json"}):
        text = read_text(path)
        if text is None:
            skipped.append({"path": path, "error": "unreadable during scan"})
            continue
        rel = os.path.relpath(path, root)

        if "gb_player" in text:
            have_player = True
        low = text.lower()
        if "bridge" in low and ("--bot=" in text or "bridge_dir" in text or "bridge-dir" in text):
            have_bridge_entry = True
        if "certify" in low and ("run_certify" in text or "--bot=certify" in text):
            certify_entry = True
        for token in ("goal_reached", "gb_goal", "done.json", "victory", "game_over", "level_complete"):
            if token in text and token not in have_outcome:
                have_outcome.append(token)
        if _DAMAGING.search(text) and _MOTION.search(text):
            moving_damage = True

        is_reporter = (
            path.endswith(".gd")
            and not (set(rel_parts(path, root)[:-1]) & (TOOL_DIRS | {"levels", "world"}))
            and (_REPORTER_STRONG.search(text)
                 or ("gb_player" in text and "global_position" in text))
        )
        if is_reporter:
            reporters.append(rel)
            if '"phase"' in text or "'phase'" in text or "phase" in text:
                phase_field = True
            if _AREA_SOURCED.search(text):
                area_sourced_in.append(rel)
            elif _ROOT_SOURCED.search(text):
                root_sourced_in.append(rel)

    decl = load_bridge_declaration(root)
    form = decl["b3_form"] or ("moving_hazard" if moving_damage else "simulation_state")

    b3_problems, b3_notes = [], []
    b3_verified = True
    if not reporters:
        b3_problems.append("no bridge/dev_dump reporter found, so no entity state is observable")
    elif form == "moving_hazard":
        if not moving_damage:
            b3_notes.append("no moving damage source detected; declare b3_form to be explicit")
        if not phase_field:
            b3_problems.append("reporter emits no phase field for cycling devices")
        if area_sourced_in:
            b3_notes.append(f"position read from the killing area in {area_sourced_in[0]}")
        elif root_sourced_in:
            b3_problems.append(
                f"reporter reads node position ({root_sourced_in[0]}) not the killing area's "
                "global_position — this is the exact shape of G11, where saws reported frozen "
                "coordinates while their Area2D travelled")
        else:
            b3_notes.append("could not tell what the reporter reads positions from")
    else:


        # there is none the result is UNVERIFIED — recorded as not established, never as a pass.
        if not phase_field:
            b3_verified = False
            b3_notes.append("simulation_state form declared but no advancing field could be "
                            "found in what the reporter emits — B3 is UNVERIFIED here, which is "
                            "not the same as passing")
        b3_notes.append("simulation_state form: even with a field present, static reading cannot "
                        "show the reported state is the state the simulation mutates rather than "
                        "a parallel copy. Only a runtime negative control settles that.")

    missing = []
    if not b3_verified:
        pass
    if not have_player:
        missing.append("no gb_player group")
    if not have_actions and not have_bridge_entry:
        missing.append("no action interface (no gb_* InputMap actions, no bridge entry point)")
    if not have_outcome:
        missing.append("win/loss not readable from outside")
    missing += [f"B3: {p}" for p in b3_problems]

    return {
        "id": "bridge", "status": "pass" if not missing else "fail", "gating": gating,
        "gb_player": have_player, "l3_actions": have_actions, "bridge_entry": have_bridge_entry,
        "outcome_tokens": have_outcome,
        "levels_json": os.path.isfile(os.path.join(engine_root(root), "gb_levels.json")),
        "b3": {
            "form": form, "declared": decl["b3_form"], "declared_in": decl["source"],
            "reporters": reporters[:6], "phase_field": phase_field,
            "moving_damage_source": moving_damage,
            "position_from_killing_area": bool(area_sourced_in),
            "problems": b3_problems, "notes": b3_notes,
            "static_screen_only": True,
            "certify_with": "two dev_dumps 37 frames apart; phase AND reported point must both "
                            "differ for every travelling kill zone",
        },
        "optional_b1_b2_b4": {"certify_entry_point": certify_entry, "gating": False},
        "missing": missing,
        "detail": "; ".join(missing) or "level-1 surface present, B3 static screen clean",
    }


def check_visual(root: str) -> dict:


    frames_by_dir: dict[str, int] = {}
    for sub in EVIDENCE_DIRS:
        d = os.path.join(root, sub)
        if os.path.isdir(d):
            n = sum(1 for _ in iter_files(d, FRAME_EXT, include_all_dirs=True))
            if n:
                frames_by_dir[sub] = n
    frames = sum(frames_by_dir.values())

    logs, import_log, review_doc = 0, False, False
    vdir = os.path.join(root, "verify")
    if os.path.isdir(vdir):
        for p in iter_files(vdir, LOG_EXT | {".md"}, include_all_dirs=True):
            name = os.path.basename(p).lower()
            try:
                if os.path.getsize(p) == 0:
                    continue
            except OSError:
                continue
            if name.endswith(".md"):
                if "review" in name or "visual" in name:
                    review_doc = True
                continue
            logs += 1
            if "import" in name or "godot" in name or "headless" in name:
                import_log = True


    off_canonical = frames > 0 and not (set(frames_by_dir) & set(CANONICAL_FRAME_DIRS))
    problems, hints = [], []
    if frames == 0:
        problems.append("no frame images anywhere in " + "/, ".join(EVIDENCE_DIRS) + "/")
    elif off_canonical:
        hints.append(
            f"frame evidence exists ({', '.join(f'{n} in {d}/' for d, n in sorted(frames_by_dir.items()))}) "
            "but not under compare/ — evidence counts, path should be aligned"
        )
    if not os.path.isdir(vdir):
        problems.append("no verify/")
    elif logs == 0:
        problems.append("verify/ has no non-empty raw command output")
    elif not import_log:
        problems.append("verify/ has raw output but no headless-import log")

    status = "fail" if problems else ("warn" if hints else "pass")
    where = ", ".join(f"{n} in {d}/" for d, n in sorted(frames_by_dir.items()))
    return {
        "id": "visual", "status": status, "gating": True,
        "frames": frames, "frames_by_dir": frames_by_dir, "off_canonical_path": off_canonical,
        "verify_logs": logs, "import_log": import_log, "visual_review_doc": review_doc,
        "problems": problems, "hints": hints,
        "detail": "; ".join(problems + hints) or
                  f"{where}; {logs} raw logs in verify/" + ("; VISUAL_REVIEW doc" if review_doc else ""),
    }


def check_ledger(root: str, skipped: list) -> dict:


    ledger_path = None
    for cand in ("assets/ASSET_SOURCES.md", "ASSET_SOURCES.md", "docs/ASSET_SOURCES.md"):
        if os.path.isfile(os.path.join(root, cand)):
            ledger_path = cand
            break
    if ledger_path is None:
        return {"id": "ledger", "status": "skip", "gating": False,
                "detail": "no ASSET_SOURCES.md to check coverage against"}

    text = read_text(os.path.join(root, ledger_path))
    if text is None:
        skipped.append({"path": ledger_path, "error": "unreadable during scan"})
        return {"id": "ledger", "status": "skip", "gating": False,
                "detail": "ledger unreadable during scan"}

    assets = []
    for path in iter_files(root, IMAGE_EXT | AUDIO_EXT | MODEL_EXT):
        parts = rel_parts(path, root)
        if set(parts[:-1]) & set(EVIDENCE_DIRS):
            continue
        assets.append(path)

    uncovered = []
    for p in assets:
        if os.path.basename(p) not in text:
            uncovered.append(os.path.relpath(p, root))


    stale = []
    for m in re.finditer(
            r"(?<![\w!/.\-])(?:res://|assets/)[\w\-./]+\.(?:png|jpg|jpeg|webp|ogg|wav|mp3|glb|gltf|obj)",
            text):
        claimed = m.group(0).replace("res://", "")
        if not os.path.isfile(os.path.join(root, claimed)) and claimed not in stale:
            stale.append(claimed)


    rows_named, rows_full, rows_none, rows_truncated = 0, 0, [], []
    row_path = re.compile(r"(?:res://|assets/)[\w\-./]+\.\w{2,5}")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not row_path.search(stripped):
            continue
        rows_named += 1
        if re.search(r"\b[0-9a-f]{64}\b", stripped):
            rows_full += 1
        elif re.search(r"\b[0-9a-f]{7,63}\b", stripped):
            rows_truncated.append(stripped[:80])
        else:
            rows_none.append(stripped[:80])
    has_hashes = rows_named > 0 and rows_full >= max(3, rows_named // 2)
    truncated_count = len(rows_truncated)
    nohash_count = len(rows_none)


    verified = mismatched = unresolved = 0
    mismatch_sample = []
    shipped_set = {os.path.relpath(a, root).replace(os.sep, "/") for a in assets}
    shipped_by_base: dict[str, list[str]] = {}
    for a in shipped_set:
        shipped_by_base.setdefault(os.path.basename(a), []).append(a)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue


        # the batch. canopy_dash keeps a pack-level table whose only path is the license copy,


        paths = [c.strip().replace("res://", "")
                 for c in re.findall(r"`([^`]+)`", stripped)
                 if re.search(r"\.\w{2,5}$", c.strip())]
        paths += [m.group(0).replace("res://", "")
                  for m in re.finditer(r"(?:res://|assets/)[\w\-./]+\.\w{2,5}", stripped)]


        resolved = []
        for q in paths:
            if "/" in q:
                resolved.append(q)
                continue
            hits = shipped_by_base.get(q, [])
            if len(hits) == 1:
                resolved.append(hits[0])
        paths = resolved
        digests = set(re.findall(r"\b[0-9a-f]{64}\b", stripped))
        if not paths or not digests:
            continue
        present = [q for q in paths if os.path.isfile(os.path.join(root, q))]

        # table per pack whose digest is the archive's and whose only in-tree path is the license


        if re.search(r"`[^`]+\.(?:zip|7z|tar\.gz|rar)`", stripped) and \
                all(q.startswith("assets/licenses/") or q.endswith(".txt") for q in present):
            continue
        if not present:
            unresolved += 1
            continue
        hit = None
        for rel in present:
            try:
                h = hashlib.sha256()
                with open(os.path.join(root, rel), "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
            except OSError:
                skipped.append({"path": rel, "error": "unreadable during scan"})
                continue
            if h.hexdigest() in digests:
                hit = rel
                break
        if hit:
            verified += 1
            continue


        subjects = [q for q in present if q in shipped_set]
        if len(subjects) == 1:
            mismatched += 1
            if len(mismatch_sample) < 5:
                mismatch_sample.append(subjects[0])
        else:
            unresolved += 1
    covered = len(assets) - len(uncovered)
    ratio = covered / len(assets) if assets else 1.0

    problems, hints = [], []
    if assets and ratio < 0.80:
        problems.append(f"only {covered}/{len(assets)} shipped assets appear in {ledger_path} "
                        f"({ratio*100:.0f}% covered)")
    elif uncovered:
        hints.append(f"{len(uncovered)} shipped assets not named in the ledger")
    if stale:
        hints.append(f"{len(stale)} ledger entries name files that are not in the tree")
    if mismatched:
        problems.append(f"{mismatched} ledger digest(s) do not match the shipped bytes: "
                        + ", ".join(mismatch_sample[:4]))
    if nohash_count and rows_named:
        hints.append(f"{nohash_count} of {rows_named} ledger rows carry no digest at all")
    if truncated_count:
        hints.append(f"{truncated_count} of {rows_named} ledger rows carry a TRUNCATED digest — "
                     "enough to identify a file, not enough to recompute against it")
    if not has_hashes and not truncated_count:
        hints.append("no per-file SHA-256; the ledger can be believed but not verified")
    elif not has_hashes:
        hints.append(f"only {rows_full} of {rows_named} rows carry a full 64-hex digest")

    return {
        "id": "ledger", "status": "fail" if problems else ("warn" if hints else "pass"),
        "gating": True, "ledger": ledger_path, "assets": len(assets), "covered": covered,
        "coverage": round(ratio, 3), "uncovered_sample": uncovered[:15],
        "stale_sample": stale[:10], "per_file_hashes": has_hashes,
        "rows_named": rows_named, "rows_full_hash": rows_full,
        "rows_truncated_hash": truncated_count, "rows_no_hash": nohash_count,
        "hashes_verified": verified, "hashes_mismatched": mismatched,
        "hashes_unresolved": unresolved,
        "detail": "; ".join(problems + hints) or
                  f"{covered}/{len(assets)} assets covered, {verified} digests recomputed and "
                  f"matched against the shipped bytes",
    }


def check_notes_authenticity(root: str) -> dict:


    p = os.path.join(root, "NOTES.md")
    if not os.path.isfile(p):
        return {"id": "notes", "status": "skip", "gating": False, "detail": "no NOTES.md"}
    try:
        proc = subprocess.run(
            ["git", "log", "--follow", "--format=%ad", "--date=short", "--", "NOTES.md"],
            cwd=root, capture_output=True, text=True, timeout=30)
    except Exception:
        return {"id": "notes", "status": "skip", "gating": False,
                "detail": "git unavailable, cannot check history"}
    if proc.returncode != 0:
        return {"id": "notes", "status": "skip", "gating": False,
                "detail": "not a git repository, cannot check history"}

    dates = [d for d in proc.stdout.split() if d]
    distinct = sorted(set(dates))
    hints = []
    if not dates:
        hints.append("NOTES.md is untracked — no history to corroborate it")
    elif len(distinct) == 1:
        hints.append(f"written in a single commit on {distinct[0]} — reads as a retrofit, "
                     "not a log kept during the build")
    return {
        "id": "notes", "status": "warn" if hints else "pass", "gating": False,
        "commits": len(dates), "distinct_days": len(distinct),
        "first": distinct[0] if distinct else None, "last": distinct[-1] if distinct else None,
        "detail": "; ".join(hints) or
                  f"{len(dates)} commits across {len(distinct)} days "
                  f"({distinct[0]} to {distinct[-1]})",
    }


def check_gb_levels(root: str) -> dict:


    res_root = engine_root(root)
    p = os.path.join(res_root, "gb_levels.json")
    if not os.path.isfile(p):
        where = res_root if res_root == os.path.realpath(root) else f"{root} or {res_root}"
        return {"id": "gb_levels", "status": "warn", "gating": False,
                "searched": where,
                "detail": f"no gb_levels.json under {where} (L2.5 not attempted)"}
    text = read_text(p)
    if text is None:
        return {"id": "gb_levels", "status": "fail", "gating": True,
                "detail": f"{p} exists but could not be read — a present-and-unreadable "
                          "interface manifest is a broken deliverable, not an unattempted one"}
    try:
        data = json.loads(text)
    except ValueError as exc:
        return {"id": "gb_levels", "status": "fail", "gating": True,
                "detail": f"gb_levels.json is not valid JSON: {exc}"}

    entries = data.get("levels", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return {"id": "gb_levels", "status": "fail", "gating": True,
                "detail": "gb_levels.json has no list of levels"}

    missing, ok = [], 0
    for e in entries:
        scene = None
        if isinstance(e, dict):
            for key in ("scene", "path", "file", "scene_path", "res"):
                if isinstance(e.get(key), str):
                    scene = e[key]
                    break
        elif isinstance(e, str):
            scene = e
        if not scene:
            missing.append("(entry with no scene path)")
            continue
        rel = scene.replace("res://", "")
        if os.path.isfile(os.path.join(res_root, rel)):
            ok += 1
        else:
            missing.append(scene)

    return {
        "id": "gb_levels", "status": "fail" if missing else "pass", "gating": True,
        "levels": len(entries), "resolved": ok, "unresolved": missing[:10],
        "detail": (f"{len(missing)} of {len(entries)} declared levels do not resolve to a "
                   f"scene file: {', '.join(missing[:4])}") if missing else
                  f"all {ok} declared levels resolve to real scenes",
    }


OFFICIAL_HARNESS = os.environ.get("GB_OFFICIAL_HARNESS", str(Path(__file__).resolve().parents[2] / "harness"))
if not os.path.isdir(OFFICIAL_HARNESS):
    _repo_harness = os.environ.get(
        "GB_HARNESS_DIR", os.path.abspath(os.path.join(HERE, "..", "..", "harness")))
    if os.path.isdir(_repo_harness):
        OFFICIAL_HARNESS = _repo_harness


def _md5(path: str) -> str | None:
    try:
        h = hashlib.md5()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def official_hashes() -> dict[str, str]:

    out: dict[str, str] = {}
    if not os.path.isdir(OFFICIAL_HARNESS):
        return out
    for name in os.listdir(OFFICIAL_HARNESS):
        if name.endswith(".gd"):
            h = _md5(os.path.join(OFFICIAL_HARNESS, name))
            if h:
                out[h] = name
    return out


def check_vendored(root: str, official: dict[str, str], skipped: list) -> dict:


    exact, drifted = [], []
    by_name: dict[str, list[str]] = {}
    for h, name in official.items():
        by_name.setdefault(name, []).append(h)

    for path in iter_files(root, {".gd"}):
        base = os.path.basename(path)
        if base not in by_name:
            continue
        h = _md5(path)
        if h is None:
            skipped.append({"path": path, "error": "unreadable during scan"})
            continue
        rel = os.path.relpath(path, root)
        if h in official:
            exact.append(rel)
        else:
            drifted.append(rel)

    return {
        "id": "vendored", "status": "warn" if drifted else "pass", "gating": False,
        "exact_copies": exact, "drifted_copies": drifted,
        "detail": (f"{len(drifted)} vendored harness file(s) modified locally: "
                   + ", ".join(drifted)) if drifted else
                  (f"{len(exact)} byte-identical harness file(s) vendored" if exact
                   else "no vendored harness code"),
    }


PREFAB_DIRS = {"actors", "actor", "entities", "entity", "hazards", "enemies", "enemy",
               "props", "devices", "objects", "pickups", "items", "obstacles"}
LEVEL_DIRS = {"levels", "level", "rooms", "room", "scenes", "world", "maps", "stages", "acts"}


EXT_SCENE_RE = re.compile(r'\[ext_resource type="PackedScene" path="res://([^"]+)" id="([^"]+)"\]')
INSTANCE_RE = re.compile(r'instance=ExtResource\("([^"]+)"\)')
GDD_SCENE_RE = re.compile(r"([A-Za-z0-9_]+\.tscn)")


def engine_root(root: str) -> str:


    return str(project_root(root))


CODE_INSTANCED = ("fx", "ui", "systems", "screens", "menus", "hud")


def check_placement(root: str, skipped: list) -> dict:


    res_root = engine_root(root)

    prefabs, hosts = [], []
    for path in iter_files(root, {".tscn"}):
        if set(rel_parts(path, root)[:-1]) & PREFAB_DIRS:
            prefabs.append(path)
        else:
            hosts.append(path)
    if not prefabs:
        return {"id": "placement", "status": "skip", "gating": False,
                "detail": "no prefab-shaped scene directories to check"}


    counts: dict[str, int] = {os.path.relpath(p, root): 0 for p in prefabs}
    engine_key = {os.path.relpath(p, res_root).replace(os.sep, "/"): os.path.relpath(p, root)
                  for p in prefabs}
    for host in hosts + prefabs:
        text = read_text(host)
        if text is None:
            skipped.append({"path": host, "error": "unreadable during scan"})
            continue
        host_rel = os.path.relpath(host, root)
        id_to_path = {i: p for p, i in EXT_SCENE_RE.findall(text)}
        for rid in INSTANCE_RE.findall(text):
            target = id_to_path.get(rid)
            key = engine_key.get(target) if target else None
            if key and key != host_rel:
                counts[key] += 1
    levels = hosts

    placeable = {p: n for p, n in counts.items()
                 if not set(p.split(os.sep)) & set(CODE_INSTANCED)}


    code_refs: set[str] = set()
    for path in iter_files(root, {".gd", ".json", ".tres", ".cfg", ".txt"}):
        text = read_text(path)
        if text is None:
            skipped.append({"path": path, "error": "unreadable during scan"})
            continue
        for rel in placeable:
            engine_rel = os.path.relpath(os.path.join(root, rel),
                                         res_root).replace(os.sep, "/")
            if rel in text or engine_rel in text or os.path.basename(rel) in text:
                code_refs.add(rel)

    never_placed = sorted(p for p, n in placeable.items() if n == 0 and p not in code_refs)
    code_placed = sorted(p for p, n in placeable.items() if n == 0 and p in code_refs)


    gdd = read_text(os.path.join(root, "GDD.md")) or ""


    content_scenes = {os.path.basename(p) for p in counts} | {os.path.basename(l) for l in levels}
    every_scene = {os.path.basename(p) for p in iter_files(root, {".tscn"})}
    inventory = ""
    for block in re.findall(r"```[^\n]*\n(.*?)```", gdd, re.S):
        if len(set(GDD_SCENE_RE.findall(block))) >= 3:
            inventory += block + "\n"
    declared = set(GDD_SCENE_RE.findall(inventory))
    declared_missing = sorted(declared - every_scene)
    undeclared = sorted(content_scenes - declared) if declared else []

    problems, hints = [], []
    if never_placed:
        problems.append(f"{len(never_placed)} actor scene(s) are referenced nowhere at all — "
                        f"no level instance, no preload, no data entry: "
                        f"{', '.join(never_placed[:6])}")
    if declared_missing:
        problems.append(f"GDD names {len(declared_missing)} scene(s) that are not on disk: "
                        + ", ".join(declared_missing[:5]))
    if undeclared:


        hints.append(f"{len(undeclared)} scene(s) on disk are not named in the GDD: "
                     + ", ".join(undeclared[:6]))

    return {
        "id": "placement", "status": "fail" if problems else ("warn" if hints else "pass"),
        "gating": True, "prefabs": len(prefabs), "levels": len(levels),
        "instances": placeable, "never_placed": never_placed[:20],
        "placed_from_code": code_placed[:20],
        "gdd_inventory_found": bool(declared),
        "gdd_declared_missing": declared_missing[:12], "undeclared_on_disk": undeclared[:20],
        "detail": "; ".join(problems + hints) or
                  (f"all {len(placeable)} placeable actor scenes are reachable "
                   f"({len(placeable) - len(code_placed)} instanced in another scene, "
                   f"{len(code_placed)} instanced from code)"
                   + ("" if declared else "; no fenced GDD inventory, reverse check skipped")),
    }


def check_gb_cardinality(root: str, skipped: list) -> dict:


    per_file: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}

    for path in iter_files(root, {".tscn"}):
        text = read_text(path)
        if text is None:
            skipped.append({"path": path, "error": "unreadable during scan"})
            continue
        rel = os.path.relpath(path, root)
        counts: dict[str, int] = {}
        for line in text.splitlines():
            if not line.startswith("[node "):
                continue
            m = re.search(r'groups\s*=\s*\[([^\]]*)\]', line)
            if not m:
                continue
            for g in re.findall(r'"(gb_[a-z_]+)"', m.group(1)):
                counts[g] = counts.get(g, 0) + 1
        if counts:
            per_file[rel] = counts
            for g, n in counts.items():
                totals[g] = max(totals.get(g, 0), n)


    script_tags: dict[str, list[str]] = {}
    for path in iter_files(root, {".gd"}):
        text = read_text(path)
        if text is None:
            continue
        rel = os.path.relpath(path, root)
        for m in re.finditer(r'add_to_group\s*\(\s*&?["\']( ?gb_[a-z_]+)["\']', text):
            g = m.group(1).strip()
            before = text[max(0, m.start() - 160):m.start()]
            if "is_in_group" in before:
                continue
            script_tags.setdefault(g, []).append(rel)

    problems, hints = [], []
    doubled = {f: c["gb_player"] for f, c in per_file.items() if c.get("gb_player", 0) > 1}
    if doubled:
        problems.append("gb_player appears on more than one node in a single scene: "
                        + ", ".join(f"{f} ({n})" for f, n in list(doubled.items())[:4]))
    if not totals.get("gb_player") and "gb_player" not in script_tags:
        problems.append("no gb_player tag anywhere")
    if not totals.get("gb_goal") and not totals.get("gb_collectible"):
        hints.append("no gb_goal and no gb_collectible: nothing marks the objective")

    other = {k: v for k, v in totals.items() if k not in ("gb_player",)}
    return {
        "id": "gb_tags", "status": "fail" if problems else ("warn" if hints else "pass"),
        "gating": True, "max_per_scene": totals, "per_file": per_file,
        "unguarded_script_tags": script_tags,
        "other_tags_counted_not_judged": other,
        "detail": "; ".join(problems + hints) or
                  ("gb_player exactly 1 per scene; "
                   + ", ".join(f"{k}={v}" for k, v in sorted(other.items())[:6])),
    }


def check_b3_negative_control(root: str) -> dict:


    hits = []
    for sub in ("verify", "compare", "docs"):
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        for dirpath, _dirnames, filenames in os.walk(d):
            for name in filenames:
                low = name.lower()
                if ("negative" in low and ("b3" in low or "control" in low)) or \
                   ("b3" in low and "control" in low):
                    p = os.path.join(dirpath, name)
                    try:
                        if os.path.getsize(p) > 0:
                            hits.append(os.path.relpath(p, root))
                    except OSError:
                        pass
    return {
        "id": "b3_negative", "status": "pass" if hits else "warn", "gating": False,
        "evidence": hits,
        "detail": ("negative control archived: " + ", ".join(hits[:3])) if hits else
                  "no archived B3 negative control — the position check has never been shown "
                  "capable of failing",
    }
TARGET_FRAME_SIZE = (1280, 720)


def _frame_pixels(path: str):


    try:
        from PIL import Image
        import numpy as np
        with Image.open(path) as im:
            return np.asarray(im.convert("RGB"), dtype=np.uint8)
    except Exception:
        return None


SCREENSHOT_MIN_WIDTH = 1000
SCREENSHOT_ASPECT = (1.5, 1.85)


def _is_screen_capture(w: int, h: int) -> bool:
    return w >= SCREENSHOT_MIN_WIDTH and SCREENSHOT_ASPECT[0] <= w / max(h, 1) <= SCREENSHOT_ASPECT[1]


NEAR_PCT = 3.0
NEAR_TOL = 8
CURRENT_SET = "compare"


def _variable_region(a, b) -> int:


    import numpy as np

    def nonflat(img):
        g = img.astype(np.int16).sum(axis=2)
        gx = np.zeros_like(g)
        gy = np.zeros_like(g)
        gx[:, 1:] = np.abs(g[:, 1:] - g[:, :-1])
        gy[1:, :] = np.abs(g[1:, :] - g[:-1, :])
        return (gx + gy) > 24

    mask = nonflat(a) | nonflat(b)
    if not mask.any():
        return int(a.shape[0] * a.shape[1])
    ys, xs = np.nonzero(mask)
    return int((ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1))


def check_frame_uniqueness(root: str, skipped: list) -> dict:


    import numpy as np

    current: list[str] = []
    historical: list[str] = []
    for sub in EVIDENCE_SUBDIRS:
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        bucket = current if sub == CURRENT_SET else historical
        for dirpath, _dirnames, filenames in os.walk(d):
            for name in sorted(filenames):
                if os.path.splitext(name)[1].lower() in FRAME_EXT:
                    bucket.append(os.path.join(dirpath, name))

    paths = current + historical
    if not paths:
        return {"id": "frames", "status": "skip", "gating": False,
                "detail": "no evidence frames to measure"}


    by_content: dict[str, list[str]] = {}
    stats: dict[str, tuple] = {}
    sizes: dict[tuple, int] = {}
    for path in paths:
        arr = _frame_pixels(path)
        if arr is None:
            skipped.append({"path": path, "error": "undecodable during scan"})
            continue
        rel = os.path.relpath(path, root)
        key = hashlib.sha256(arr.tobytes()).hexdigest()
        by_content.setdefault(key, []).append(rel)
        stats[rel] = (arr.shape[:2], arr.reshape(-1, 3).mean(axis=0))
        sizes[(arr.shape[1], arr.shape[0])] = sizes.get((arr.shape[1], arr.shape[0]), 0) + 1

    current_rel = {os.path.relpath(p, root) for p in current}
    total = sum(len(v) for v in by_content.values())
    if total == 0:
        return {"id": "frames", "status": "skip", "gating": False,
                "detail": "no decodable evidence frames"}
    unique = len(by_content)


    def _claim(rel: str) -> str:
        return re.sub(r"[_-]?\d+$", "", os.path.splitext(os.path.basename(rel))[0])

    all_dupes = [v for v in by_content.values() if len(v) > 1]
    dupes = sorted((v for v in all_dupes if len({_claim(x) for x in v}) > 1),
                   key=len, reverse=True)
    seq_dupes = [v for v in all_dupes if len({_claim(x) for x in v}) == 1]


    reps = sorted(sorted(v)[0] for v in by_content.values()
                  if sorted(v)[0] in current_rel)
    near: list[dict] = []
    cache: dict[str, object] = {}

    def pixels(rel: str):
        if rel not in cache:
            if len(cache) > 24:
                cache.clear()
            cache[rel] = _frame_pixels(os.path.join(root, rel))
        return cache[rel]

    for i, a in enumerate(reps):
        for b in reps[i + 1:]:
            sa, ma = stats[a]
            sb, mb = stats[b]
            if sa != sb:
                continue
            if float(np.abs(ma - mb).max()) > 16.0:
                continue
            pa, pb = pixels(a), pixels(b)
            if pa is None or pb is None:
                continue
            d = np.abs(pa.astype(np.int16) - pb.astype(np.int16)).sum(axis=2)
            differing = int((d > NEAR_TOL).sum())
            region = _variable_region(pa, pb)
            pct = 100.0 * differing / max(region, 1)
            if pct < NEAR_PCT:
                near.append({"a": a, "b": b, "pct": round(pct, 3),
                             "frame_pct": round(100.0 * differing / d.size, 3),
                             "region_share": round(100.0 * region / d.size, 1)})
    near.sort(key=lambda x: x["pct"])

    off_current: dict[str, int] = {}
    off_historical: dict[str, int] = {}
    working_material = 0
    for rel, (shape, _m) in stats.items():
        wh = f"{shape[1]}x{shape[0]}"
        if (shape[1], shape[0]) == TARGET_FRAME_SIZE:
            continue
        if not _is_screen_capture(shape[1], shape[0]):
            working_material += 1
            continue
        bucket = off_current if rel in current_rel else off_historical
        bucket[wh] = bucket.get(wh, 0) + 1

    problems, hints = [], []
    biggest = len(dupes[0]) if dupes else 0
    if biggest >= 5:
        problems.append(f"{biggest} evidence frames are byte-identical to each other "
                        f"({', '.join(os.path.basename(x) for x in dupes[0][:4])}…) — the "
                        f"effective frame count is {unique}, not {total}")
    elif dupes:
        hints.append(f"{sum(len(g) - 1 for g in dupes)} frames duplicate another frame under a "
                     f"different name; {unique} unique of {total}")
    if seq_dupes:
        hints.append(f"{sum(len(g) - 1 for g in seq_dupes)} redundant frames inside numbered "
                     f"sequences (a still moment, not a false claim)")
    if near:
        hints.append(f"{len(near)} pair(s) differ in under {NEAR_PCT}% of their variable region "
                     f"at full resolution and need justifying, closest "
                     f"{os.path.basename(near[0]['a'])} vs {os.path.basename(near[0]['b'])} "
                     f"at {near[0]['pct']}%")
    if off_current:
        problems.append(f"{CURRENT_SET}/ gameplay frames not at 1280x720: "
                        + ", ".join(f"{k} x{v}" for k, v in sorted(off_current.items())[:5]))
    if off_historical:


        marked = any(
            os.path.isfile(os.path.join(root, sub, marker))
            for sub in EVIDENCE_SUBDIRS if sub != CURRENT_SET
            for marker in ("README.md", "HISTORY.md", "NOTES.md")
        ) or any("history" in p.lower().split(os.sep) for p in stats if p not in current_rel)
        note = ("historical frames outside the delivery set, kept at their original size: "
                + ", ".join(f"{k} x{v}" for k, v in sorted(off_historical.items())[:5]))
        if not marked:
            note += (" — nothing in those directories identifies them as historical; add a "
                     "README.md saying so, or move them under a history/ path")
        hints.append(note)

    return {
        "id": "frames", "status": "fail" if problems else ("warn" if hints else "pass"),
        "gating": True, "total": total, "unique": unique,
        "largest_duplicate_group": biggest,
        "duplicate_groups": [g[:6] for g in dupes[:4]],
        "sequence_duplicate_groups": len(seq_dupes),
        "near_duplicates": near[:12], "near_pairs": len(near),
        "downsampled": False,
        "current_set_frames": len(current_rel),
        "working_material_exempt": working_material,
        "historical_frames": total - len(current_rel),
        "off_size_current": off_current, "off_size_historical": off_historical,
        "sizes": {f"{w}x{h}": n for (w, h), n in sizes.items()},
        "detail": "; ".join(problems + hints) or
                  f"{unique} unique frames of {total}, no near-duplicates under {NEAR_PCT}% of "
                  f"the variable region at full resolution, delivery set all at 1280x720",
    }


ABSENCE_TEST = re.compile(
    r"(\.is_empty\s*\(\s*\)|\w\s*==\s*null|\bnot\s+is_instance_valid\s*\("
    r"|get_child_count\s*\(\s*\)\s*==\s*0|\.size\s*\(\s*\)\s*==\s*0)")
SUCCESS_OUTCOME = re.compile(
    r'"(?:[a-z_]*(?:cleared|complete|victory|won|win|finished|solved|success)[a-z_]*)"', re.I)
OUTCOME_NOISE = re.compile(
    r"(check\s*\(|push_error|push_warning|printerr|print\s*\(|assert\s*\(|%[sd]|refus)", re.I)
STATE_REPORTING = re.compile(
    r"(bridge|agent|observ|report|game_state|world_state|run_state|level_root|results"
    r"|scene_flow|router|stops)", re.I)


def check_absence_outcome(root: str, skipped: list) -> dict:


    sites = []
    for path in iter_files(root, {".gd"}):
        rel = os.path.relpath(path, root)
        if set(rel_parts(path, root)[:-1]) & set(EVIDENCE_DIRS):
            continue
        if not STATE_REPORTING.search(rel):
            continue
        text = read_text(path)
        if text is None:
            skipped.append({"path": path, "error": "unreadable during scan"})
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            code = line.split("#")[0]
            if not re.match(r"\s*(if|elif)\b", code) or not ABSENCE_TEST.search(code):
                continue
            window = "\n".join(x.split("#")[0] for x in lines[i:i + 5])
            m = SUCCESS_OUTCOME.search(window)
            if not m or OUTCOME_NOISE.search(window):
                continue
            sites.append({"file": rel, "line": i + 1,
                          "test": code.strip()[:88], "outcome": m.group(0)})

    return {
        "id": "absence_outcome",
        "status": "warn" if sites else "pass",
        "gating": False, "advisory": True,
        "sites": sites[:12], "site_count": len(sites),
        "detail": (f"{len(sites)} terminal outcome(s) decided by an absence — confirm each has "
                   f"exactly one cause: "
                   + ", ".join(f"{x['file']}:{x['line']}->{x['outcome']}" for x in sites[:3]))
        if sites else "no success outcome is decided purely by something being absent",
    }


def check_gdignore(root: str) -> dict:


    dirs, missing, imports = [], [], 0
    for sub in EVIDENCE_SUBDIRS:
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        dirs.append(sub)
        if not os.path.isfile(os.path.join(d, ".gdignore")):
            missing.append(sub)
        for dirpath, _dirnames, filenames in os.walk(d):
            imports += sum(1 for f in filenames if f.endswith(".import"))

    if not dirs:
        return {"id": "gdignore", "status": "skip", "gating": False,
                "detail": "no evidence directories"}
    status = "pass"
    if imports:
        status = "fail"
    elif missing:
        status = "warn"
    return {
        "id": "gdignore", "status": status, "gating": True,
        "evidence_dirs": dirs, "missing_gdignore": missing, "import_sidecars": imports,
        "detail": (f"{imports} .import sidecars generated under {', '.join(missing or dirs)} — "
                   f"add .gdignore" if imports else
                   (f"no .gdignore in {', '.join(missing)} (0 sidecars so far)" if missing
                    else f".gdignore present in all {len(dirs)} evidence dirs, 0 sidecars")),
    }


CLASS_NAME_RE = re.compile(r"^\s*class_name\s+([A-Za-z_]\w*)", re.M)


def check_class_name(root: str, skipped: list) -> dict:


    declared: dict[str, str] = {}
    for path in iter_files(root, {".gd"}):
        text = read_text(path)
        if text is None:
            skipped.append({"path": path, "error": "unreadable during scan"})
            continue
        for m in CLASS_NAME_RE.finditer(text):
            declared[m.group(1)] = os.path.relpath(path, root)

    if not declared:
        return {"id": "class_name", "status": "pass", "gating": False,
                "detail": "no class_name declarations, nothing to resolve from the cache"}

    cache_path = os.path.join(engine_root(root), ".godot", "global_script_class_cache.cfg")
    cache_text = read_text(cache_path)
    if cache_text is None:
        return {"id": "class_name", "status": "warn", "gating": False,
                "declared": declared,
                "detail": f"{len(declared)} class_name declarations and no import cache on "
                          "disk — a cold start cannot resolve any of them until an import runs"}

    cached = set(re.findall(r'"class"\s*:\s*&?"(\w+)"', cache_text))
    cached |= set(re.findall(r'class\s*=\s*&?"(\w+)"', cache_text))
    stale = sorted(n for n in declared if n not in cached)


    bare_users: dict[str, list[str]] = {}
    for name in stale:
        for path in iter_files(root, {".gd"}):
            rel = os.path.relpath(path, root)
            if rel == declared[name]:
                continue
            text = read_text(path)
            if text is None or not re.search(rf"\b{name}\b", text):
                continue
            if re.search(rf'preload\s*\(\s*"[^"]+"\s*\)[^\n]*\b{name}\b', text) or \
               re.search(rf'\b{name}\s*(?::|=)\s*preload', text):
                continue
            bare_users.setdefault(name, []).append(rel)

    return {
        "id": "class_name", "status": "fail" if stale else "pass", "gating": True,
        "declared_count": len(declared), "cached_count": len(cached),
        "not_in_cache": stale, "bare_consumers": bare_users,
        "detail": (f"{len(stale)} class_name(s) missing from the import cache, so a cold start "
                   f"cannot resolve them: " + ", ".join(f"{n} ({declared[n]})" for n in stale[:4]))
                  if stale else
                  f"all {len(declared)} class_name declarations are present in the import cache",
    }


def check_evidence_footprint(root: str) -> dict:


    images, total_bytes, imports = 0, 0, 0
    biggest: dict[str, list] = {}
    for sub in EVIDENCE_DIRS:
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        n_img, n_bytes, n_imp = 0, 0, 0
        for dirpath, dirnames, filenames in os.walk(d):
            dirnames[:] = [x for x in dirnames if x not in {".git", "__pycache__"}]
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                p = os.path.join(dirpath, name)
                if ext == ".import":
                    n_imp += 1
                elif ext in FRAME_EXT:
                    n_img += 1
                    try:
                        n_bytes += os.path.getsize(p)
                    except OSError:
                        pass
        if n_img or n_imp:
            biggest[sub] = [n_img, round(n_bytes / 1048576, 1), n_imp]
        images += n_img
        total_bytes += n_bytes
        imports += n_imp

    mb = total_bytes / 1048576
    problems, hints = [], []
    if imports >= EVIDENCE_IMPORT_FAIL:
        problems.append(f"{imports} .import sidecars generated for evidence images — "
                        "these ride into the export and slow every import")
    elif imports:
        hints.append(f"{imports} .import sidecars under evidence dirs")
    if images >= EVIDENCE_IMAGES_FAIL or total_bytes >= EVIDENCE_BYTES_FAIL:
        problems.append(f"{images} evidence images totalling {mb:.0f} MB inside the project — "
                        "capture to /tmp and keep only cited frames")
    elif images >= EVIDENCE_IMAGES_WARN or total_bytes >= EVIDENCE_BYTES_WARN:
        hints.append(f"{images} evidence images totalling {mb:.0f} MB inside the project")

    status = "fail" if problems else ("warn" if hints else "pass")
    return {
        "id": "evidence_size", "status": status, "gating": True,
        "images": images, "megabytes": round(mb, 1), "import_sidecars": imports,
        "by_dir": biggest, "problems": problems, "hints": hints,
        "detail": "; ".join(problems + hints) or
                  f"{images} evidence images, {mb:.1f} MB, {imports} .import sidecars",
    }


def check_long_files(root: str, skipped: list, official: dict[str, str]) -> dict:


    long_files, exempt = [], []
    for path in iter_files(root, {".gd"}):
        text = read_text(path)
        if text is None:
            skipped.append({"path": path, "error": "unreadable during scan"})
            continue
        n = text.count("\n") + 1
        if n <= LONG_FILE_LINES:
            continue
        rel = os.path.relpath(path, root)
        parts = rel_parts(path, root)
        base = parts[-1]
        reason = None
        if _md5(path) in official:
            reason = "vendored, byte-identical to the official harness"
        elif "bridge" in base or "gb_probe" in base or (set(parts[:-1]) & {"bot", "harness"}):
            reason = "bridge/harness implementation"
        elif set(parts[:-1]) & TOOL_DIRS:
            reason = "build-time tool"
        if reason:
            exempt.append({"path": rel, "lines": n, "reason": reason})
        else:
            long_files.append({"path": rel, "lines": n})
    long_files.sort(key=lambda r: -r["lines"])
    exempt.sort(key=lambda r: -r["lines"])
    return {
        "id": "longfiles", "status": "warn" if long_files else "pass", "gating": False,
        "files": long_files, "exempt": exempt,
        "detail": ", ".join(f"{f['path']} ({f['lines']})" for f in long_files[:5]) or
                  (f"no over-length hand-written script "
                   f"({len(exempt)} exempt: vendored/bridge/build-tool)" if exempt else
                   f"no script over {LONG_FILE_LINES} lines"),
    }


def check_locked(root: str, timeout: int) -> dict:


    if not os.path.isfile(LOCKED_SCANNER):
        return {"id": "locked", "status": "skip", "gating": False,
                "detail": f"scanner not found at {LOCKED_SCANNER}"}

    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    tmp.close()
    try:
        try:
            proc = subprocess.run(
                [sys.executable, LOCKED_SCANNER, root, "--quiet", "--jsonl", tmp.name],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"id": "locked", "status": "skip", "gating": False,
                    "detail": f"scan exceeded {timeout}s, skipped"}
        except Exception as exc:
            return {"id": "locked", "status": "skip", "gating": False,
                    "detail": f"scanner failed: {type(exc).__name__}: {exc}"}

        if proc.returncode not in (0, 1):
            return {"id": "locked", "status": "skip", "gating": False,
                    "returncode": proc.returncode,
                    "detail": f"scanner exited {proc.returncode}: {proc.stderr.strip()[:200]}"}

        hits = []
        try:
            with open(tmp.name, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        hits.append(json.loads(line))
        except Exception as exc:

            return {"id": "locked", "status": "skip", "gating": False,
                    "detail": f"scan output unreadable: {type(exc).__name__}: {exc}"}
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    locked = [h for h in hits if h.get("tier") == "LOCKED"]
    suspect = [h for h in hits if h.get("tier") == "SUSPECT"]
    watermarked = [h for h in locked if h.get("basis") == "watermark"]
    signature = [h for h in locked if h.get("basis") != "watermark"]

    ledger_text = ""
    ledger_path = None
    for cand in ("assets/ASSET_SOURCES.md", "ASSET_SOURCES.md", "docs/ASSET_SOURCES.md",
                 "PROVENANCE.json", "assets/PROVENANCE.json"):
        p = os.path.join(root, cand)
        if os.path.isfile(p):
            ledger_path = ledger_path or cand
            ledger_text += read_text(p) or ""

    resolved, unresolved = [], []
    for h in signature:
        digest = None
        try:
            with open(h["path"], "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
        except Exception:
            pass
        if digest and digest in ledger_text:
            h["resolved_by"] = digest
            resolved.append(h)
        else:
            h["sha256"] = digest
            unresolved.append(h)

    if watermarked:
        detail = (f"{len(watermarked)} file(s) carry actual Premium-Version watermark pixels — "
                  f"provenance cannot excuse these: "
                  + ", ".join(os.path.relpath(h["path"], root) for h in watermarked[:4]))
        status = "fail"
    elif unresolved:
        if ledger_path is None:
            detail = (f"{len(unresolved)} locked-signature hit(s) and NO ledger to check them "
                      f"against — the defect is the missing provenance: "
                      + ", ".join(os.path.relpath(h["path"], root) for h in unresolved[:4]))
        else:
            detail = (f"{len(unresolved)} locked-signature hit(s) with no matching SHA-256 in "
                      f"{ledger_path} — add the digest or explain the file: "
                      + ", ".join(os.path.relpath(h["path"], root) for h in unresolved[:4]))
        status = "fail"
    elif resolved:
        detail = (f"no watermarked art; {len(resolved)} locked-signature hit(s) all cleared by "
                  f"matching SHA-256 in {ledger_path} ({len(suspect)} suspect, advisory)")
        status = "pass"
    else:
        detail = (f"no locked-art signature ({len(suspect)} suspect, advisory)"
                  if suspect else "no locked-art signature")
        status = "pass"

    return {
        "id": "locked", "status": status, "gating": True,
        "returncode": proc.returncode,
        "hits": len(hits), "locked": len(locked), "suspect": len(suspect),
        "watermarked": [h["path"] for h in watermarked],
        "signature_resolved": [h["path"] for h in resolved],
        "signature_unresolved": [h["path"] for h in unresolved],
        "detail": detail,
    }


def check_audio(root: str, skipped: list) -> dict:
    audio = [p for p in iter_files(root, AUDIO_EXT)]
    if not audio:
        return {"id": "audio", "status": "fail", "gating": True, "files": 0, "referenced": 0,
                "detail": "no audio files in the shipped tree"}

    corpus = []
    for path in iter_files(root, REFERENCING_EXT):
        if os.path.splitext(path)[1].lower() == ".json" and "verify" in rel_parts(path, root):
            continue
        text = read_text(path)
        if text is None:
            skipped.append({"path": path, "error": "unreadable during scan"})
            continue
        corpus.append(text)
    blob = "\n".join(corpus)

    referenced, orphans = 0, []
    for p in audio:
        base = os.path.basename(p)
        if base in blob:
            referenced += 1
        else:
            orphans.append(os.path.relpath(p, root))

    status = "pass" if referenced > 0 else "fail"
    return {
        "id": "audio", "status": status, "gating": True,
        "files": len(audio), "referenced": referenced, "orphans": orphans[:20],
        "detail": (f"{referenced}/{len(audio)} audio files referenced from a scene, script or data file"
                   if referenced else
                   f"{len(audio)} audio files present but NONE referenced — sitting in a directory is not use"),
    }


def check_project(root: str, args, official: dict[str, str]) -> dict:
    skipped: list = []
    checks = [
        check_docs(root, skipped),
        check_ledger(root, skipped),
        check_notes_authenticity(root),
        check_gb_levels(root),
        check_add_child(root, skipped),
        check_bridge(root, skipped, gating=not args.no_gate_bridge),
        check_visual(root),
        check_evidence_footprint(root),
        check_long_files(root, skipped, official),
        check_vendored(root, official, skipped),
        check_placement(root, skipped),
        check_gb_cardinality(root, skipped),
        check_b3_negative_control(root),
        check_gdignore(root),
        check_frame_uniqueness(root, skipped),
        check_absence_outcome(root, skipped),
        check_class_name(root, skipped),
        check_locked(root, args.locked_timeout) if not args.skip_locked else
        {"id": "locked", "status": "skip", "gating": False, "detail": "--skip-locked"},
        check_audio(root, skipped),
    ]
    failures = [c["id"] for c in checks if c["gating"] and c["status"] == "fail"]
    return {
        "project": os.path.basename(os.path.normpath(root)),
        "path": root,
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "has_project_godot": os.path.isfile(os.path.join(engine_root(root), "project.godot")),
        "verdict": "FAIL" if failures else "PASS",
        "failing": failures,
        "checks": {c["id"]: c for c in checks},
        "skipped_files": skipped,
    }


SYMBOL = {"pass": "ok", "fail": "FAIL", "warn": "warn", "skip": "--"}
REPORT_IDS = ["docs", "ledger", "placement", "add_child", "bridge", "gb_tags", "gb_levels",
              "class_name", "visual", "frames", "absence_outcome", "gdignore", "evidence_size", "audio", "locked",
              "b3_negative", "vendored", "notes", "longfiles"]


def print_report(results: list[dict]) -> None:
    ids = REPORT_IDS
    head = f"{'project':<34}{'verdict':<9}" + "".join(f"{i:<15}" for i in ids)
    print(head)
    print("-" * len(head))
    for r in results:
        row = f"{r['project']:<34}{r['verdict']:<9}"
        for i in ids:
            row += f"{SYMBOL[r['checks'][i]['status']]:<15}"
        print(row)

    print("\nwhat each failing project still needs")
    print("-" * 74)
    for r in results:
        if r["verdict"] == "PASS" and not any(
            c["status"] in ("fail", "warn") for c in r["checks"].values()
        ):
            continue
        print(f"\n{r['project']}  [{r['verdict']}]")
        for i in ids:
            c = r["checks"][i]
            if c["status"] in ("fail", "warn"):
                mark = "FAIL" if c["status"] == "fail" else "warn"
                gate = "" if c["gating"] else " (advisory)"
                print(f"  {mark:<5}{i:<15}{gate} {c['detail']}")

    allskip = [s for r in results for s in r["skipped_files"]]
    if allskip:
        print(f"\nskipped {len(allskip)} unreadable files (concurrent writes, not retried):")
        for s in allskip[:15]:
            print(f"  {s['path']}  {s['error']}")
        if len(allskip) > 15:
            print(f"  ... and {len(allskip) - 15} more")


def main() -> int:
    hostenv.configure_stdio()
    ap = argparse.ArgumentParser(
        description="Deliverable gate for shipped Godot game projects.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Passing this gate means the paperwork and structure are in order. "
               "It says nothing about whether the game looks right or plays well.",
    )
    ap.add_argument("roots", nargs="*", help="project directories")
    ap.add_argument("--batch", action="append", default=[],
                    help="treat each child directory of this path as a project")
    ap.add_argument("--jsonl", metavar="FILE", help="one JSON object per project")
    ap.add_argument("--markdown", metavar="FILE", help="write a status report")
    ap.add_argument("--no-gate-bridge", action="store_true",
                    help="report the bridge check without letting it affect the exit code")
    ap.add_argument("--skip-locked", action="store_true", help="skip the locked-art image scan")
    ap.add_argument("--locked-timeout", type=int, default=600,
                    help="seconds before abandoning the locked-art scan (default 600)")
    ap.add_argument("--quiet", action="store_true", help="suppress the table")
    args = ap.parse_args()

    roots = list(args.roots)
    for batch in args.batch:
        if not os.path.isdir(batch):
            sys.stderr.write(f"warning: no such batch directory: {batch}\n")
            continue
        for name in sorted(os.listdir(batch)):
            p = os.path.join(batch, name)
            if os.path.isdir(p) and not name.startswith((".", "_")):
                roots.append(p)
    if not roots:
        ap.error("give at least one project directory or --batch path")

    official = official_hashes()
    results = []
    for root in roots:
        if not os.path.isdir(root):
            sys.stderr.write(f"warning: no such project: {root}\n")
            continue
        results.append(check_project(root, args, official))

    if args.jsonl:
        with open(args.jsonl, "w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r) + "\n")
    if args.markdown:
        write_markdown(results, args.markdown)
    if not args.quiet:
        print_report(results)

    return 1 if any(r["verdict"] == "FAIL" for r in results) else 0


def write_markdown(results: list[dict], path: str) -> None:
    ids = REPORT_IDS
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    n_fail = sum(1 for r in results if r["verdict"] == "FAIL")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# Deliverable gate status\n\n")
        fh.write(f"Generated by `tools/gates/check_deliverables.py` at **{when}**. "
                 f"{len(results) - n_fail}/{len(results)} projects pass.\n\n")
        fh.write("Projects are built concurrently; this is a snapshot, not a standing fact.\n\n")
        fh.write("| project | verdict | " + " | ".join(ids) + " |\n")
        fh.write("|---|---|" + "---|" * len(ids) + "\n")
        for r in results:
            cells = " | ".join(SYMBOL[r["checks"][i]["status"]] for i in ids)
            fh.write(f"| {r['project']} | {r['verdict']} | {cells} |\n")
        fh.write("\n## What each project still needs\n")
        for r in results:
            gaps = [(i, r["checks"][i]) for i in ids if r["checks"][i]["status"] in ("fail", "warn")]
            if not gaps:
                fh.write(f"\n### {r['project']} — clean\n")
                continue
            fh.write(f"\n### {r['project']} — {r['verdict']}\n\n")
            for i, c in gaps:
                mark = "**FAIL**" if c["status"] == "fail" else "warn"
                gate = "" if c["gating"] else " _(advisory)_"
                fh.write(f"- {mark} `{i}`{gate}: {c['detail']}\n")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
