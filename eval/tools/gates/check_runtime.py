#!/usr/bin/env python3


from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:
    sys.stderr.write(f"need numpy and Pillow: {exc}\n")
    sys.exit(2)

Image.MAX_IMAGE_PIXELS = None

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS_DIR = os.environ.get(
    "GB_HARNESS_DIR", os.path.abspath(os.path.join(HERE, "..", "..", "harness"))
)
PROBE_SRC = os.path.join(HARNESS_DIR, "gb_runtime_probe.gd")
_EVALSYS_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "evalsys"))
if _EVALSYS_ROOT not in sys.path:
    sys.path.insert(0, _EVALSYS_ROOT)
from evalsys.engine import hostenv
from evalsys.engine.import_retry import configure_scratch_import, is_retryable_import_crash


POSIX_SCRATCH_VOLUME = hostenv.POSIX_SCRATCH_VOLUME


def ascii_safe_scratch_root(name: str) -> str:


    return hostenv.scratch_root(name)


SCRATCH_ROOT = ascii_safe_scratch_root("gb_runtime_scratch")


GODOT = hostenv.find_godot()


EXCLUDE = ["inputs", "reference", "staging", "_archives", ".git", "web", "builds", "export",
           ".godot"]


EVIDENCE_DIRS = {"compare", "verify", "recording", "shots", "screenshots"}
HEAVY_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".avi", ".mp4", ".mov", ".gif"}

BOOT_FRAMES = 300
BOOT_TIMEOUT = 240
FRAME_COUNT = 120
FRAME_TIMEOUT = 300


INPUT_TIMEOUT = 900


LAUNCHER_RC = {-2, 127}


def x_launcher(width: int = 1280, height: int = 720) -> list[str]:


    if sys.platform.startswith("linux"):
        if shutil.which("xvfb-run") is None:
            raise EnvironmentError(
                "X mode needs xvfb-run on Linux and it is not installed; "
                "`apt install xvfb` or run the gate on a host with a display"
            )
        return ["xvfb-run", "-a", "-s", f"-screen 0 {width}x{height}x24"]
    return []


FATAL_PATTERNS = [
    (re.compile(r"SCRIPT ERROR", re.I), "script error"),
    (re.compile(r"Parse Error", re.I), "parse error"),
    (re.compile(r"Failed to load script", re.I), "script failed to load"),
    (re.compile(r"Attempt to call .* on a null instance", re.I), "null instance call"),
    (re.compile(r"Invalid (call|access|get index|set index|operands)", re.I), "invalid operation"),
    (re.compile(r"Nonexistent (function|signal)", re.I), "nonexistent member"),
    (re.compile(r"Can't open file|Cannot open file|No loader found|Failed loading resource", re.I),
     "resource load failure"),
    (re.compile(r"Condition \".*\" is true\. (Returning|Breaking|Continuing)", re.I), "engine assertion"),
    (re.compile(r"Segmentation fault|Aborted|handle_crash", re.I), "crash"),


    (re.compile(r"Lambda capture at index \d+ was freed", re.I), "freed lambda capture"),
]


UNCLASSIFIED_ERROR = (re.compile(r"^\s*ERROR:", re.I), "engine error (unclassified)")

WARN_PATTERNS = [
    (re.compile(r"^WARNING:", re.I), "warning"),
    (re.compile(r"Started the engine as `root`", re.I), "running as root"),
    (re.compile(r"shader|ETC2|VRAM compress", re.I), "shader/import warning"),
    (re.compile(r"is deprecated", re.I), "deprecation"),


    (re.compile(r"resources still in use at exit", re.I), "resources live at exit"),
    (re.compile(r"Texture with GL ID of \d+: leaked", re.I), "texture leaked at exit"),
    (re.compile(r"RID allocations? of type .* were leaked at exit", re.I), "RID leaked at exit"),
    (re.compile(r"ObjectDB instances leaked at exit", re.I), "objects leaked at exit"),
]

IGNORE_PATTERNS = [
    re.compile(r"Godot Engine v", re.I),
    re.compile(r"^\s*at: ", re.I),
    re.compile(r"GODOT_SILENCE_ROOT_WARNING", re.I),
    re.compile(r"Xlib|xvfb|dri3|libGL|MESA|glx", re.I),
    re.compile(r"CPU time:|GPU time:|Done recording|frames at .* FPS|^-+$", re.I),
    re.compile(r"audio.*(dummy|Dummy)|PulseAudio|ALSA", re.I),
    re.compile(r"^\s*$"),
]


BLANK_COLOURS = 3
BLANK_DOMINANT_SHARE = 0.985
NEAR_BLANK_COLOURS = 24
NEAR_BLANK_SHARE = 0.93

MIN_EDGE_ENERGY = 0.4


