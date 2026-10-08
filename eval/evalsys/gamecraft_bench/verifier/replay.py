


from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import json
import os
import random
import signal
import shutil
import subprocess
import time
from pathlib import Path

from .. import config as cfg

_DEFAULT_GODOT_WINDOW_TIMEOUT_SECONDS = 45.0


_KEYCODES: dict[str, str] = {
    "ESCAPE": "Escape",
    "ENTER": "Return",
    "SPACE": "space",
    "TAB": "Tab",
    "BACKSPACE": "BackSpace",
    "DELETE": "Delete",
    "UP": "Up",
    "DOWN": "Down",
    "LEFT": "Left",
    "RIGHT": "Right",
    "SHIFT": "Shift_L",
    "CTRL": "Control_L",
    "ALT": "Alt_L",
    **{c: c.lower() for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    **{str(d): str(d) for d in range(10)},
}


class ReplayError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class ReplayResult:
    output_mp4: Path
    duration_seconds: float
    godot_returncode: int


def replay_trace(
    *,
    project_dir: Path,
    trace_path: Path,
    output_mp4: Path,
    viewport: tuple[int, int] = (1280, 720),
    record_size: tuple[int, int] | None = (854, 480),
    fps: int = 30,
    godot_bin: str | None = None,
    settle_seconds: float = 1.5,
    log_dir: Path | None = None,
    max_replay_seconds: float = 90.0,
) -> ReplayResult:


    project_dir = Path(project_dir).resolve()
    trace_path = Path(trace_path).resolve()
    output_mp4 = Path(output_mp4).resolve()
    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    if log_dir is not None:
        log_dir = Path(log_dir).resolve()
        log_dir.mkdir(parents=True, exist_ok=True)

    godot = godot_bin or cfg.GODOT_BIN
    if not godot:
        raise ReplayError("no Godot binary configured (set GAMECRAFT_BENCH_GODOT_BIN)")
    for tool in ("Xvfb", "xdotool", "ffmpeg"):
        if shutil.which(tool) is None:
            raise ReplayError(f"required tool not on PATH: {tool}")

    trace = json.loads(trace_path.read_text())
    events = list(trace.get("events", []))
    duration_frames = int(trace.get("duration_frames", 0))
    scenario = trace.get("scenario")
    replay_frames = max(
        duration_frames,
        *(int(ev["frame"]) for ev in events),
    ) if events else duration_frames
    if replay_frames < 0:
        raise ReplayError(f"negative trace duration/frame in {trace_path}")
    trace_seconds = replay_frames / fps
    if trace_seconds > max_replay_seconds:
        raise ReplayError(
            f"trace lasts {trace_seconds:.2f}s, exceeding "
            f"max_replay_seconds={max_replay_seconds}s"
        )

    w, h = viewport

    procs: list[subprocess.Popen] = []
    log_handles: list = []

    def _open_log(name: str):
        if log_dir is None:
            return subprocess.DEVNULL
        h = open(log_dir / name, "wb")
        log_handles.append(h)
        return h

    try:


        xvfb_log = _open_log("xvfb.log")
        xvfb, display_n = _start_xvfb(xvfb_log, viewport=(w, h))
        display = f":{display_n}"
        procs.append(xvfb)

        env = {**os.environ, "DISPLAY": display, "GODOT_SILENCE_ROOT_WARNING": "1",
               "LP_NUM_THREADS": "1"}


        godot_cmd: list[str] = [
            godot,
            "--path", str(project_dir),
            "--display-driver", "x11",
            "--rendering-driver", "opengl3",
            "--audio-driver", "Dummy",
            "--resolution", f"{w}x{h}",
            "--single-window",
        ]
        if scenario:
            godot_cmd += ["--", "--scenario", str(scenario)]
        godot_log = _open_log("godot.log")
        godot_proc = subprocess.Popen(
            godot_cmd, env=env, stdout=godot_log, stderr=godot_log,
            preexec_fn=lambda: signal.signal(signal.SIGPIPE, signal.SIG_IGN),
        )
        procs.append(godot_proc)


        time.sleep(settle_seconds)
        if godot_proc.poll() is not None:
            raise ReplayError(
                f"godot exited early with code {godot_proc.returncode} "
                f"(see {log_dir/'godot.log' if log_dir else 'godot.log'})"
            )
        window_id = _find_godot_window(
            env,
            pid=godot_proc.pid,
            timeout=_godot_window_timeout_seconds(),
        )


        _xdotool(env, "windowfocus", "--sync", window_id)


        ffmpeg_log_path = log_dir / "ffmpeg.log" if log_dir is not None else None
        ffmpeg_log = _open_log("ffmpeg.log")
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "x11grab",
            "-framerate", str(fps),
            "-video_size", f"{w}x{h}",
            "-i", display,
        ]
        if record_size is not None and record_size != viewport:
            rw, rh = record_size
            ffmpeg_cmd += ["-vf", f"scale={rw}:{rh}:flags=lanczos"]
        ffmpeg_record_seconds = trace_seconds + 2.0
        ffmpeg_cmd += [
            "-t", f"{ffmpeg_record_seconds:.3f}",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            str(output_mp4),
        ]
        ffmpeg = subprocess.Popen(
            ffmpeg_cmd, env=env,
            stdin=subprocess.PIPE, stdout=ffmpeg_log, stderr=ffmpeg_log,
        )
        procs.append(ffmpeg)
        _wait_for_ffmpeg_capture(ffmpeg, ffmpeg_log_path, timeout=15.0)


        t0 = time.time()
        deadline = t0 + max_replay_seconds
        last_frame = 0
        for ev in events:
            frame = int(ev["frame"])
            target = min(t0 + frame / fps, deadline)
            if not _sleep_until(target, deadline=deadline):
                raise ReplayError(
                    f"replay exceeded max_replay_seconds={max_replay_seconds}s "
                    f"while waiting for event at frame {frame}"
                )
            _post_event(ev, env, window_id=window_id)
            last_frame = max(last_frame, frame)


        end_target = min(t0 + replay_frames / fps, deadline)
        _sleep_until(end_target, deadline=deadline)
        elapsed = time.time() - t0


        with contextlib.suppress(Exception):
            assert ffmpeg.stdin is not None
            ffmpeg.stdin.write(b"q\n")
            ffmpeg.stdin.flush()
        try:
            ffmpeg.wait(timeout=10)
        except subprocess.TimeoutExpired:
            ffmpeg.terminate()
            ffmpeg.wait(timeout=5)


        godot_rc = _stop(godot_proc)

        if not output_mp4.exists() or output_mp4.stat().st_size == 0:
            raise ReplayError(
                f"recording is empty: {output_mp4}. See ffmpeg/godot logs."
            )

        return ReplayResult(
            output_mp4=output_mp4,


            duration_seconds=trace_seconds,
            godot_returncode=godot_rc,
        )

    finally:
        for p in reversed(procs):
            if p.poll() is None:
                _stop(p)
        for h in log_handles:
            with contextlib.suppress(Exception):
                h.close()


