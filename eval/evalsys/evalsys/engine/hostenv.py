


from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from typing import NamedTuple


def is_windows(platform: str | None = None) -> bool:


    return (platform if platform is not None else sys.platform) == "win32"


POSIX_SCRATCH_VOLUME = "/data2"


SCRATCH_OVERRIDE_ENV = "GB_GATES_SCRATCH"


def scratch_root(
    name: str,
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    tempdir: Callable[[], str] = tempfile.gettempdir,
    isdir: Callable[[str], bool] = os.path.isdir,
) -> str:


    env = os.environ if environ is None else environ
    override = env.get(SCRATCH_OVERRIDE_ENV)
    if override:
        return os.path.join(override, name)
    if not is_windows(platform):
        if isdir(POSIX_SCRATCH_VOLUME):
            return os.path.join("/tmp", "swe-game", name)
        return f"/tmp/{name}"
    base = tempdir()
    try:
        base.encode("ascii")
    except UnicodeEncodeError:
        base = os.path.join(env.get("SystemDrive", "C:") + os.sep, "gb_scratch")
    return os.path.join(base, name)


def is_within(path: str, root: str, *, platform: str | None = None) -> bool:


    real = os.path.realpath(path)
    real_root = os.path.realpath(root)
    if is_windows(platform):
        real, real_root = os.path.normcase(real), os.path.normcase(real_root)
    return real.startswith(real_root + os.sep)


def copytree_kwargs(*, platform: str | None = None) -> dict:


    if is_windows(platform):
        return {"symlinks": False, "ignore_dangling_symlinks": True}
    return {"symlinks": True}


GODOT_PREFERRED_POSIX = "/opt/godot451-bin/godot"
GODOT_EXPECTED_VERSION = "4.5.1"


GODOT_WINDOWS_NAMES = (
    "Godot_v4.5.1-stable_win64_console.exe",
    "Godot_v4.5.1-stable_win64.exe",
    "godot.windows.editor.x86_64.console.exe",
    "godot.windows.editor.x86_64.exe",
    "godot_console.exe",
    "godot.exe",
)


GODOT_WINDOWS_GLOBS = (
    "Godot_v4*_win64_console.exe",
    "Godot_v4*_win64.exe",
    "godot.windows.*.x86_64.console.exe",
    "godot.windows.*.x86_64.exe",
)


def windows_install_dirs(environ: Mapping[str, str] | None = None) -> list[str]:

    env = os.environ if environ is None else environ
    out: list[str] = []

    def add(*parts: str | None) -> None:
        if any(p is None or p == "" for p in parts):
            return
        joined = os.path.join(*[p for p in parts if p])
        if joined not in out:
            out.append(joined)

    add(env.get("GODOT_HOME"))
    add(env.get("GODOT_DIR"))
    add(env.get("LOCALAPPDATA"), "Programs", "Godot")
    add(env.get("LOCALAPPDATA"), "Godot")
    add(env.get("ProgramFiles"), "Godot")
    add(env.get("ProgramFiles(x86)"), "Godot")
    add(env.get("USERPROFILE"), "scoop", "apps", "godot", "current")
    add(env.get("USERPROFILE"), "Godot")
    add(env.get("USERPROFILE"), "Downloads")
    add(env.get("ChocolateyInstall"), "lib", "godot", "tools")
    drive = env.get("SystemDrive", "C:")
    add(drive + os.sep, "Godot")
    add(drive + os.sep, "tools", "godot")
    return out


def prefer_console_build(path: str, *, exists: Callable[[str], bool] = os.path.exists) -> str:

    d, base = os.path.split(path)
    low = base.lower()
    if "console" in low or not low.endswith(".exe"):
        return path
    stem = base[:-4]
    for cand in (f"{stem}_console.exe", f"{stem}.console.exe"):
        p = os.path.join(d, cand)
        if exists(p):
            return p
    return path


def find_godot(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    exists: Callable[[str], bool] = os.path.exists,
    iglob: Callable[[str], list[str]] = glob.glob,
) -> str:


    env = os.environ if environ is None else environ
    if not is_windows(platform):
        cand = env.get("GODOT_BIN", GODOT_PREFERRED_POSIX)
        return cand if exists(cand) else "godot"

    override = env.get("GODOT_BIN")
    if override and exists(override):
        return override
    for name in GODOT_WINDOWS_NAMES + ("godot",):
        hit = which(name)
        if hit:
            return prefer_console_build(hit, exists=exists)
    for d in windows_install_dirs(env):
        for name in GODOT_WINDOWS_NAMES:
            p = os.path.join(d, name)
            if exists(p):
                return prefer_console_build(p, exists=exists)
        for pat in GODOT_WINDOWS_GLOBS:
            hits = sorted(iglob(os.path.join(d, pat)))
            if hits:
                return prefer_console_build(hits[-1], exists=exists)
    return "godot"


