


from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence


from evalsys.engine.import_retry import ImportPass, run_with_crash_retry, settle_import
from evalsys.render.modes import x_launcher

TOOLS_DIR = Path(os.environ.get("GB_TOOLS_DIR", str(Path(__file__).resolve().parents[3] / "tools")))
from evalsys.harness import harness_dir

HARNESS_DIR = harness_dir()


def _default_scratch_root() -> str:


    try:
        spec = importlib.util.spec_from_file_location(
            "_gbtool_check_runtime_scratch", TOOLS_DIR / "check_runtime.py")
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.ascii_safe_scratch_root("gb_ocard_scratch")
    except Exception:
        pass
    if os.name == "posix":
        if os.path.isdir("/data2"):
            return "/tmp/swe-game/gb_ocard_scratch"
        return "/tmp/gb_ocard_scratch"
    base = tempfile.gettempdir()
    try:
        base.encode("ascii")
    except UnicodeEncodeError:
        base = os.path.join(os.environ.get("SystemDrive", "C:") + os.sep, "gb_scratch")
    return os.path.join(base, "gb_ocard_scratch")


SCRATCH_ROOT = Path(os.environ.get("GB_OCARD_SCRATCH", _default_scratch_root()))
GODOT = os.environ.get("GODOT_BIN", "/opt/godot451-bin/godot")
if not os.path.exists(GODOT):
    GODOT = "godot"

CHECK_DELIVERABLES = TOOLS_DIR / "gates" / "check_deliverables.py"
CHECK_RUNTIME = TOOLS_DIR / "gates" / "check_runtime.py"
FRAME_AUDIT = TOOLS_DIR / "gates" / "frame_audit.py"
SCAN_LOCKED = TOOLS_DIR / "scan_locked_assets.py"
RUNTIME_PROBE = HARNESS_DIR / "gb_runtime_probe.gd"


class ToolUnavailable(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fingerprint_tree(root: str | Path, *, exclude_dirs: Sequence[str] = (".git",)) -> str:


    root = Path(root)
    drop = set(exclude_dirs)
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in drop)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            try:
                digest = sha256_file(p)
            except OSError as exc:
                digest = f"unreadable:{type(exc).__name__}"
            h.update(rel.encode("utf-8", "replace"))
            h.update(b"\0")
            h.update(digest.encode())
            h.update(b"\n")
    return h.hexdigest()


class ToolOutcome(str, Enum):


    REPORTED = "reported"
    CRASHED = "crashed"
    TIMED_OUT = "timed_out"
    UNPARSEABLE = "unparseable"

    @property
    def ran(self) -> bool:
        return self is ToolOutcome.REPORTED