def _bound_x11_displays() -> set[int]:


    bound: set[int] = set()
    sock_dir = Path("/tmp/.X11-unix")
    if sock_dir.is_dir():
        for entry in sock_dir.iterdir():
            name = entry.name
            if name.startswith("X") and name[1:].isdigit():
                bound.add(int(name[1:]))
    try:
        for line in Path("/proc/net/unix").read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) < 8:
                continue
            path = parts[-1]

            if path.startswith("@/tmp/.X11-unix/X"):
                tail = path[len("@/tmp/.X11-unix/X"):]
                if tail.isdigit():
                    bound.add(int(tail))
    except OSError:
        pass
    return bound


def _free_display(
    *, start: int = 99, end: int = 199, skip: set[int] | None = None
) -> int:


    bound = _bound_x11_displays()
    skip = skip or set()
    for n in range(start, end):
        if n in bound or n in skip:
            continue
        return n
    raise ReplayError(f"no free X display in :{start}..:{end - 1}")


def _wait_for_xvfb(
    proc: subprocess.Popen, display_n: int, *, timeout: float
) -> None:


    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise ReplayError(
                f"Xvfb on :{display_n} exited early with code {proc.returncode}"
            )
        if _x_display_bound(display_n):
            return
        time.sleep(0.05)
    raise ReplayError(f"Xvfb on :{display_n} did not bind in {timeout}s")


def _x_display_bound(display_n: int) -> bool:
    suffix = f"/tmp/.X11-unix/X{display_n}"
    try:
        for line in Path("/proc/net/unix").read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) < 8:
                continue
            path = parts[-1]
            if path == suffix or path == f"@{suffix}":
                return True
    except OSError:
        pass
    return False


def _start_xvfb(
    log,
    *,
    viewport: tuple[int, int],
    timeout: float = 5.0,
    max_attempts: int = 5,
) -> tuple[subprocess.Popen, int]:


    w, h = viewport
    last_err: ReplayError | None = None
    display_start = int(cfg._env("GAMECRAFT_BENCH_XVFB_DISPLAY_START", "200") or "200")
    display_end = int(cfg._env("GAMECRAFT_BENCH_XVFB_DISPLAY_END", "500") or "500")
    for _ in range(max_attempts):
        proc: subprocess.Popen | None = None
        display_n = -1
        try:
            with open("/tmp/gamecraft-bench-xvfb-display.lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                display_n = _free_display_randomized(
                    start=display_start,
                    end=display_end,
                )
                proc = subprocess.Popen(
                    ["Xvfb", f":{display_n}", "-screen", "0",
                     f"{w}x{h}x24", "-nolisten", "tcp"],
                    stdout=log, stderr=log,
                )
                _wait_for_xvfb(proc, display_n, timeout=timeout)
                return proc, display_n
        except ReplayError as exc:
            last_err = exc
            if proc is not None:
                with contextlib.suppress(Exception):
                    _stop(proc)
    raise last_err or ReplayError("Xvfb failed to start after retries")