def godot_resolves(godot: str, *, which: Callable[[str], str | None] = shutil.which,
                   exists: Callable[[str], bool] = os.path.exists) -> str | None:

    if os.path.dirname(godot):
        return godot if exists(godot) else None
    return which(godot)


def godot_not_found_hint(godot: str, *, platform: str | None = None,
                         environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    if is_windows(platform):
        searched = ", ".join(windows_install_dirs(env)[:6])
        return (
            f"no Godot {GODOT_EXPECTED_VERSION} binary found ({godot!r} is not on PATH). "
            f"Set GODOT_BIN to the full path of Godot_v{GODOT_EXPECTED_VERSION}-stable_win64_console.exe "
            f"(the _console build, so stdout reaches this gate), or put it on PATH. "
            f"Also searched: {searched}"
        )
    return (
        f"no Godot {GODOT_EXPECTED_VERSION} binary found ({godot!r} is not on PATH and "
        f"{GODOT_PREFERRED_POSIX} is absent). Set GODOT_BIN or install the 4.5.1 build there."
    )


def godot_arg_path(path: str, *, platform: str | None = None) -> str:


    return path.replace("\\", "/") if is_windows(platform) else path


class Display(NamedTuple):
    available: bool
    detail: str


def display_status(*, platform: str | None = None,
                   environ: Mapping[str, str] | None = None,
                   which: Callable[[str], str | None] = shutil.which) -> Display:


    env = os.environ if environ is None else environ
    plat = platform if platform is not None else sys.platform
    if is_windows(plat):
        try:
            import ctypes

            monitors = int(ctypes.windll.user32.GetSystemMetrics(80))
        except Exception:
            return Display(True, "desktop session assumed (monitor count unavailable)")
        if monitors > 0:
            return Display(True, f"desktop session, {monitors} monitor(s); no Xvfb needed")
        return Display(False, "no monitors in this session; frame/input need an interactive desktop")
    if plat == "darwin":
        return Display(True, "macOS window server")
    xvfb = which("xvfb-run")
    if xvfb is None:
        return Display(False, "xvfb-run not on PATH (`apt install xvfb`, or a PATH shim)")
    note = "" if xvfb == "/usr/bin/xvfb-run" else " (PATH shim, not /usr/bin: the renamed-Xvfb workaround is in effect)"
    disp = env.get("DISPLAY")
    return Display(True, f"xvfb-run at {xvfb}{note}; DISPLAY={disp or 'unset'} (ignored, xvfb-run allocates its own)")


def kill_tree(pid: int, *, platform: str | None = None) -> None:


    if is_windows(platform):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, check=False)
        return
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def run_captured(cmd: list[str], cwd: str, timeout: int, *,
                 platform: str | None = None) -> tuple[int, str, str]:


    if not is_windows(platform):
        try:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as exc:
            return -9, _text(exc.stdout), _text(exc.stderr) + f"\nTIMEOUT after {timeout}s"
        except FileNotFoundError:
            return -2, "", f"{cmd[0]} not found"

    try:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return -2, "", f"{cmd[0]} not found"
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid, platform=platform)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return -9, out or "", (err or "") + f"\nTIMEOUT after {timeout}s"
    return proc.returncode, out, err


def _text(v: bytes | str | None) -> str:

    if v is None:
        return ""
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else v


def describe_returncode(rc: int, *, platform: str | None = None) -> str:


    if is_windows(platform) and rc > 0xFFFF:
        return f"exit code {rc} (0x{rc & 0xFFFFFFFF:08X})"
    return f"exit code {rc}"


def configure_stdio(*, platform: str | None = None,
                    environ: Mapping[str, str] | None = None) -> bool:


    env = os.environ if environ is None else environ
    if not is_windows(platform):
        return False
    if env.get("PYTHONIOENCODING") or env.get("PYTHONUTF8") == "1":
        return False
    changed = False
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
            changed = True
        except Exception:
            pass
    return changed


class DoctorRow(NamedTuple):
    name: str
    ok: bool
    required: bool
    detail: str


def _godot_version(godot: str, *, run=run_captured) -> tuple[int, str]:
    rc, out, err = run([godot, "--headless", "--version"], os.getcwd(), 60)
    text = (out or err or "").strip()

    return rc, (text.splitlines()[-1].strip() if text else "")


def _git_setting(key: str, cwd: str) -> str:
    try:
        p = subprocess.run(["git", "config", "--get", key], cwd=cwd, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=20)
    except Exception:
        return "(git unavailable)"
    return p.stdout.strip() or "(unset)"