PLACEHOLDER_MAX_COLOURS = 3
PLACEHOLDER_MIN_PIXELS = 256
PLACEHOLDER_SKIP_DIRS = {"ui", "hud", "font", "fonts", "menus", "particles", "gradients", "noise"}


def log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def make_scratch(root: str, name: str) -> str | None:


    dst = os.path.join(SCRATCH_ROOT, f"{name}.{os.getpid()}")
    drop_scratch(dst)
    os.makedirs(SCRATCH_ROOT, exist_ok=True)
    root_real = os.path.realpath(root)

    def ignore(dirpath: str, names: list[str]) -> set[str]:
        drop = {n for n in names if n in EXCLUDE}
        rel = os.path.relpath(os.path.realpath(dirpath), root_real)
        if set(rel.split(os.sep)) & EVIDENCE_DIRS:
            drop |= {n for n in names
                     if os.path.splitext(n)[1].lower() in HEAVY_EXT
                     or n.endswith(".png.import")}
        return drop

    try:


        shutil.copytree(root, dst, ignore=ignore, **hostenv.copytree_kwargs())
        configure_scratch_import(dst)
    except Exception as exc:
        log(f"  scratch copy failed: {type(exc).__name__}: {exc}")
        return None
    return dst


def drop_scratch(path: str) -> None:

    real = os.path.realpath(path)


    if not hostenv.is_within(real, SCRATCH_ROOT):
        log(f"  refusing to remove {real}: outside the scratch root")
        return
    if os.path.isdir(real):
        shutil.rmtree(real, ignore_errors=True)