@dataclass(frozen=True)
class ToolInvocation:


    tool: str
    tool_path: str
    tool_sha256: str
    argv: list[str]
    cwd: str
    returncode: int
    seconds: float
    outcome: ToolOutcome
    stdout_tail: str = ""
    stderr_tail: str = ""
    extra_hashes: dict[str, str] = field(default_factory=dict)
    """Other files whose content shaped the reading, e.g. the injected probe."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "tool_path": self.tool_path,
            "tool_sha256": self.tool_sha256,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "returncode": self.returncode,
            "seconds": round(self.seconds, 3),
            "outcome": self.outcome.value,
            "stderr_tail": self.stderr_tail,
            "extra_hashes": dict(self.extra_hashes),
        }

    @property
    def command_line(self) -> str:
        return " ".join(self.argv)


def _tool_project_arg(project: str | Path) -> str:


    return str(Path(project).resolve())


def _classify_exit(rc: int, timed_out: bool) -> ToolOutcome:
    if timed_out:
        return ToolOutcome.TIMED_OUT
    if rc in (0, 1):
        return ToolOutcome.REPORTED
    return ToolOutcome.CRASHED


def _run(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    timeout: int = 900,
    tool_path: Path | None = None,
    extra_hashes: dict[str, str] | None = None,
    exit_classifier=_classify_exit,
) -> tuple[ToolInvocation, str, str]:
    argv = list(argv)
    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd) if cwd else None, capture_output=True,
            text=True, errors="replace", timeout=timeout,
        )
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        rc = -9

        def _txt(v: Any) -> str:
            if v is None:
                return ""
            return v.decode("utf-8", "replace") if isinstance(v, bytes) else v

        out, err = _txt(exc.stdout), _txt(exc.stderr) + f"\nTIMEOUT after {timeout}s"
    except FileNotFoundError as exc:
        rc, out, err = -2, "", f"{exc}"

    inv = ToolInvocation(
        tool=(tool_path.name if tool_path else argv[0]),
        tool_path=str(tool_path or argv[0]),
        tool_sha256=(sha256_file(tool_path) if tool_path and tool_path.exists() else ""),
        argv=argv,
        cwd=str(cwd or os.getcwd()),
        returncode=rc,
        seconds=time.time() - started,
        outcome=exit_classifier(rc, timed_out),
        stdout_tail=out[-4000:],
        stderr_tail=err[-4000:],
        extra_hashes=dict(extra_hashes or {}),
    )
    return inv, out, err


def load_tool_module(path: str | Path):


    path = Path(path)
    if not path.is_file():
        raise ToolUnavailable(f"tool not found: {path}")
    name = "_gbtool_" + path.stem
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ToolUnavailable(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _copy_rules():


    try:
        cr = load_tool_module(CHECK_RUNTIME)
        return set(cr.EXCLUDE), set(cr.EVIDENCE_DIRS), set(cr.HEAVY_EXT)
    except (ToolUnavailable, AttributeError):
        return (
            {"inputs", "reference", "staging", "_archives", ".git", "web", "builds", "export",
             ".godot"},
            {"compare", "verify", "recording", "shots", "screenshots"},
            {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".avi", ".mp4", ".mov", ".gif"},
        )


@dataclass
class Scratch:


    source: str
    path: str

    def drop(self) -> None:
        real = os.path.realpath(self.path)
        root = os.path.realpath(SCRATCH_ROOT)
        if not real.startswith(root + os.sep):
            return
        shutil.rmtree(real, ignore_errors=True)

    def __enter__(self) -> "Scratch":
        return self

    def __exit__(self, *exc: Any) -> None:
        if not os.environ.get("GB_OCARD_KEEP_SCRATCH"):
            self.drop()


def make_scratch(project: str | Path, *, tag: str = "run") -> Scratch:

    src = Path(project).resolve()
    exclude, evidence_dirs, heavy_ext = _copy_rules()
    dst = SCRATCH_ROOT / f"{src.name}-{tag}-{uuid.uuid4().hex[:8]}"
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    src_real = os.path.realpath(src)

    def ignore(dirpath: str, names: list[str]) -> set[str]:
        drop = {n for n in names if n in exclude}
        rel = os.path.relpath(os.path.realpath(dirpath), src_real)
        if set(rel.split(os.sep)) & evidence_dirs:
            drop |= {
                n for n in names
                if os.path.splitext(n)[1].lower() in heavy_ext or n.endswith(".png.import")
            }
        return drop

    shutil.copytree(src, dst, ignore=ignore, symlinks=True)
    from ..engine.import_retry import configure_scratch_import

    configure_scratch_import(dst)
    return Scratch(source=str(src), path=str(dst))


def project_root(project: str | Path) -> Path:


    from ..interface.loader import project_root as canonical_project_root

    return canonical_project_root(project)


def read_gb_levels(project: str | Path, interface: Any = None) -> list[str]:

    if interface is None:
        from ..interface import load_submission_interface

        interface = load_submission_interface(project)
    return [level.scene for level in interface.levels]


def read_gb_manifest(project: str | Path, interface: Any = None) -> dict[str, Any]:

    if interface is None:
        from ..interface import load_submission_interface

        interface = load_submission_interface(project)
    return interface.runtime_dict()


def deliverables_report_ids() -> list[str]:
    return list(load_tool_module(CHECK_DELIVERABLES).REPORT_IDS)


def runtime_report_ids() -> list[str]:
    return list(load_tool_module(CHECK_RUNTIME).REPORT_IDS)


@dataclass
class DeliverablesResult:


    project: str
    path: str
    record: dict[str, Any]
    invocation: ToolInvocation

    @property
    def ran(self) -> bool:
        return self.invocation.outcome.ran and bool(self.record)

    @property
    def verdict(self) -> str | None:

        return self.record.get("verdict")

    def check(self, check_id: str) -> dict[str, Any] | None:
        return (self.record.get("checks") or {}).get(check_id)

    def status(self, check_id: str) -> str | None:
        c = self.check(check_id)
        return c.get("status") if c else None

    def detail(self, check_id: str) -> str:
        c = self.check(check_id)
        return (c or {}).get("detail", "")

    @property
    def missing_check_ids(self) -> list[str]:

        got = set((self.record.get("checks") or {}).keys())
        try:
            expected = set(deliverables_report_ids())
        except ToolUnavailable:
            return []
        return sorted(expected - got)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "path": self.path,
            "ran": self.ran,
            "verdict": self.verdict,
            "failing": self.record.get("failing", []),
            "statuses": {k: v.get("status") for k, v in (self.record.get("checks") or {}).items()},
            "missing_check_ids": self.missing_check_ids,
            "invocation": self.invocation.to_dict(),
        }


def run_check_deliverables(
    project: str | Path,
    *,
    timeout: int = 1800,
    skip_locked: bool = False,
    jsonl_path: str | Path | None = None,
) -> DeliverablesResult:

    if not CHECK_DELIVERABLES.is_file():
        raise ToolUnavailable(f"missing {CHECK_DELIVERABLES}")
    project = Path(project)
    out = Path(jsonl_path or (SCRATCH_ROOT / f"deliverables-{uuid.uuid4().hex[:8]}.jsonl"))
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, str(CHECK_DELIVERABLES), _tool_project_arg(project),
            "--jsonl", str(out), "--quiet"]
    if skip_locked:
        argv.append("--skip-locked")
    inv, _out, _err = _run(argv, cwd=TOOLS_DIR, timeout=timeout, tool_path=CHECK_DELIVERABLES)

    record: dict[str, Any] = {}
    if inv.outcome.ran:
        record = _first_jsonl_row(out, project.name)
        if not record:
            inv = _with_outcome(inv, ToolOutcome.UNPARSEABLE)
    if jsonl_path is None:
        out.unlink(missing_ok=True)
    return DeliverablesResult(
        project=project.name, path=str(project), record=record, invocation=inv
    )


def _with_outcome(inv: ToolInvocation, outcome: ToolOutcome) -> ToolInvocation:
    return ToolInvocation(
        tool=inv.tool, tool_path=inv.tool_path, tool_sha256=inv.tool_sha256,
        argv=inv.argv, cwd=inv.cwd, returncode=inv.returncode, seconds=inv.seconds,
        outcome=outcome, stdout_tail=inv.stdout_tail, stderr_tail=inv.stderr_tail,
        extra_hashes=inv.extra_hashes,
    )


def _first_jsonl_row(path: Path, project_name: str) -> dict[str, Any]:
    if not path.is_file():
        return {}
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    for r in rows:
        if r.get("project") == project_name:
            return r
    return rows[0] if rows else {}


@dataclass
class BotModeReading:


    mode: str
    driven: bool
    rc: int | None = None
    verdict: str | None = None
    checks_run: int | None = None
    assert_lines: int | None = None
    count_source: str | None = None
    checks_failed: int | None = None
    expected_sites: int = 0
    script_errors: list[str] = field(default_factory=list)
    first_error: str = ""
    reason: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BotModeReading":
        return cls(
            mode=d.get("mode", "?"),
            driven=bool(d.get("driven")),
            rc=d.get("rc"),
            verdict=d.get("verdict"),
            checks_run=d.get("checks_run"),
            assert_lines=d.get("assert_lines"),
            count_source=d.get("count_source"),
            checks_failed=d.get("checks_failed"),
            expected_sites=int(d.get("expected_sites") or 0),
            script_errors=list(d.get("script_errors") or []),
            first_error=d.get("first_error", ""),
            reason=d.get("reason", ""),
        )


@dataclass
class RuntimeResult:


    project: str
    path: str
    record: dict[str, Any]
    invocation: ToolInvocation

    @property
    def ran(self) -> bool:
        return self.invocation.outcome.ran and bool(self.record)

    def check(self, check_id: str) -> dict[str, Any] | None:
        return (self.record.get("checks") or {}).get(check_id)

    def status(self, check_id: str) -> str | None:
        c = self.check(check_id)
        return c.get("status") if c else None

    @property
    def bot(self) -> dict[str, Any]:
        return self.check("bot") or {}

    @property
    def bot_modes(self) -> list[BotModeReading]:
        return [BotModeReading.from_dict(m) for m in (self.bot.get("modes") or [])]

    @property
    def bot_requested(self) -> bool:


        bot = self.bot
        if not bot:
            return False
        return bot.get("detail") != "not run" or "harness" in bot

    @property
    def bot_skipped(self) -> bool:


        return self.bot.get("status") == "skip" and self.bot_requested

    @property
    def boot_passed_on_main_scene(self) -> bool:


        return self.status("boot") == "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "ran": self.ran,
            "verdict": self.record.get("verdict"),
            "statuses": {k: v.get("status") for k, v in (self.record.get("checks") or {}).items()},
            "bot_status": self.bot.get("status"),
            "bot_skipped": self.bot_skipped,
            "modes": [m.mode for m in self.bot_modes],
            "invocation": self.invocation.to_dict(),
        }


def run_check_runtime(
    project: str | Path,
    *,
    only: Sequence[str] | None = None,
    timeout: int = 3600,
    jsonl_path: str | Path | None = None,
    frames_dir: str | Path | None = None,
) -> RuntimeResult:


    if not CHECK_RUNTIME.is_file():
        raise ToolUnavailable(f"missing {CHECK_RUNTIME}")
    project = Path(project)
    out = Path(jsonl_path or (SCRATCH_ROOT / f"runtime-{uuid.uuid4().hex[:8]}.jsonl"))
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, str(CHECK_RUNTIME), _tool_project_arg(project),
            "--jsonl", str(out), "--quiet"]
    for c in only or []:
        argv += ["--only", c]
    if frames_dir:
        argv += ["--frames-dir", str(frames_dir)]
    inv, _o, _e = _run(
        argv, cwd=TOOLS_DIR, timeout=timeout, tool_path=CHECK_RUNTIME,
        extra_hashes={"gb_runtime_probe.gd": sha256_file(RUNTIME_PROBE)}
        if RUNTIME_PROBE.is_file() else {},
    )
    record: dict[str, Any] = {}
    if inv.outcome.ran:
        record = _first_jsonl_row(out, project.name)
        if not record:
            inv = _with_outcome(inv, ToolOutcome.UNPARSEABLE)
    if jsonl_path is None:
        out.unlink(missing_ok=True)
    return RuntimeResult(project=project.name, path=str(project), record=record, invocation=inv)


def load_runtime_jsonl(path: str | Path, project_name: str) -> RuntimeResult:

    p = Path(path)
    record = _first_jsonl_row(p, project_name)
    inv = ToolInvocation(
        tool=CHECK_RUNTIME.name, tool_path=str(CHECK_RUNTIME),
        tool_sha256=sha256_file(CHECK_RUNTIME) if CHECK_RUNTIME.is_file() else "",
        argv=["(replayed)", str(p)], cwd=str(p.parent), returncode=0, seconds=0.0,
        outcome=ToolOutcome.REPORTED if record else ToolOutcome.UNPARSEABLE,
    )
    return RuntimeResult(project=project_name, path=str(p), record=record, invocation=inv)


@dataclass
class ColdImportResult:


    ok: bool
    cold: bool
    fatal: list[str]
    warnings: list[str]
    invocation: ToolInvocation

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "cold": self.cold,
            "fatal_count": len(self.fatal), "fatal": self.fatal[:20],
            "warning_count": len(self.warnings),
            "invocation": self.invocation.to_dict(),
        }


def run_cold_import(project: str | Path, *, timeout: int = 600) -> ColdImportResult:

    cr = load_tool_module(CHECK_RUNTIME)
    with make_scratch(project_root(project), tag="cold") as sc:
        cache = Path(sc.path) / ".godot"
        cold = True
        if cache.exists():
            shutil.rmtree(cache, ignore_errors=True)
        cold = not cache.exists()
        classify = lambda rc, to: (
            ToolOutcome.TIMED_OUT if to
            else ToolOutcome.REPORTED if rc in (0, 1)
            else ToolOutcome.CRASHED
        )
        argv = [GODOT, "--headless", "--path", sc.path, "--import"]

        def _once() -> ImportPass:
            inv, out, err = _run(argv, cwd=sc.path, timeout=timeout,
                                 tool_path=Path(GODOT), exit_classifier=classify)
            fatal_lines, warn_lines = cr.classify_log(out + "\n" + err)
            return ImportPass(
                returncode=inv.returncode,
                timed_out=inv.outcome is ToolOutcome.TIMED_OUT,
                errors=tuple(fatal_lines),
                seconds=inv.seconds,
                payload=(inv, warn_lines),
            )


        settled_run = settle_import(_once, extra_error_pass=False)
        first = settled_run.first.payload[0]
        second = settled_run.scored.payload[0]
        fatal = list(settled_run.scored.errors)
        warn = list(settled_run.scored.payload[1])
        first_fatal = list(settled_run.first.errors)
    inv = second
    ok = (first.outcome.ran and second.outcome.ran
          and first.returncode == 0 and second.returncode == 0 and not fatal)

    settled = [f for f in first_fatal if f not in fatal]
    return ColdImportResult(ok=ok, cold=cold, fatal=fatal[:20],
                            warnings=(warn + [f"settled on reimport: {s}" for s in settled])[:20],
                            invocation=inv)


@dataclass
class LevelProbeResult:


    level: str
    reached: bool
    entry_method: str
    report: dict[str, Any]
    fatal: list[str]
    warnings: list[str]
    invocation: ToolInvocation
    mode: str = "H"
    import_invocation: ToolInvocation | None = None
    """The cold `--import` that preceded the launch, when `cold=True`."""

    @property
    def actions_tested(self) -> list[str]:
        return list(self.report.get("actions_tested") or [])

    @property
    def live_actions(self) -> int:
        return int(self.report.get("live_actions") or 0)

    @property
    def per_action(self) -> dict[str, dict[str, Any]]:
        return dict(self.report.get("per_action") or {})

    @property
    def idle_baseline(self) -> float:
        return float(self.report.get("idle_baseline_per_frame") or 0.0)

    @property
    def idle_sig_delta(self) -> float:
        return float(self.report.get("idle_sig_delta") or 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level, "reached": self.reached, "mode": self.mode,
            "entry_method": self.entry_method,
            "actions_tested": self.actions_tested,
            "live_actions": self.live_actions,
            "idle_baseline_per_frame": self.idle_baseline,
            "idle_sig_delta": self.idle_sig_delta,
            "per_action": self.per_action,
            "fatal_count": len(self.fatal), "fatal": self.fatal[:12],
            "invocation": self.invocation.to_dict(),
            "import_invocation": (self.import_invocation.to_dict()
                                  if self.import_invocation else None),
        }


def _register_probe(scratch: str) -> bool:

    try:
        shutil.copy2(RUNTIME_PROBE, os.path.join(scratch, "gb_runtime_probe.gd"))
        pg = os.path.join(scratch, "project.godot")
        with open(pg, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        line = 'GBRuntimeProbe="*res://gb_runtime_probe.gd"\n'
        if "[autoload]" in text:
            text = text.replace("[autoload]\n", "[autoload]\n\n" + line, 1)
        else:
            text = text.rstrip("\n") + "\n\n[autoload]\n\n" + line
        with open(pg, "w", encoding="utf-8") as fh:
            fh.write(text)
        return True
    except OSError:
        return False


def run_level_probe(
    project: str | Path,
    *,
    level: str | None = None,
    mode: str = "H",
    timeout: int = 420,
    frames: int = 3000,
    cold: bool = True,
    interface: Any = None,
) -> LevelProbeResult:


    cr = load_tool_module(CHECK_RUNTIME)
    root = project_root(project)
    levels = read_gb_levels(root, interface)
    target = level or (levels[0] if levels else "")
    if not target:


        inv = ToolInvocation(
            tool="gb_runtime_probe",
            tool_path=str(RUNTIME_PROBE),
            tool_sha256=sha256_file(RUNTIME_PROBE) if RUNTIME_PROBE.is_file() else "",
            argv=["(refused: no gb_levels.json entry)"],
            cwd=str(root),
            returncode=0,
            seconds=0.0,
            outcome=ToolOutcome.REPORTED,
        )
        return LevelProbeResult(
            level="",
            reached=False,
            entry_method="none",
            report={
                "detail": "gb_levels.json declares no level to measure O1 on",
                "actions_tested": [],
                "live_actions": 0,
                "per_action": {},
            },
            fatal=["gb_levels.json declares no level to measure O1 on"],
            warnings=[],
            invocation=inv,
            mode=mode,
        )

    with make_scratch(root, tag="probe") as sc:
        import_inv: ToolInvocation | None = None
        if cold:


            shutil.rmtree(Path(sc.path) / ".godot", ignore_errors=True)
            import_inv, iout, ierr = _run(
                [GODOT, "--headless", "--path", sc.path, "--import"],
                cwd=sc.path, timeout=timeout, tool_path=Path(GODOT),
                exit_classifier=lambda rc, to: (
                    ToolOutcome.TIMED_OUT if to
                    else ToolOutcome.REPORTED if rc in (0, 1) else ToolOutcome.CRASHED
                ),
            )
        out_json = Path(sc.path).parent / f"probe-{uuid.uuid4().hex[:8]}.json"
        if not _register_probe(sc.path):
            raise ToolUnavailable("could not inject gb_runtime_probe.gd into the scratch copy")
        base = [GODOT]
        if mode == "H":
            base.append("--headless")
        else:
            base += ["--rendering-driver", "opengl3"]
        argv = base + ["--path", sc.path, target, "--quit-after", str(frames),
                       "--fixed-fps", "60", "--gb-probe-out", str(out_json)]
        if mode != "H":
            argv = x_launcher(1280, 720) + argv
        inv, out, err = _run(
            argv, cwd=sc.path, timeout=timeout, tool_path=Path(GODOT),
            extra_hashes={"gb_runtime_probe.gd": sha256_file(RUNTIME_PROBE)},
            exit_classifier=lambda rc, to: (
                ToolOutcome.TIMED_OUT if to
                else ToolOutcome.REPORTED if rc in (0, 1) else ToolOutcome.CRASHED
            ),
        )
        report: dict[str, Any] = {}
        if out_json.is_file():
            try:
                report = json.loads(out_json.read_text(encoding="utf-8", errors="replace"))
            except ValueError:
                report = {}
            out_json.unlink(missing_ok=True)
        fatal, warn = cr.classify_log(out + "\n" + err)

    return LevelProbeResult(
        level=target,
        reached=bool(report.get("entered_game")),
        entry_method=str(report.get("entry_method", "none")),
        report=report,
        fatal=fatal[:20],
        warnings=warn[:20],
        invocation=inv,
        mode=mode,
        import_invocation=import_inv,
    )


@dataclass
class BotRunResult:


    mode: str
    harness: str | None
    stdout: str
    stderr: str
    harness_sites: int
    invocation: ToolInvocation

    @property
    def text(self) -> str:
        return self.stdout + "\n" + self.stderr

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "harness": self.harness,
            "harness_sites": self.harness_sites,
            "stdout_tail": self.stdout[-2000:],
            "invocation": self.invocation.to_dict(),
        }


def discover_bot(project: str | Path) -> dict[str, Any] | None:

    cr = load_tool_module(CHECK_RUNTIME)
    return cr.discover_bot(str(project_root(project)))


def run_bot_mode(
    project: str | Path, mode: str, *, timeout: int | None = None
) -> BotRunResult:


    cr = load_tool_module(CHECK_RUNTIME)
    root = project_root(project)
    found = cr.discover_bot(str(root))
    if found is None:
        raise ToolUnavailable(f"{root}: no driveable bot harness")
    flag = found["flag"]
    sites = cr.static_check_sites(str(root), found["script_abs"], mode)
    with make_scratch(root, tag=f"bot-{mode}") as sc:
        inv, out, err = _run(
            [GODOT, "--headless", "--path", sc.path, f"{flag}={mode}", "--", f"{flag}={mode}"],
            cwd=sc.path, timeout=timeout or cr.BOT_MODE_TIMEOUT, tool_path=CHECK_RUNTIME,
            exit_classifier=lambda rc, to: (
                ToolOutcome.TIMED_OUT if to
                else ToolOutcome.REPORTED if rc in (0, 1) else ToolOutcome.CRASHED
            ),
        )
    return BotRunResult(mode=mode, harness=found["script_rel"], stdout=out, stderr=err,
                        harness_sites=sites, invocation=inv)


@dataclass
class FrameCaptureResult:


    level: str
    mode: str
    frames_captured: int
    measures: list[dict[str, Any]]
    best: dict[str, Any]
    frame_paths: list[str]
    invocation: ToolInvocation
    import_invocation: ToolInvocation | None = None


    frame_evidence: list[dict[str, Any]] = field(default_factory=list)
    fixed_fps: float = 30.0

    @property
    def has_pixels(self) -> bool:
        return self.frames_captured > 0 and bool(self.measures)

    @property
    def imported_first(self) -> bool:


        return bool(self.import_invocation and self.import_invocation.outcome.ran)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level, "mode": self.mode,
            "frames_captured": self.frames_captured,
            "imported_first": self.imported_first,
            "best": self.best, "measures": self.measures,
            "frame_paths": self.frame_paths[:8],
            "frame_evidence": self.frame_evidence[:8],
            "fixed_fps": self.fixed_fps,
            "invocation": self.invocation.to_dict(),
            "import_invocation": (self.import_invocation.to_dict()
                                  if self.import_invocation else None),
        }


def capture_level_frames(
    project: str | Path,
    *,
    level: str | None = None,
    frames: int = 120,
    timeout: int = 420,
    keep_dir: str | Path | None = None,
    interface: Any = None,
) -> FrameCaptureResult:


    cr = load_tool_module(CHECK_RUNTIME)
    root = project_root(project)
    levels = read_gb_levels(root, interface)
    target = level or (levels[0] if levels else "")
    if not target:
        inv = ToolInvocation(
            tool="frame_capture",
            tool_path="",
            tool_sha256="",
            argv=["(refused: no gb_levels.json entry)"],
            cwd=str(root),
            returncode=0,
            seconds=0.0,
            outcome=ToolOutcome.REPORTED,
        )
        return FrameCaptureResult(
            level="",
            mode="X_CPU",
            frames_captured=0,
            measures=[],
            best={},
            frame_paths=[],
            invocation=inv,
        )

    with make_scratch(root, tag="frames") as sc:


        shutil.rmtree(Path(sc.path) / ".godot", ignore_errors=True)
        classify_import = lambda rc, to: (
            ToolOutcome.TIMED_OUT if to
            else ToolOutcome.REPORTED if rc in (0, 1) else ToolOutcome.CRASHED
        )
        import_argv = [GODOT, "--headless", "--path", sc.path, "--import"]

        def _import_once() -> ImportPass:
            inv, io, ie = _run(
                import_argv, cwd=sc.path, timeout=timeout, tool_path=Path(GODOT),
                exit_classifier=classify_import,
            )
            return ImportPass(
                returncode=inv.returncode,
                timed_out=inv.outcome is ToolOutcome.TIMED_OUT,
                seconds=inv.seconds,
                payload=inv,
            )


        import_pass, _retried = run_with_crash_retry(_import_once)
        import_inv = import_pass.payload
        outdir = Path(sc.path).parent / f"frames-{uuid.uuid4().hex[:8]}"
        outdir.mkdir(parents=True, exist_ok=True)
        capture_fps = 30.0
        argv = x_launcher(1280, 720) + [
            GODOT, "--rendering-driver", "opengl3", "--path", sc.path, target,
            "--quit-after", str(frames), "--fixed-fps", str(int(capture_fps)),
            "--write-movie", str(outdir / "f.png")]
        inv, _o, _e = _run(
            argv, cwd=sc.path, timeout=timeout, tool_path=Path(GODOT),
            exit_classifier=lambda rc, to: (
                ToolOutcome.TIMED_OUT if to
                else ToolOutcome.REPORTED if rc in (0, 1) else ToolOutcome.CRASHED
            ),
        )
        pngs = sorted(p for p in outdir.glob("*.png"))
        measures: list[dict[str, Any]] = []
        kept: list[str] = []
        frame_evidence: list[dict[str, Any]] = []
        if pngs:
            pick = ([(int(len(pngs) * f), pngs[int(len(pngs) * f)])
                     for f in (0.35, 0.6, 0.9)
                     if int(len(pngs) * f) < len(pngs)]
                    if len(pngs) > 3 else list(enumerate(pngs)))
            for frame_index, p in pick:
                try:
                    m = cr.measure_frame(str(p))
                except Exception:
                    continue
                m["frame"] = p.name
                measures.append(m)
                if keep_dir:
                    Path(keep_dir).mkdir(parents=True, exist_ok=True)
                    dest = Path(keep_dir) / f"{root.name}__{p.name}"
                    shutil.copy2(p, dest)
                    kept.append(str(dest))
                    frame_evidence.append({
                        "path": str(dest),
                        "source_frame": p.name,
                        "frame_index": frame_index,
                        "timestamp_seconds": round(frame_index / capture_fps, 6),
                    })
        best = max(measures, key=lambda m: (m["colours"], m["edge_energy"])) if measures else {}
        n = len(pngs)
        shutil.rmtree(outdir, ignore_errors=True)

    return FrameCaptureResult(level=target, mode="X", frames_captured=n, measures=measures,
                              best=best, frame_paths=kept, invocation=inv,
                              import_invocation=import_inv,
                              frame_evidence=frame_evidence,
                              fixed_fps=capture_fps)


@dataclass
class FrameAuditResult:
    frames: int
    effective: int
    identical: list[list[str]]
    near: list[dict[str, Any]]
    invocation: ToolInvocation | None = None

    @property
    def duplicate_groups(self) -> int:
        return len(self.identical)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames": self.frames, "effective": self.effective,
            "identical_groups": self.identical, "near": self.near[:20],
            "invocation": self.invocation.to_dict() if self.invocation else None,
        }


def run_frame_audit(
    paths: Sequence[str | Path], *, near_pct: float = 2.0, tol: int = 8
) -> FrameAuditResult:

    fa = load_tool_module(FRAME_AUDIT)
    rep = fa.audit([str(p) for p in paths], near_pct, tol)
    inv = ToolInvocation(
        tool=FRAME_AUDIT.name, tool_path=str(FRAME_AUDIT),
        tool_sha256=sha256_file(FRAME_AUDIT), argv=["(in-process) frame_audit.audit"],
        cwd=os.getcwd(), returncode=0, seconds=0.0, outcome=ToolOutcome.REPORTED,
    )
    return FrameAuditResult(
        frames=rep.get("frames", 0), effective=rep.get("effective", 0),
        identical=rep.get("identical", []), near=rep.get("near", []), invocation=inv,
    )


@dataclass
class LockedAssetsResult:
    records: list[dict[str, Any]]
    invocation: ToolInvocation

    def by_tier(self, tier: str) -> list[dict[str, Any]]:
        return [r for r in self.records if r.get("tier") == tier]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": len(self.records),
            "locked": len(self.by_tier("LOCKED")),
            "suspect": len(self.by_tier("SUSPECT")),
            "invocation": self.invocation.to_dict(),
        }


def run_scan_locked_assets(
    project: str | Path, *, timeout: int = 900, include_clean: bool = False
) -> LockedAssetsResult:
    if not SCAN_LOCKED.is_file():
        raise ToolUnavailable(f"missing {SCAN_LOCKED}")
    out = SCRATCH_ROOT / f"locked-{uuid.uuid4().hex[:8]}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, str(SCAN_LOCKED), _tool_project_arg(project),
            "--jsonl", str(out), "--quiet"]
    if include_clean:
        argv.append("--all")
    inv, _o, _e = _run(argv, cwd=TOOLS_DIR, timeout=timeout, tool_path=SCAN_LOCKED)
    records: list[dict[str, Any]] = []
    if out.is_file():
        for line in out.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except ValueError:
                    pass
        out.unlink(missing_ok=True)
    return LockedAssetsResult(records=records, invocation=inv)


@dataclass
class AudioReading:


    source: str
    peak_dbfs: float | None = None
    duty_cycle: float | None = None
    """Share of sampled windows whose level is above the silence floor."""
    discrete_events: int | None = None
    categories: dict[str, int] = field(default_factory=dict)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "peak_dbfs": self.peak_dbfs,
            "duty_cycle": self.duty_cycle, "discrete_events": self.discrete_events,
            "categories": dict(self.categories), "detail": self.detail,
        }


SILENCE_FLOOR_DBFS = -60.0
"""Above this a window counts as audible. The silent recordings measured -91."""


def read_wav_peak(path: str | Path, *, windows: int = 64) -> AudioReading:


    import struct
    import wave

    p = Path(path)
    if not p.is_file():
        return AudioReading(source=str(p), detail="no capture wav; nothing to read")
    try:
        with wave.open(str(p), "rb") as w:
            n = w.getnframes()
            width = w.getsampwidth()
            if width != 2 or n == 0:
                return AudioReading(
                    source=str(p),
                    detail=f"unsupported capture ({width * 8}-bit, {n} frames)",
                )
            raw = w.readframes(n)
    except (OSError, EOFError, wave.Error) as exc:
        return AudioReading(source=str(p), detail=f"capture unreadable: {type(exc).__name__}")

    total = len(raw) // 2
    if total == 0:
        return AudioReading(source=str(p), detail="empty capture")
    samples = struct.unpack("<%dh" % total, raw[: total * 2])
    step = max(1, total // windows)
    peak = 0
    audible = 0
    seen = 0
    for start in range(0, total, step):
        chunk = samples[start:start + step]
        if not chunk:
            continue
        seen += 1
        local = max(abs(s) for s in chunk)
        peak = max(peak, local)
        if local > 0 and 20.0 * _log10(local / 32768.0) > SILENCE_FLOOR_DBFS:
            audible += 1
    peak_db = 20.0 * _log10(peak / 32768.0) if peak > 0 else -float("inf")
    return AudioReading(
        source=str(p),
        peak_dbfs=peak_db if peak > 0 else -120.0,
        duty_cycle=(audible / seen) if seen else 0.0,
        detail=f"{seen} windows sampled from {total} samples",
    )


def _log10(x: float) -> float:
    import math

    return math.log10(x) if x > 0 else -12.0