def doctor(*, scratch: str, godot: str, harness_dir: str | None = None,
           repo_root: str | None = None, platform: str | None = None,
           run=run_captured) -> list[DoctorRow]:


    plat = platform if platform is not None else sys.platform
    rows: list[DoctorRow] = []
    rows.append(DoctorRow("os", True, False,
                          f"{plat} / {os.name}; python {sys.version.split()[0]} at {sys.executable}"))


    resolved = godot_resolves(godot)
    if resolved is None:
        rows.append(DoctorRow("godot", False, True, godot_not_found_hint(godot, platform=plat)))
    else:
        rc, version = _godot_version(resolved, run=run)
        if rc == -2 or not version:
            rows.append(DoctorRow("godot", False, True,
                                  f"{resolved}: `--version` printed nothing (rc={rc}). On Windows this "
                                  "is the plain GUI build; use Godot_v4.5.1-stable_win64_console.exe"))
        else:
            ok = version.startswith(GODOT_EXPECTED_VERSION)
            note = "" if ok else f" -- expected {GODOT_EXPECTED_VERSION}; projects authored in 4.5.1 misreport under other versions"
            rows.append(DoctorRow("godot", ok, True, f"{resolved} -> {version}{note}"))
        if is_windows(plat) and "console" not in os.path.basename(resolved).lower():
            rows.append(DoctorRow("godot_console", False, False,
                                  f"{os.path.basename(resolved)} is not the _console build; engine "
                                  "stdout may not reach the gate. Prefer "
                                  "Godot_v4.5.1-stable_win64_console.exe"))
    if os.environ.get("GODOT_BIN") and not os.path.exists(os.environ["GODOT_BIN"]):
        rows.append(DoctorRow("GODOT_BIN", False, False,
                              f"GODOT_BIN={os.environ['GODOT_BIN']!r} does not exist; it was ignored"))


    disp = display_status(platform=plat)
    rows.append(DoctorRow("display", disp.available, True, disp.detail))


    try:
        os.makedirs(scratch, exist_ok=True)
        probe = os.path.join(scratch, f".doctor.{os.getpid()}")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok\n")
        os.remove(probe)
        free = shutil.disk_usage(scratch).free // (1024 ** 3)
        ascii_ok = True
        try:
            scratch.encode("ascii")
        except UnicodeEncodeError:
            ascii_ok = False
        detail = f"{scratch} writable, {free} GiB free"
        if not ascii_ok:
            detail += "; NON-ASCII path: Godot 4.5.1 crashes importing from it, set GB_GATES_SCRATCH"
        if len(scratch) > 80:
            detail += f"; long ({len(scratch)} chars) -- scratch copies may hit MAX_PATH, set GB_GATES_SCRATCH"
        rows.append(DoctorRow("scratch", ascii_ok, True, detail))
    except OSError as exc:
        rows.append(DoctorRow("scratch", False, True, f"{scratch}: {type(exc).__name__}: {exc}"))


    missing = []
    for mod in ("numpy", "PIL"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    rows.append(DoctorRow("python_deps", not missing, True,
                          "numpy, Pillow importable" if not missing else f"missing: {', '.join(missing)}"))


    if harness_dir and os.path.isdir(harness_dir):
        crlf = []
        for name in sorted(os.listdir(harness_dir)):
            if name.endswith(".gd"):
                try:
                    with open(os.path.join(harness_dir, name), "rb") as fh:
                        if b"\r\n" in fh.read():
                            crlf.append(name)
                except OSError:
                    pass
        rows.append(DoctorRow("harness_lf", not crlf, True,
                              "eval/harness/*.gd are LF on disk" if not crlf else
                              f"CRLF on disk: {', '.join(crlf)} -- checkout rewrote them; "
                              "set `git config core.autocrlf false` and re-checkout, or `git add --renormalize .`"))
    if repo_root:
        autocrlf = _git_setting("core.autocrlf", repo_root)
        ok = autocrlf.lower() in ("false", "input", "(unset)") or not is_windows(plat)
        rows.append(DoctorRow("git_autocrlf", ok, False,
                              f"core.autocrlf={autocrlf}"
                              + ("" if ok else " -- .gitattributes pins the hashed classes to LF, but "
                                 "`false` is the safe setting for this repo")))
    return rows


def print_doctor(rows: list[DoctorRow], out=None) -> int:

    out = out or sys.stdout
    width = max(len(r.name) for r in rows) if rows else 8
    for r in rows:
        mark = "ok  " if r.ok else ("FAIL" if r.required else "warn")
        out.write(f"{mark} {r.name:<{width}}  {r.detail}\n")
    bad = [r for r in rows if r.required and not r.ok]
    out.write("\n" + ("environment ok" if not bad else
                      f"{len(bad)} blocking problem(s): " + ", ".join(r.name for r in bad)) + "\n")
    return 0 if not bad else 1