def count_declared_actions(scratch: str) -> int:


    path = os.path.join(scratch, "project.godot")
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return 12
    section = re.search(r"^\[input\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if not section:
        return 12
    return len(set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{", section.group(1), re.M))) or 12


def register_probe(scratch: str) -> bool:

    try:
        shutil.copy2(PROBE_SRC, os.path.join(scratch, "gb_runtime_probe.gd"))
        pg = os.path.join(scratch, "project.godot")
        with open(pg, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        line = 'GBRuntimeProbe="*res://gb_runtime_probe.gd"\n'
        if "[autoload]" in text:
            text = text.replace("[autoload]\n", "[autoload]\n\n" + line, 1)
        else:
            text = text.rstrip("\n") + "\n\n[autoload]\n\n" + line


        with open(pg, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return True
    except Exception as exc:
        log(f"  probe injection failed: {type(exc).__name__}: {exc}")
        return False


def run(cmd: list[str], cwd: str, timeout: int) -> tuple[int, str, str]:


    return hostenv.run_captured(cmd, cwd, timeout)


def godot_path_arg(path: str) -> str:

    return hostenv.godot_arg_path(path)


def classify_log(text: str) -> tuple[list[str], list[str]]:


    fatal, warn = [], []
    catch_pat, catch_label = UNCLASSIFIED_ERROR
    for raw in text.splitlines():
        line = raw.strip()
        if any(p.search(line) for p in IGNORE_PATTERNS):
            continue
        hit = next((label for pat, label in FATAL_PATTERNS if pat.search(line)), None)
        if hit:
            fatal.append(f"[{hit}] {line[:200]}")
            continue
        if any(pat.search(line) for pat, _ in WARN_PATTERNS):
            warn.append(line[:200])
            continue
        if catch_pat.search(line):
            fatal.append(f"[{catch_label}] {line[:200]}")
    return fatal, warn


def check_boot(scratch: str) -> dict:
    rc, out, err = run([GODOT, "--headless", "--quit-after", str(BOOT_FRAMES)],
                       scratch, BOOT_TIMEOUT)
    fatal, warn = classify_log(out + "\n" + err)
    problems = []
    if rc == -9:
        problems.append(f"hung: no exit within {BOOT_TIMEOUT}s")
    elif rc == -2:
        problems.append("godot binary not found")
    elif rc < 0:
        problems.append(f"killed by signal {-rc}")
    elif rc != 0:


        problems.append(hostenv.describe_returncode(rc))
    if fatal:
        problems.append(f"{len(fatal)} fatal log lines")

    return {
        "id": "boot", "status": "fail" if problems else ("warn" if warn else "pass"),
        "gating": True, "returncode": rc, "frames": BOOT_FRAMES,
        "fatal": fatal[:20], "fatal_count": len(fatal),
        "warnings": warn[:8], "warning_count": len(warn),
        "detail": "; ".join(problems) or
                  (f"ran {BOOT_FRAMES} frames clean, {len(warn)} warnings" if warn
                   else f"ran {BOOT_FRAMES} frames clean"),
    }


def measure_frame(path: str) -> dict:
    with Image.open(path) as im:
        arr = np.asarray(im.convert("RGB"))
    flat = arr.reshape(-1, 3)
    uniq, counts = np.unique(flat, axis=0, return_counts=True)
    dominant = int(counts.max())
    grey = arr.astype(np.float32).mean(-1)
    lap = (4 * grey[1:-1, 1:-1] - grey[:-2, 1:-1] - grey[2:, 1:-1]
           - grey[1:-1, :-2] - grey[1:-1, 2:])
    return {
        "colours": int(len(uniq)),
        "dominant_share": dominant / float(len(flat)),
        "edge_energy": float(np.abs(lap).mean()),
    }


def check_frame(scratch: str, keep_dir: str | None, name: str) -> dict:


    outdir = os.path.join(SCRATCH_ROOT, "_frames", f"{name}.{os.getpid()}")
    drop_scratch(outdir)
    os.makedirs(outdir, exist_ok=True)


    rc, out, err = run(
        x_launcher() + [GODOT, "--quit-after", str(FRAME_COUNT),
                        "--fixed-fps", "30", "--write-movie",
                        godot_path_arg(os.path.join(outdir, "f.png"))],
        scratch, FRAME_TIMEOUT)

    frames = sorted(f for f in os.listdir(outdir) if f.endswith(".png")) \
        if os.path.isdir(outdir) else []
    if not frames:
        # Attribution decides which side of the denominator this lands on. A launcher that


        if rc in LAUNCHER_RC:
            return {"id": "frame", "status": "inconclusive", "gating": False, "sampled": 0,
                    "detail": f"the X-mode launcher did not start (rc={rc}); this is an "
                              "environment fault, not a rendering failure",
                    "log_tail": (err or out)[-400:]}
        return {"id": "frame", "status": "fail", "gating": True, "sampled": 0,
                "detail": f"captured no frames (rc={rc}) — the game did not render",
                "log_tail": (err or out)[-400:]}


    pick = [frames[i] for i in {int(len(frames) * f) for f in (0.35, 0.6, 0.9)}
            if i < len(frames)] if len(frames) > 3 else frames
    measures, bad = [], []
    for f in pick:
        try:
            m = measure_frame(os.path.join(outdir, f))
        except Exception as exc:
            bad.append(f"{f}: {type(exc).__name__}")
            continue
        m["frame"] = f
        measures.append(m)

    if not measures:
        return {"id": "frame", "status": "fail", "gating": True, "sampled": 0,
                "detail": "frames captured but none readable", "errors": bad}


    best = max(measures, key=lambda m: (m["colours"], m["edge_energy"]))
    problems, hints = [], []
    if best["colours"] <= BLANK_COLOURS or best["dominant_share"] >= BLANK_DOMINANT_SHARE:
        problems.append(f"blank screen: best frame has {best['colours']} colours, "
                        f"{best['dominant_share']*100:.1f}% one colour")
    elif best["colours"] <= NEAR_BLANK_COLOURS and best["dominant_share"] >= NEAR_BLANK_SHARE:
        problems.append(f"near-blank: {best['colours']} colours, "
                        f"{best['dominant_share']*100:.1f}% one colour")
    elif best["edge_energy"] < MIN_EDGE_ENERGY:
        problems.append(f"flat: edge energy {best['edge_energy']:.2f} — nothing is drawn "
                        "on top of the background")
    if best["dominant_share"] >= 0.85 and not problems:
        hints.append(f"{best['dominant_share']*100:.0f}% of the screen is one colour")

    wav = os.path.join(outdir, "f.wav")
    audio_silent = None
    if os.path.isfile(wav):
        try:
            audio_silent = os.path.getsize(wav) < 2048
            if not audio_silent:
                with open(wav, "rb") as fh:
                    body = fh.read()[44:]
                audio_silent = not any(body[i] for i in range(0, min(len(body), 400000), 7))
        except OSError:
            audio_silent = None
    if audio_silent:
        hints.append("silent audio track over the whole capture")

    if keep_dir:
        os.makedirs(keep_dir, exist_ok=True)
        for m in measures:
            try:
                shutil.copy2(os.path.join(outdir, m["frame"]),
                             os.path.join(keep_dir, f"{name}__{m['frame']}"))
            except OSError:
                pass

    return {
        "id": "frame", "status": "fail" if problems else ("warn" if hints else "pass"),
        "gating": True, "sampled": len(measures), "captured": len(frames),
        "measures": measures, "best": best, "audio_silent": audio_silent,
        "problems": problems, "hints": hints,
        "detail": "; ".join(problems + hints) or
                  (f"{best['colours']} colours, {best['dominant_share']*100:.0f}% dominant, "
                   f"edge energy {best['edge_energy']:.1f}"),
    }


def check_placeholder_art(root: str) -> dict:


    hits, scanned = [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in set(EXCLUDE) | EVIDENCE_DIRS
                       | {".godot", ".import", "__pycache__"}]
        if set(p.lower() for p in dirpath.split(os.sep)) & PLACEHOLDER_SKIP_DIRS:
            continue
        for name in filenames:
            if os.path.splitext(name)[1].lower() not in {".png", ".webp", ".bmp", ".tga"}:
                continue
            p = os.path.join(dirpath, name)
            scanned += 1
            try:
                with Image.open(p) as im:
                    im = im.convert("RGBA")
                    if im.width * im.height < PLACEHOLDER_MIN_PIXELS:
                        continue
                    arr = np.asarray(im)
            except Exception:
                continue
            opaque = arr[..., 3] > 8
            if opaque.sum() < PLACEHOLDER_MIN_PIXELS:
                continue
            visible = arr[..., :3][opaque]
            n_col = int(len(np.unique(visible, axis=0)))
            if n_col > PLACEHOLDER_MAX_COLOURS:
                continue

            fill = opaque.sum() / float(opaque.size)
            if fill > 0.97:
                hits.append({"path": os.path.relpath(p, root), "colours": n_col,
                             "size": f"{arr.shape[1]}x{arr.shape[0]}"})
    return {
        "id": "placeholder", "status": "warn" if hits else "pass", "gating": False,
        "scanned": scanned, "hits": hits[:20], "hit_count": len(hits),
        "detail": (f"{len(hits)} flat <={PLACEHOLDER_MAX_COLOURS}-colour full-bleed images "
                   f"of {scanned} scanned — render and look")
                  if hits else f"no placeholder-shaped art in {scanned} images",
    }


def check_input(scratch: str, name: str) -> dict:


    out_json = os.path.join(SCRATCH_ROOT, "_input", f"{name}.{os.getpid()}.json")
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    try:
        os.remove(out_json)
    except FileNotFoundError:
        pass
    if not register_probe(scratch):
        return {"id": "input", "status": "skip", "gating": False,
                "detail": "could not inject the probe into the scratch copy"}


    n_actions = count_declared_actions(scratch)
    budget = max(1800, 300 + 240 * n_actions)

    rc, out, err = run(
        x_launcher() + [GODOT, "--quit-after", str(budget), "--fixed-fps", "60",
                        "--gb-probe-out", godot_path_arg(out_json)],
        scratch, INPUT_TIMEOUT)

    if not os.path.isfile(out_json):
        if rc in LAUNCHER_RC:
            return {"id": "input", "status": "inconclusive", "gating": False,
                    "detail": f"the X-mode launcher did not start (rc={rc}); the probe never "
                              "ran, so nothing was measured either way",
                    "log_tail": (err or out)[-400:]}
        return {"id": "input", "status": "fail", "gating": True,
                "detail": f"probe produced no report (rc={rc}) after {budget} frames for "
                          f"{n_actions} declared actions — the game did not reach a "
                          "state where input could be tested",
                "log_tail": (err or out)[-400:]}
    try:
        with open(out_json, encoding="utf-8") as fh:
            rep = json.load(fh)
    except Exception as exc:
        return {"id": "input", "status": "skip", "gating": False,
                "detail": f"probe report unreadable: {type(exc).__name__}"}

    tested = rep.get("actions_tested", [])
    live = rep.get("live_actions", 0)


    per_action = rep.get("per_action", {})
    dead = [a for a, v in per_action.items() if not v.get("live") and v.get("measured", True)]
    unmeasured = [a for a, v in per_action.items() if not v.get("measured", True)]
    entered = rep.get("entered_game", False)


    if not entered:


        return {
            "id": "input", "status": "inconclusive", "gating": False, "inconclusive": True,
            "actions_tested": tested, "live": live, "dead": dead,
            "entered_game": False, "entry_method": rep.get("entry_method"),
            "detail": f"NO EVIDENCE: never reached gameplay (tried: {rep.get('entry_method')}); "
                      f"{len(tested)} declared actions went untested. This is not a pass — "
                      "nothing about the controls was measured. Check the game boots into a "
                      "level (missing binary assets are the usual cause).",
        }

    problems, hints = [], []
    if not tested:
        problems.append("project declares no non-ui input actions at all")
    elif live == 0 and not dead:


        hints.append(f"reached gameplay but all {len(unmeasured)} segments lost the player "
                     "mid-window; nothing about the controls was measured")
    elif live == 0:
        problems.append(f"reached gameplay but none of {len(dead)} measurable actions "
                        "changed the world — dead bindings")
    elif dead:


        shown = sorted(dead)[:8]
        more = "" if len(dead) <= 8 else f" (+{len(dead) - 8} more not shown)"
        tail = f"; {len(unmeasured)} more were not measurable (player lost mid-window)"             if unmeasured else ""
        hints.append(f"{len(dead)} of {len(tested)} actions produced no measurable motion: "
                     + ", ".join(shown) + more + tail)


    return {
        "id": "input", "status": "fail" if problems else ("warn" if hints else "pass"),
        "gating": False, "advisory": True,
        "actions_tested": tested, "live": live, "dead": dead,
        "unmeasured": unmeasured,
        "segment_restores": rep.get("segment_restores"),
        "restore_failures": rep.get("restore_failures"),
        "entered_game": True, "entry_method": rep.get("entry_method"),
        "tracked": rep.get("tracked"), "idle_baseline": rep.get("idle_baseline_per_frame"),
        "per_action": rep.get("per_action", {}), "problems": problems, "hints": hints,
        "detail": "; ".join(problems + hints) or
                  f"{live}/{len(tested)} declared actions move the world "
                  f"(tracked via {rep.get('tracked')}, entered via {rep.get('entry_method')})",
    }


def read_text(path: str) -> str | None:


    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


BOT_MODE_TIMEOUT = 60


BOT_MODE_TIMEOUTS = {"playthrough": 180}
SCRIPT_ERROR_MARKERS = (
    "SCRIPT ERROR", "Nonexistent function", "Invalid call", "Invalid get index",
    "Invalid set index", "Attempt to call function", "Cannot call method",
    "Trying to assign", "Parse Error", "Compile Error", "Invalid access",

    "Lambda capture at index",
)
CHECKS_LINE = re.compile(r"checks?:\s*(\d+)\s*run(?:,\s*(\d+)\s*failed)?", re.I)


ASSERT_LINE = re.compile(r"^\s*(?:\[[\w ]+\]\s*)?(PASS|FAIL|OK|ok)\b", re.M)
VERDICT_LINE = re.compile(r"VERDICT:?\s*([A-Z]+)")

CHECK_CALL = re.compile(
    r"\b(?:_?check|_?expect|_?assert|_?require)(?:_[a-z_]+)?\s*\(")


UNDRIVEABLE = {
    "shots": "needs a display; correctly refuses to run headless",
    "record": "needs a display",
    "recording": "needs a display",
    "capture": "needs a display",
    "video": "needs a display",
    "screenshot": "needs a display",
    "screenshots": "needs a display",
    "demo": "needs a display",
    "demos": "needs a display",
    "bridge": "waits for an external agent to connect",
    "serve": "waits for an external agent to connect",
    "agent": "waits for an external agent to connect",
    "interactive": "waits for a human",
}


BOT_ARG_RE = re.compile(r'--(bot|mode|bot-mode)=')
BOT_MODE_LITERAL = re.compile(r'--(?:bot|mode)=([a-z][\w-]*)')


def _autoloads(root: str) -> list[tuple[str, str]]:
    text = read_text(os.path.join(root, "project.godot"))
    if text is None:
        return []
    return [(m.group(1), m.group(2))
            for m in re.finditer(r'^\s*(\w+)\s*=\s*"\*?res://([^"]+\.gd)"', text, re.M)]


def _parse_modes(body: str, script_abs: str) -> tuple[list[str], str]:


    m = re.search(r"const\s+MODES\s*:?[\w\[\], ]*=\s*(\[|\{)", body)
    if m:
        opener = m.group(1)
        closer = "]" if opener == "[" else "}"
        depth, i = 0, m.end() - 1
        while i < len(body):
            if body[i] in "[{":
                depth += 1
            elif body[i] in "]}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        blob = body[m.end():i]
        if opener == "{":
            names = re.findall(r'"([\w-]+)"\s*:', blob)
        else:
            names = re.findall(r'"([\w-]+)"', blob)
        if names:
            return names, f"const MODES ({'dictionary' if opener == '{' else 'array'})"

    d = os.path.dirname(script_abs)
    if os.path.isdir(d):
        files = sorted(f[5:-3] for f in os.listdir(d)
                       if f.startswith("mode_") and f.endswith(".gd"))
        if files:
            return files, "mode_*.gd files beside the harness"


    literals = sorted(set(BOT_MODE_LITERAL.findall(body)) - {"", "mode"})
    if literals:
        return literals, "--bot=NAME literals in the harness source"
    return [], "none found"


def discover_bot(root: str) -> dict | None:


    candidates: list[tuple[int, str, str, str]] = []
    for name, rel in _autoloads(root):
        abs_path = os.path.join(root, rel)
        body = read_text(abs_path)
        if body is None:
            continue
        segments = set(os.path.normpath(rel).split(os.sep)[:-1])
        score = 0
        if BOT_ARG_RE.search(body):
            score += 4
        if re.search(r"const\s+MODES", body):
            score += 3
        d = os.path.dirname(abs_path)
        if os.path.isdir(d) and any(f.startswith("mode_") and f.endswith(".gd")
                                    for f in os.listdir(d)):
            score += 2
        if segments & {"bot", "harness", "tests", "test", "testing", "bridge"}:
            score += 1
        if "bot" in name.lower() or "harness" in name.lower():
            score += 1
        if score >= 3:
            candidates.append((score, name, rel, abs_path))

    if not candidates:


        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".godot", ".git")]
            found_modes = sorted(f[5:-3] for f in filenames
                                 if f.startswith("mode_") and f.endswith(".gd"))
            if len(found_modes) >= 2:
                rel = os.path.relpath(dirpath, root)
                return {"autoload": "(none — modes found by directory)",
                        "script_rel": rel, "script_abs": os.path.join(dirpath, "mode_x.gd"),
                        "modes": found_modes,
                        "mode_source": f"mode_*.gd files in {rel}/", "flag": "--bot"}
        return None
    candidates.sort(key=lambda c: -c[0])
    _score, name, rel, abs_path = candidates[0]
    body = read_text(abs_path) or ""
    modes, source = _parse_modes(body, abs_path)
    flag = "--bot"
    fm = BOT_ARG_RE.search(body)
    if fm:
        flag = "--" + (fm.group(1) or fm.group(2))
    return {"autoload": name, "script_rel": rel, "script_abs": abs_path,
            "modes": modes, "mode_source": source, "flag": flag}


def static_check_sites(root: str, script_abs: str, mode: str) -> int:


    d = os.path.dirname(script_abs)
    path = os.path.join(d, f"mode_{mode}.gd")
    body = read_text(path)
    if body is None:
        return 0
    body = re.sub(r"^\s*#.*$", "", body, flags=re.M)
    return len(CHECK_CALL.findall(body))


def check_bot(root: str, scratch: str) -> dict:


    found = discover_bot(root)
    if found is None:
        return {"id": "bot", "status": "skip", "gating": False, "harness": None,
                "detail": "no driveable bot harness found — nothing was checked here, which is "
                          "an absence of evidence, not a pass"}
    script_rel = found["script_rel"]
    script_abs = found["script_abs"]
    modes = found["modes"]
    flag = found["flag"]
    if not modes:
        return {"id": "bot", "status": "warn", "gating": False, "harness": script_rel,
                "detail": f"harness {script_rel} advertises no modes "
                          f"({found['mode_source']}) — nothing could be driven"}

    results, errored, bare, empty, shortfall, timed_out = [], [], [], [], [], []
    not_driven: list[str] = []
    self_failed: list[str] = []
    silent: list[str] = []
    for mode in modes:
        if mode in UNDRIVEABLE:
            not_driven.append(f"{mode} ({UNDRIVEABLE[mode]})")
            results.append({"mode": mode, "driven": False,
                            "reason": UNDRIVEABLE[mode], "script_errors": []})
            continue


        rc, out, err = run([GODOT, "--headless", "--path", godot_path_arg(scratch),
                            f"{flag}={mode}", "--", f"{flag}={mode}"],
                           scratch, BOT_MODE_TIMEOUTS.get(mode, BOT_MODE_TIMEOUT))
        blob = (out or "") + "\n" + (err or "")
        hits = sorted({mk for mk in SCRIPT_ERROR_MARKERS if mk in blob})
        cm = CHECKS_LINE.search(blob)
        vm = VERDICT_LINE.search(blob)
        lines = ASSERT_LINE.findall(blob)
        counted = len(lines) or None
        ran = int(cm.group(1)) if cm else counted
        if cm and cm.group(2):
            failed = int(cm.group(2))
        elif lines:
            failed = sum(1 for x in lines if x == "FAIL")
        else:
            failed = None
        verdict = vm.group(1) if vm else None
        expected = static_check_sites(root, script_abs, mode)

        entry = {"mode": mode, "driven": True, "rc": rc, "verdict": verdict,
                 "checks_run": ran,
                 "assert_lines": counted, "count_source": "summary" if cm else "assert lines",
                 "checks_failed": failed, "expected_sites": expected,
                 "script_errors": hits}

        for line in blob.splitlines():
            if any(mk in line for mk in SCRIPT_ERROR_MARKERS):
                entry["first_error"] = line.strip()[:200]
                break
        results.append(entry)

        if hits:
            errored.append(mode)
        elif rc == -9:


            timed_out.append(mode)
        elif verdict == "PASS" and ran is None:
            bare.append(mode)
        elif ran == 0:
            empty.append(mode)
        elif rc == 0 and ran is None:


            silent.append(mode)
        elif verdict == "FAIL" or (failed or 0) > 0:
            self_failed.append(f"{mode} ({failed or '?'} failed)")
        elif ran is not None and expected and ran < expected:
            shortfall.append(f"{mode} {ran}/{expected}")

    problems, hints = [], []
    if errored:
        problems.append(f"script error inside bot mode(s) {', '.join(errored)} — "
                        "checks after the error never ran and the verdict is meaningless")
    if bare:
        problems.append(f"bare PASS with no check count: {', '.join(bare)}")
    if empty:
        problems.append(f"mode executed zero checks: {', '.join(empty)}")


    def listing(items: list[str], keep: int = 6) -> str:
        shown = ", ".join(items[:keep])
        return shown if len(items) <= keep else f"{shown} (+{len(items) - keep} more, {len(items)} total)"

    if self_failed:
        problems.append("the project's own bot reports failing checks: " + listing(self_failed))
    if shortfall:
        hints.append("fewer checks executed than the mode contains: " + listing(shortfall))
    if silent:
        hints.append("exited 0 with no countable check output on stdout: " + listing(silent))
    if timed_out:
        hints.append(f"did not terminate under a generic invocation (mode-specific arguments "
                     f"are likely needed): {', '.join(timed_out)}")
    if not_driven:
        hints.append("not driven, recorded rather than counted: " + "; ".join(not_driven[:6]))

    driven = [r for r in results if r.get("driven")]
    total_ran = sum(r.get("checks_run") or 0 for r in driven)
    total_exp = sum(r.get("expected_sites", 0) for r in driven)
    return {
        "id": "bot", "status": "fail" if problems else ("warn" if hints else "pass"),
        "gating": True, "modes": results, "harness": script_rel,
        "autoload": found["autoload"], "mode_source": found["mode_source"],
        "modes_declared": len(modes), "modes_driven": len(driven),
        "modes_not_driven": not_driven,
        "checks_run_total": total_ran, "expected_sites_total": total_exp,
        "detail": "; ".join(problems + hints) or
                  f"{len(driven)} of {len(modes)} modes driven clean, {total_ran} checks "
                  f"executed against {total_exp} assertion sites",
    }


REPORT_IDS = ["boot", "bot", "frame", "input", "placeholder"]


SYMBOL = {"pass": "ok", "fail": "FAIL", "warn": "warn", "skip": "--",
          "inconclusive": "NO-EVIDENCE"}


def check_project(root: str, args) -> dict:
    name = os.path.basename(os.path.normpath(root))


    if name in ("", ".", ".."):
        name = os.path.basename(os.path.abspath(root)) or "unnamed"
    log(f"[{name}] copying to scratch...")
    started = time.time()
    checks: dict[str, dict] = {}


    if not os.path.isfile(os.path.join(root, "project.godot")):
        nested = [d for d in sorted(os.listdir(root))
                  if os.path.isfile(os.path.join(root, d, "project.godot"))]
        if len(nested) == 1:
            root = os.path.join(root, nested[0])
            log(f"[{name}] engine project is nested in {nested[0]}/")
        else:
            return {"project": name, "path": root, "verdict": "SKIP",
                    "reason": "no project.godot", "checks": {}, "failing": []}

    want = set(args.only) if args.only else set(REPORT_IDS)

    if "placeholder" in want:
        checks["placeholder"] = check_placeholder_art(root)

    scratch = make_scratch(root, name)
    if scratch is None:
        return {"project": name, "path": root, "verdict": "SKIP",
                "reason": "scratch copy failed", "checks": checks, "failing": []}


    log(f"[{name}] cold import...")
    imp = [GODOT, "--headless", "--path", godot_path_arg(scratch), "--import"]
    irc, iout, ierr = run(imp, scratch, BOOT_TIMEOUT)
    if is_retryable_import_crash(irc):


        log(f"[{name}] cold import exited {irc}; retrying once...")
        irc, iout, ierr = run(imp, scratch, BOOT_TIMEOUT)
    if irc != 0:
        if not args.keep_scratch:
            drop_scratch(scratch)
        return {"project": name, "path": root, "verdict": "PASS",
                "reason": f"cold import failed twice (rc={irc})",
                "checks": {"boot": {
                    "id": "boot", "status": "inconclusive", "gating": False,
                    "inconclusive": True,
                    "detail": f"cold `--import` exited {irc} twice; the engine did not "
                              "finish our setup step, so nothing about this project "
                              "was measured either way",
                    "log_tail": (ierr or iout)[-400:]}},
                "inconclusive": ["boot"], "failing": []}


    run(imp, scratch, BOOT_TIMEOUT)

    try:
        if "boot" in want:
            log(f"[{name}] boot...")
            checks["boot"] = check_boot(scratch)
        if "bot" in want:
            log(f"[{name}] bot modes...")
            checks["bot"] = check_bot(root, scratch)
        if "frame" in want:
            log(f"[{name}] frame capture...")
            checks["frame"] = check_frame(scratch, args.frames_dir, name)
        if "input" in want:
            log(f"[{name}] input...")
            checks["input"] = check_input(scratch, name)
    finally:
        if not args.keep_scratch:
            drop_scratch(scratch)


            drop_scratch(os.path.join(SCRATCH_ROOT, "_frames", f"{name}.{os.getpid()}"))
            try:
                os.remove(os.path.join(SCRATCH_ROOT, "_input", f"{name}.{os.getpid()}.json"))
            except OSError:
                pass

    for i in REPORT_IDS:
        checks.setdefault(i, {"id": i, "status": "skip", "gating": False, "detail": "not run"})
    failing = [c["id"] for c in checks.values() if c.get("gating") and c["status"] == "fail"]


    inconclusive = [c["id"] for c in checks.values()
                    if c.get("inconclusive") or c["status"] == "inconclusive"]
    return {
        "project": name, "path": root,
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(time.time() - started, 1),
        "verdict": "FAIL" if failing else "PASS",
        "failing": failing, "inconclusive": inconclusive, "checks": checks,
    }


def print_report(results: list[dict]) -> None:
    head = f"{'project':<34}{'verdict':<9}" + "".join(f"{i:<13}" for i in REPORT_IDS)
    print(head)
    print("-" * len(head))
    for r in results:
        row = f"{r['project']:<34}{r['verdict']:<9}"
        for i in REPORT_IDS:
            row += f"{SYMBOL.get(r['checks'].get(i, {}).get('status', 'skip')):<13}"
        print(row)


    unmeasured = [r for r in results if r.get("inconclusive")]
    if unmeasured:
        print(f"\nNO EVIDENCE — {len(unmeasured)} project(s) passed with a gate that ran and "
              "measured nothing.")
        print("These are not clean results. Nothing was learned about the gates listed.")
        for r in unmeasured:
            print(f"  {r['project']:<34}{r['verdict']:<6} unmeasured: {', '.join(r['inconclusive'])}")

    print("\ndetail")
    print("-" * 74)
    for r in results:
        gaps = [(i, r["checks"][i]) for i in REPORT_IDS
                if i in r["checks"]
                and r["checks"][i]["status"] in ("fail", "warn", "inconclusive")]
        if not gaps:
            print(f"\n{r['project']}  [{r['verdict']}] — clean")
            continue
        print(f"\n{r['project']}  [{r['verdict']}]")
        for i, c in gaps:
            mark = {"fail": "FAIL", "inconclusive": "NOEVID"}.get(c["status"], "warn")
            gate = "" if c.get("gating") else " (advisory)"
            print(f"  {mark:<5}{i:<12}{gate} {c['detail']}")
            for line in c.get("fatal", [])[:4]:
                print(f"        {line}")


def doctor() -> int:

    rows = hostenv.doctor(
        scratch=SCRATCH_ROOT, godot=GODOT, harness_dir=HARNESS_DIR,
        repo_root=os.path.abspath(os.path.join(HERE, "..", "..", "..")))
    rows.append(hostenv.DoctorRow(
        "probe", os.path.isfile(PROBE_SRC), True,
        f"{PROBE_SRC}" + ("" if os.path.isfile(PROBE_SRC) else " is missing (GB_HARNESS_DIR?)")))
    if sys.platform.startswith("linux"):
        try:
            x_launcher()
            rows.append(hostenv.DoctorRow("x_launcher", True, False,
                                          "frame/input will run under " + " ".join(x_launcher()[:1])))
        except EnvironmentError as exc:
            rows.append(hostenv.DoctorRow("x_launcher", False, True, str(exc)))
    else:
        rows.append(hostenv.DoctorRow("x_launcher", True, False,
                                      "none needed: frame/input run in a real window on this OS"))
    return hostenv.print_doctor(rows)


def main() -> int:
    hostenv.configure_stdio()
    ap = argparse.ArgumentParser(
        description="Runtime gate: does the game boot, draw, and respond to input.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Passing means it runs. It says nothing about whether it is any good.")
    ap.add_argument("roots", nargs="*")
    ap.add_argument("--batch", action="append", default=[])
    ap.add_argument("--jsonl", metavar="FILE")
    ap.add_argument("--only", action="append", choices=REPORT_IDS,
                    help="run only these checks (repeatable)")
    ap.add_argument("--frames-dir", metavar="DIR", help="keep the sampled frames here")
    ap.add_argument("--keep-scratch", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--doctor", action="store_true",
                    help="print OS, Godot path/version, display, scratch writability; exit 0/1")
    args = ap.parse_args()

    if args.doctor:
        return doctor()


    if hostenv.godot_resolves(GODOT) is None:
        log("error: " + hostenv.godot_not_found_hint(GODOT))
        log("       run `check_runtime.py --doctor` for the full picture")
        return 2

    roots = list(args.roots)
    for batch in args.batch:
        if not os.path.isdir(batch):
            log(f"warning: no such batch directory: {batch}")
            continue
        for n in sorted(os.listdir(batch)):
            p = os.path.join(batch, n)
            if os.path.isdir(p) and not n.startswith((".", "_")):
                roots.append(p)
    if not roots:
        ap.error("give at least one project directory or --batch path")

    os.makedirs(SCRATCH_ROOT, exist_ok=True)
    results = []
    for root in roots:
        if not os.path.isdir(root):
            log(f"warning: no such project: {root}")
            continue
        results.append(check_project(root, args))
        if args.jsonl:
            with open(args.jsonl, "w", encoding="utf-8") as fh:
                for r in results:
                    fh.write(json.dumps(r) + "\n")

    if not args.quiet:
        print_report(results)
    return 1 if any(r["verdict"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
