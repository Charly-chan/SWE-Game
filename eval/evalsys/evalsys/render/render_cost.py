


from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from evalsys.probe.inject import CAPTURE_PROBE, prepare_scratch
from evalsys.render.modes import (
    DEFAULT_RESOLUTION,
    RenderMode,
    build_command,
    build_env,
    run,
    wall_timeout_s,
)
from evalsys.verdict import Attribution, Item, inconclusive, passed


FRAMES_PER_OP = 46.5


RENDER_BUDGET_S = 300.0


ROUTE_OP_CEILING = 40


L5_MS_PER_FRAME_MAX = 165.0
L5_FRAMES = 1800


WARMUP_FRAMES = 60


MEASURE_FRAMES = 600


def percentile(values: Sequence[float], q: float) -> float:


    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(math.ceil(q / 100.0 * len(ordered))) - 1))
    return float(ordered[k])


@dataclass
class LevelChoice:


    source: str
    scene: str
    ok: bool
    detail: str
    levels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "scene": self.scene,
            "ok": self.ok,
            "detail": self.detail,
            "levels": self.levels,
        }


def first_playable_level(
    project_dir: str | os.PathLike[str], *, interface: Any = None
) -> LevelChoice:

    if interface is None:
        from ..interface import load_submission_interface

        interface = load_submission_interface(project_dir)
    path = interface.project_root / "gb_levels.json"
    levels = [level.scene for level in interface.levels]
    if not levels:
        return LevelChoice(
            str(path), "", False,
            "the normalized interface declares no playable level; not substituting main_scene",
            levels,
        )
    first = interface.levels[0]
    if not first.valid:
        return LevelChoice(
            str(path), first.scene, False,
            f"normalized levels[0] is unresolved: {first.scene}; not substituting main_scene",
            levels,
        )
    return LevelChoice(
        str(path), first.scene, True,
        f"normalized interface levels[0] = {first.scene}", levels,
    )


@dataclass
class RenderCost:


    measured_on: str
    mode: str
    resolution: str
    ms_per_frame_p50: float
    ms_per_frame_p95: float
    route_op_cap: int
    l5_eligible: bool
    gate_wall_timeout_s: int
    measured: bool = True
    detail: str = ""
    samples: int = 0
    ms_per_frame_mean: float = 0.0
    ms_per_frame_max: float = 0.0
    frame_post_draw_count_2s: int = -1
    display_server: str = ""
    warmup_frames: int = WARMUP_FRAMES
    project: str = ""
    scene: str = ""
    seconds_wall: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "measured_on": self.measured_on,
            "mode": self.mode,
            "resolution": self.resolution,
            "ms_per_frame_p50": round(self.ms_per_frame_p50, 1),
            "ms_per_frame_p95": round(self.ms_per_frame_p95, 1),
            "route_op_cap": self.route_op_cap,
            "l5_eligible": self.l5_eligible,
            "gate_wall_timeout_s": self.gate_wall_timeout_s,
            "measured": self.measured,
            "detail": self.detail,
            "samples": self.samples,
            "ms_per_frame_mean": round(self.ms_per_frame_mean, 1),
            "ms_per_frame_max": round(self.ms_per_frame_max, 1),
            "frame_post_draw_count_2s": self.frame_post_draw_count_2s,
            "display_server": self.display_server,
            "warmup_frames": self.warmup_frames,
            "project": self.project,
            "scene": self.scene,
            "seconds_wall": round(self.seconds_wall, 2),
        }

    def write(self, path: str | os.PathLike[str]) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return out

    def as_item(self, id: str = "render/cost_measured", weight: float = 1.0) -> Item:
        if self.measured:
            return passed(id, weight=weight, detail=self.detail, evidence=self.to_dict())


        return inconclusive(
            id,
            weight=weight,
            detail=self.detail,
            attribution=Attribution.HARNESS,
            evidence=self.to_dict(),
        )


def route_op_cap(ms_per_frame: float) -> int:


    if ms_per_frame <= 0:
        return ROUTE_OP_CEILING
    ops = RENDER_BUDGET_S * 1000.0 / (FRAMES_PER_OP * ms_per_frame)
    return max(1, min(ROUTE_OP_CEILING, int(math.floor(ops))))


def gate_wall_timeout(ms_per_frame_p95: float) -> int:

    return int(math.ceil(ms_per_frame_p95 * L5_FRAMES * 3.0 / 1000.0))


def cost_from_samples(
    frame_us: Sequence[int],
    *,
    warmup: int = WARMUP_FRAMES,
    mode: RenderMode = RenderMode.X_CPU,
    resolution: tuple[int, int] = DEFAULT_RESOLUTION,
    measured_on: str = "",
    project: str = "",
    scene: str = "",
) -> RenderCost:

    body = [us / 1000.0 for us in list(frame_us)[warmup:]]
    if not body:
        return RenderCost(
            measured_on=measured_on,
            mode=mode.value,
            resolution=f"{resolution[0]}x{resolution[1]}",
            ms_per_frame_p50=0.0,
            ms_per_frame_p95=0.0,
            route_op_cap=0,
            l5_eligible=False,
            gate_wall_timeout_s=0,
            measured=False,
            detail=(
                f"no frame timing survived the {warmup}-frame warm-up cut "
                f"({len(frame_us)} samples collected)"
            ),
            project=project,
            scene=scene,
        )
    p50 = percentile(body, 50)
    p95 = percentile(body, 95)
    return RenderCost(
        measured_on=measured_on,
        mode=mode.value,
        resolution=f"{resolution[0]}x{resolution[1]}",
        ms_per_frame_p50=p50,
        ms_per_frame_p95=p95,
        route_op_cap=route_op_cap(p50),
        l5_eligible=p50 <= L5_MS_PER_FRAME_MAX,
        gate_wall_timeout_s=gate_wall_timeout(p95),
        measured=True,
        detail=(
            f"{len(body)} frames after a {warmup}-frame warm-up: "
            f"p50 {p50:.1f} ms, p95 {p95:.1f} ms"
        ),
        samples=len(body),
        ms_per_frame_mean=sum(body) / len(body),
        ms_per_frame_max=max(body),
        project=project,
        scene=scene,
    )


