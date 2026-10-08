


from __future__ import annotations

import enum
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from evalsys.harness import harness_dir
from evalsys.verdict import Attribution, Item, Verdict, inconclusive


DEFAULT_RESOLUTION: tuple[int, int] = (1152, 648)


GPU_WRAPPER = "/usr/local/bin/godot-gpu"


GPU_ROUTING_FRAMES = 1500


HEADLESS_DISPLAY_SERVER = "headless"


def needs_virtual_display() -> bool:


    return sys.platform.startswith("linux")


def x_launcher(width: int, height: int) -> list[str]:

    if not needs_virtual_display():
        return []
    if shutil.which("xvfb-run") is None:
        raise EnvironmentError(
            "X mode needs xvfb-run on this platform and it is not installed; "
            "`apt install xvfb`, or run on a host with a display"
        )
    return ["xvfb-run", "-a", "-s", f"-screen 0 {width}x{height}x24"]


class RenderMode(str, enum.Enum):


    H = "H"
    X_CPU = "X_CPU"
    X_GPU = "X_GPU"

    @property
    def produces_pixels(self) -> bool:
        return self is not RenderMode.H

    @property
    def needs_display(self) -> bool:
        return self.produces_pixels


def godot_binary() -> str:


    env = os.environ.get("GODOT_BIN")
    if env and os.path.exists(env):
        return env
    if os.path.exists("/opt/godot451-bin/godot"):
        return "/opt/godot451-bin/godot"
    return shutil.which("godot") or "godot"


def build_env(mode: RenderMode, base: dict[str, str] | None = None) -> dict[str, str]:


    env = dict(os.environ if base is None else base)
    env["GODOT_SILENCE_ROOT_WARNING"] = "1"
    env["GODOT_BIN"] = godot_binary()
    if mode is RenderMode.X_GPU:
        env["__GLX_VENDOR_LIBRARY_NAME"] = "nvidia"
        env.setdefault("GODOT_GPU_API", "opengl")
    if mode.needs_display:
        env.pop("DISPLAY", None)
    return env