def _free_display_randomized(*, start: int, end: int) -> int:
    bound = _bound_x11_displays()
    candidates = [n for n in range(start, end) if n not in bound]
    if not candidates:
        raise ReplayError(f"no free X display in :{start}..:{end - 1}")
    rng = random.Random(f"{os.getpid()}:{time.time_ns()}")
    return candidates[rng.randrange(len(candidates))]


def _sleep_until(target: float, *, deadline: float | None = None) -> bool:


    while True:
        now = time.time()
        if now >= target:
            return True
        if deadline is not None and now >= deadline:
            return False
        time.sleep(min(target - now, 0.01))


def _wait_for_ffmpeg_capture(
    proc: subprocess.Popen,
    log_path: Path | None,
    *,
    timeout: float,
) -> None:


    if log_path is None:
        time.sleep(0.2)
        return

    deadline = time.time() + timeout
    last_tail = b""
    while time.time() < deadline:
        if proc.poll() is not None:
            detail = _decode_log_tail(last_tail)
            raise ReplayError(
                f"ffmpeg exited before recording started with code "
                f"{proc.returncode}{detail}"
            )
        try:
            data = log_path.read_bytes()
        except OSError:
            data = b""
        if b"frame=" in data:
            return
        last_tail = data[-1200:]
        time.sleep(0.05)

    detail = _decode_log_tail(last_tail)
    raise ReplayError(f"ffmpeg did not start recording within {timeout}s{detail}")


def _decode_log_tail(data: bytes) -> str:
    if not data:
        return ""
    text = data.decode(errors="replace").strip()
    return f"; log tail: {text}" if text else ""


def _godot_window_timeout_seconds() -> float:
    raw = (cfg._env("GAMECRAFT_BENCH_GODOT_WINDOW_TIMEOUT_SECONDS", "") or "").strip()
    if not raw:
        return _DEFAULT_GODOT_WINDOW_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_GODOT_WINDOW_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_GODOT_WINDOW_TIMEOUT_SECONDS


def _stop(proc: subprocess.Popen, *, term_timeout: float = 5.0) -> int:
    if proc.poll() is not None:
        return proc.returncode
    proc.terminate()
    try:
        return proc.wait(timeout=term_timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait(timeout=2.0)


def _find_godot_window(env: dict, *, pid: int, timeout: float) -> str:
    deadline = time.time() + timeout
    last_err = ""
    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--pid", str(pid)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=0.5,
            )
        except subprocess.TimeoutExpired:
            last_err = "xdotool search timed out"
        else:
            if r.returncode == 0:
                ids = [line.strip() for line in r.stdout.decode().splitlines() if line.strip()]
                if ids:
                    return ids[-1]
            last_err = (r.stderr or b"").decode(errors="replace").strip()

        try:
            r = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", ".*"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=0.5,
            )
        except subprocess.TimeoutExpired:
            last_err = "xdotool window search timed out"
        else:
            if r.returncode == 0:
                ids = [line.strip() for line in r.stdout.decode().splitlines() if line.strip()]
                if ids:
                    win = ids[-1]
                    _xdotool(env, "windowactivate", "--sync", win)
                    return win
            last_err = (r.stderr or b"").decode(errors="replace").strip()
        time.sleep(0.05)
    detail = f": {last_err}" if last_err else ""
    raise ReplayError(f"could not find Godot X window in {timeout}s{detail}")


def _post_event(ev: dict, env: dict, *, window_id: str) -> None:
    typ = ev["type"]
    if typ == "wait":
        return
    if typ == "mouse_move":
        _xdotool(env, "mousemove", "--window", window_id,
                 str(int(ev["x"])), str(int(ev["y"])))
        return
    if typ == "mouse_click":
        button = _mouse_button(ev.get("button", "left"))


        _xdotool(env, "mousemove", "--window", window_id,
                 str(int(ev["x"])), str(int(ev["y"])),
                 "mousedown", button, "mouseup", button)
        return
    if typ in ("mouse_down", "mouse_up"):
        button = _mouse_button(ev.get("button", "left"))
        action = "mousedown" if typ == "mouse_down" else "mouseup"

        _xdotool(env, "mousemove", "--window", window_id,
                 str(int(ev["x"])), str(int(ev["y"])),
                 action, button)
        return
    if typ in ("key_press", "key_down", "key_up"):
        sym = _normalize_keycode(ev["keycode"])
        action = {"key_press": "key", "key_down": "keydown", "key_up": "keyup"}[typ]
        _xdotool(env, action, "--window", window_id, sym)
        return
    raise ReplayError(f"unknown event type: {typ!r}")


def _mouse_button(button: object) -> str:
    raw = str(button).strip().lower()
    if raw == "left":
        return "1"
    if raw == "right":
        return "3"
    raise ReplayError(f"unknown mouse button: {button!r}")


def _normalize_keycode(keycode: object) -> str:
    key = str(keycode).strip().upper()
    mapped = _KEYCODES.get(key)
    if mapped is None:
        raise ReplayError(f"unknown keycode: {keycode!r}")
    return mapped


def _xdotool(env: dict, *args: str) -> None:
    subprocess.run(["xdotool", *args], env=env, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