def measure_render_cost(
    project_dir: str | os.PathLike[str],
    *,
    mode: RenderMode = RenderMode.X_CPU,
    scratch: str | os.PathLike[str] | None = None,
    resolution: tuple[int, int] = DEFAULT_RESOLUTION,
    warmup: int = WARMUP_FRAMES,
    frames: int = MEASURE_FRAMES,
    out_dir: str | os.PathLike[str] | None = None,
    prepared: bool = False,
    timeout: float | None = None,
) -> RenderCost:


    res_str = f"{resolution[0]}x{resolution[1]}"
    src = Path(project_dir)

    choice = first_playable_level(src)
    if not choice.ok:
        return RenderCost(
            measured_on="gb_levels.json[0]",
            mode=mode.value,
            resolution=res_str,
            ms_per_frame_p50=0.0,
            ms_per_frame_p95=0.0,
            route_op_cap=0,
            l5_eligible=False,
            gate_wall_timeout_s=0,
            measured=False,
            detail=choice.detail,
            project=src.name,
        )

    if prepared:
        work = src
    else:
        prep = prepare_scratch(src, scratch)
        if not prep.ok:
            return RenderCost(
                measured_on="gb_levels.json[0]",
                mode=mode.value,
                resolution=res_str,
                ms_per_frame_p50=0.0,
                ms_per_frame_p95=0.0,
                route_op_cap=0,
                l5_eligible=False,
                gate_wall_timeout_s=0,
                measured=False,
                detail=f"could not prepare a scratch copy: {prep.to_dict()}",
                project=src.name,
                scene=choice.scene,
            )
        work = prep.path

    total = warmup + frames
    out = Path(out_dir) if out_dir else Path(work) / "_gb_render_cost"
    out.mkdir(parents=True, exist_ok=True)
    cmd = build_command(
        mode,
        work,
        scene=choice.scene,
        quit_after=total + 120,
        resolution=resolution,
        user_args=[
            "--gb-capture-out",
            str(out),
            "--gb-capture-quit-after",
            str(total),
            "--gb-capture-timing",
            str(total + 10),
        ],
    )


    deadline = timeout if timeout is not None else max(300.0, total * 0.5 + 120)
    result = run(cmd, cwd=work, timeout=deadline, env=build_env(mode))

    manifest_path = out / "capture_manifest.json"
    if not manifest_path.is_file():
        tail = result.log.strip().splitlines()[-1][:200] if result.log.strip() else "no output"
        return RenderCost(
            measured_on="gb_levels.json[0]",
            mode=mode.value,
            resolution=res_str,
            ms_per_frame_p50=0.0,
            ms_per_frame_p95=0.0,
            route_op_cap=0,
            l5_eligible=False,
            gate_wall_timeout_s=0,
            measured=False,
            detail=(
                f"the timing run produced no manifest (rc={result.returncode}, "
                f"timed_out={result.timed_out}); last log line: {tail}"
            ),
            project=src.name,
            scene=choice.scene,
            seconds_wall=result.seconds,
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
    if manifest.get("refused"):
        return RenderCost(
            measured_on="gb_levels.json[0]",
            mode=mode.value,
            resolution=res_str,
            ms_per_frame_p50=0.0,
            ms_per_frame_p95=0.0,
            route_op_cap=0,
            l5_eligible=False,
            gate_wall_timeout_s=0,
            measured=False,
            detail="the probe refused: " + str(manifest.get("refusal_reason", ""))[:300],
            display_server=str(manifest.get("display_server", "")),
            frame_post_draw_count_2s=int(manifest.get("frame_post_draw_count_2s", -1)),
            project=src.name,
            scene=choice.scene,
            seconds_wall=result.seconds,
        )


    ran_scene = str(manifest.get("scene_file", ""))
    if ran_scene and ran_scene != choice.scene:
        return RenderCost(
            measured_on="gb_levels.json[0]",
            mode=mode.value,
            resolution=res_str,
            ms_per_frame_p50=0.0,
            ms_per_frame_p95=0.0,
            route_op_cap=0,
            l5_eligible=False,
            gate_wall_timeout_s=0,
            measured=False,
            detail=(
                f"asked for {choice.scene} and the run was on {ran_scene}; "
                "refusing to report a per-frame cost measured on a scene "
                "nobody asked for (this is the silent main_scene fallback)"
            ),
            display_server=str(manifest.get("display_server", "")),
            project=src.name,
            scene=ran_scene,
            seconds_wall=result.seconds,
        )

    cost = cost_from_samples(
        manifest.get("frame_us", []),
        warmup=warmup,
        mode=mode,
        resolution=resolution,
        measured_on="gb_levels.json[0]",
        project=src.name,
        scene=choice.scene,
    )
    cost.display_server = str(manifest.get("display_server", ""))
    cost.frame_post_draw_count_2s = int(manifest.get("frame_post_draw_count_2s", -1))
    cost.seconds_wall = result.seconds
    return cost


def route_budget(cost: RenderCost, frames: int) -> dict[str, Any]:

    return {
        "frames": frames,
        "expected_s": round(frames * cost.ms_per_frame_p50 / 1000.0, 1),
        "wall_timeout_s": wall_timeout_s(frames, cost.ms_per_frame_p95),
        "mode": cost.mode,
    }