def route_replay_fixed_fps() -> int | None:


    if os.environ.get("GB_ROUTE_NO_FIXED_FPS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    raw = os.environ.get("GB_ROUTE_FIXED_FPS", "60").strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return 60


def build_command(
    mode: RenderMode,
    project_dir: str | os.PathLike[str],
    *,
    scene: str | None = None,
    quit_after: int | None = None,
    fixed_fps: int | None = None,
    extra_args: Sequence[str] = (),
    user_args: Sequence[str] = (),
    resolution: tuple[int, int] = DEFAULT_RESOLUTION,
) -> list[str]:


    w, h = resolution
    godot = godot_binary()
    proj = str(Path(project_dir))

    if mode is RenderMode.H:
        cmd = [godot, "--headless", "--path", proj]


        if os.environ.get("GODOT_VERBOSE", "").strip().lower() in {"1", "true", "yes"}:
            cmd.insert(2, "--verbose")
        rd = os.environ.get("GODOT_RENDERING_DRIVER", "").strip()
        if rd:
            cmd += ["--rendering-driver", rd]
        if os.environ.get("GODOT_AUDIO_DUMMY", "").strip().lower() in {"1", "true", "yes"}:
            cmd += ["--audio-driver", "Dummy"]
    else:
        launcher = x_launcher(w, h)


        if mode is RenderMode.X_GPU and launcher:
            cmd = launcher + [GPU_WRAPPER, "--path", proj]
        else:
            cmd = launcher + [godot, "--rendering-driver", "opengl3", "--path", proj]
        cmd += ["--resolution", f"{w}x{h}"]

    if quit_after is not None:
        cmd += ["--quit-after", str(quit_after)]
    if fixed_fps is not None:
        cmd += ["--fixed-fps", str(fixed_fps)]
    cmd += list(extra_args)
    if scene and os.environ.get("GODOT_SKIP_SCENE_ARG", "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        cmd.append(scene)
    if user_args:
        cmd.append("--")
        cmd += list(user_args)
    return cmd


@dataclass
class RunResult:


    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False
    launch_failed: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.launch_failed

    @property
    def log(self) -> str:
        return (self.stdout or "") + "\n" + (self.stderr or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "cmd": " ".join(self.cmd),
            "returncode": self.returncode,
            "seconds": round(self.seconds, 3),
            "timed_out": self.timed_out,
            "launch_failed": self.launch_failed,
            "stdout_tail": (self.stdout or "")[-2000:],
            "stderr_tail": (self.stderr or "")[-2000:],
        }


def run(
    cmd: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float = 300.0,
    env: dict[str, str] | None = None,
) -> RunResult:


    started = time.time()
    try:
        p = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        return RunResult(list(cmd), p.returncode, p.stdout, p.stderr, time.time() - started)
    except subprocess.TimeoutExpired as exc:
        def _txt(v: Any) -> str:
            if v is None:
                return ""
            return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)

        return RunResult(
            list(cmd),
            -9,
            _txt(exc.stdout),
            _txt(exc.stderr) + f"\nTIMEOUT after {timeout}s",
            time.time() - started,
            timed_out=True,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return RunResult(
            list(cmd),
            -2,
            "",
            f"{type(exc).__name__}: {exc}",
            time.time() - started,
            launch_failed=f"{type(exc).__name__}: {exc}",
        )


def select_mode(
    frames_needed: int, ms_per_frame: float, needs_pixels: bool
) -> RenderMode:


    if not needs_pixels:
        return RenderMode.H
    if frames_needed > GPU_ROUTING_FRAMES:
        return RenderMode.X_GPU
    return RenderMode.X_CPU


BUDGET_SCALE_ENV = "GB_EVAL_BUDGET_SCALE"


def budget_scale() -> float:
    raw = os.environ.get(BUDGET_SCALE_ENV, "").strip()
    if not raw:
        return 1.0
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 1.0


def scaled_timeout(seconds: float) -> float:
    return seconds * budget_scale()


class scaled_budget:


    def __init__(self, factor: float) -> None:
        self.factor = max(1.0, float(factor))
        self._previous: str | None = None

    def __enter__(self) -> "scaled_budget":
        self._previous = os.environ.get(BUDGET_SCALE_ENV)
        os.environ[BUDGET_SCALE_ENV] = repr(self.factor * budget_scale())
        return self

    def __exit__(self, *exc: object) -> None:
        if self._previous is None:
            os.environ.pop(BUDGET_SCALE_ENV, None)
        else:
            os.environ[BUDGET_SCALE_ENV] = self._previous


def wall_timeout_s(frames: int, ms_per_frame_p95: float, safety: float = 3.0) -> int:


    if frames <= 0 or ms_per_frame_p95 <= 0:
        return int(60 * budget_scale())
    return int(
        math.ceil((frames * ms_per_frame_p95 * safety / 1000.0 + 60) * budget_scale())
    )


def timeout_item(
    id: str, *, frames: int, timeout_s: float, mode: RenderMode, weight: float = 1.0
) -> Item:


    return inconclusive(
        id,
        weight=weight,
        detail=(
            f"render timed out after {timeout_s:.0f}s for {frames} frames in "
            f"{mode.value}; the budget was ours to set, so this is a gap in "
            "coverage, not a finding about the submission"
        ),
        attribution=Attribution.HARNESS,
        evidence={"frames": frames, "timeout_s": timeout_s, "mode": mode.value},
    )


@dataclass
class DisplayServerProbe:


    mode: RenderMode
    display_server: str
    expected_pixels: bool
    ok: bool
    detail: str
    returncode: int = 0
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "display_server": self.display_server,
            "expected_pixels": self.expected_pixels,
            "ok": self.ok,
            "detail": self.detail,
            "returncode": self.returncode,
            "seconds": round(self.seconds, 3),
        }

    def as_item(self, id: str = "render/d1_display_server", weight: float = 1.0) -> Item:
        if self.ok:
            return Item(
                id=id,
                weight=weight,
                verdict=Verdict.PASSED,
                credit=1.0,
                detail=self.detail,
                evidence=self.to_dict(),
            )
        return inconclusive(
            id,
            weight=weight,
            detail=self.detail,
            attribution=Attribution.HARNESS,
            evidence=self.to_dict(),
        )


_DS_MARKER = "GB_DISPLAY_SERVER="


def probe_display_server(
    project_dir: str | os.PathLike[str],
    mode: RenderMode,
    *,
    timeout: float = 120.0,
    scratch: str | os.PathLike[str] | None = None,
) -> DisplayServerProbe:


    _ = scratch
    src = harness_dir() / "gb_display_server_probe.gd"

    cmd = build_command(mode, project_dir, extra_args=["--script", str(src)])
    res = run(cmd, timeout=timeout, env=build_env(mode))

    name = ""
    for line in res.log.splitlines():
        if _DS_MARKER in line:
            name = line.split(_DS_MARKER, 1)[1].strip()
            break

    if not name:
        return DisplayServerProbe(
            mode=mode,
            display_server="",
            expected_pixels=mode.produces_pixels,
            ok=False,
            detail=(
                "the display-server pre-flight produced no answer "
                f"(rc={res.returncode}, timed_out={res.timed_out}); "
                "refusing to guess whether this mode can render"
            ),
            returncode=res.returncode,
            seconds=res.seconds,
        )

    is_headless = name == HEADLESS_DISPLAY_SERVER
    if mode.produces_pixels and is_headless:
        return DisplayServerProbe(
            mode, name, True, False,
            "X mode asked for pixels and got DisplayServer 'headless' -- "
            + ("xvfb-run did not take" if needs_virtual_display()
               else "the engine fell back to the dummy backend")
            + ". Nothing captured in this state is evidence.",
            res.returncode, res.seconds,
        )
    if not mode.produces_pixels and not is_headless:
        return DisplayServerProbe(
            mode, name, False, False,
            f"H mode came up with DisplayServer {name!r} rather than 'headless'; "
            "the run is not the engine-truth-only configuration it was routed as.",
            res.returncode, res.seconds,
        )
    return DisplayServerProbe(
        mode, name, mode.produces_pixels, True,
        f"{mode.value}: DisplayServer.get_name() == {name!r}"
        + (" (pixels available)" if mode.produces_pixels else " (no pixels, as intended)"),
        res.returncode, res.seconds,
    )
