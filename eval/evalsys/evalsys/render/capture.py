


from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from evalsys.probe.inject import prepare_scratch
from evalsys.render.modes import (
    DEFAULT_RESOLUTION,
    RenderMode,
    build_command,
    build_env,
    run,
    select_mode,
    timeout_item,
    wall_timeout_s,
)
from evalsys.verdict import (
    Attribution,
    Item,
    Verdict,
    failed,
    inconclusive,
    passed,
    skipped,
)


SETTLE_FRAMES = 10


BOOT_FRAMES = 30


@dataclass
class CapturePoint:


    id: str
    frame: int
    level: str
    purpose: str = ""
    inject_state: dict[str, Any] = field(default_factory=dict)
    anchor_camera: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CapturePoint":
        return cls(
            id=str(d["id"]),
            frame=int(d.get("frame", 0)),
            level=str(d.get("level", "")),
            purpose=str(d.get("purpose", "")),
            inject_state=dict(d.get("inject_state") or {}),
            anchor_camera=str(d.get("anchor_camera", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_capture_points(path: str | os.PathLike[str]) -> list[CapturePoint]:

    raw = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    items = raw.get("points", []) if isinstance(raw, dict) else raw
    return [CapturePoint.from_dict(d) for d in items]


def write_capture_points(
    points: Sequence[CapturePoint], path: str | os.PathLike[str]
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"points": [p.to_dict() for p in points]}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return out


@dataclass
class Shot:


    point_id: str
    ok: bool
    path: str = ""
    bytes: int = 0
    resolution: str = ""
    frame: int = 0
    detail: str = ""
    inject_applied: list[str] = field(default_factory=list)
    inject_unapplied: list[str] = field(default_factory=list)
    refused: bool = False
    refusal_reason: str = ""
    display_server: str = ""


    frame_post_draw_count: int = -1
    frames_drawn: int = 0
    requested_level: str = ""
    actual_scene: str = ""
    wrong_scene: bool = False
    anchor_camera: str = ""
    anchor_camera_applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_item(self, weight: float = 1.0, prefix: str = "render/shot") -> Item:


        id = f"{prefix}/{self.point_id}"
        ev = self.to_dict()
        if self.ok:
            return passed(id, weight=weight, detail=self.detail, evidence=ev)
        if self.refused:
            return inconclusive(
                id,
                weight=weight,
                detail=self.detail,
                attribution=Attribution.HARNESS,
                evidence=ev,
            )
        return skipped(id, weight=weight, detail=self.detail, evidence=ev)


@dataclass
class CaptureRun:


    strategy: str
    mode: str
    out_dir: str
    shots: list[Shot] = field(default_factory=list)
    seconds: float = 0.0
    detail: str = ""

    @property
    def ok_count(self) -> int:
        return sum(1 for s in self.shots if s.ok)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "mode": self.mode,
            "out_dir": self.out_dir,
            "requested": len(self.shots),
            "captured": self.ok_count,
            "seconds": round(self.seconds, 2),
            "detail": self.detail,
            "shots": [s.to_dict() for s in self.shots],
            "png_json_ratio": png_json_ratio(self.out_dir).to_dict(),
        }

    def items(self, weight: float = 1.0) -> list[Item]:
        return [s.as_item(weight=weight) for s in self.shots]

    def write(self, path: str | os.PathLike[str] | None = None) -> Path:
        out = Path(path) if path else Path(self.out_dir) / "capture_run.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return out


@dataclass
class PngJsonRatio:


    root: str
    png: int
    json: int
    ratio: float
    ok: bool
    detail: str


    video: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_item(self, id: str = "render/png_json_ratio", weight: float = 1.0) -> Item:
        if self.ok:
            return passed(id, weight=weight, detail=self.detail, evidence=self.to_dict())
        return failed(
            id,
            weight=weight,
            detail=self.detail,
            attribution=Attribution.UNATTRIBUTABLE,
            evidence=self.to_dict(),
        )


def png_json_ratio(root: str | os.PathLike[str]) -> PngJsonRatio:


    base = Path(root)
    png = 0
    js = 0
    vid = 0
    for dirpath, _dirs, names in os.walk(base):
        for n in names:
            low = n.lower()
            if low.endswith(".png"):
                png += 1
            elif low.endswith(".json"):
                js += 1
            elif low.endswith((".avi", ".mp4", ".mov", ".webm", ".gif")):
                vid += 1
    ratio = (png / js) if js else float(png)
    if png == 0 and js == 0 and vid == 0:
        return PngJsonRatio(str(base), 0, 0, 0.0, False, f"nothing at all under {base}", 0)
    if png == 0:
        note = (
            f" ({vid} video files are present, so something was rendered -- but "
            "no still frame was, and stills are what a frame comparison needs)"
            if vid
            else ""
        )
        return PngJsonRatio(
            str(base),
            png,
            js,
            0.0,
            False,
            f"{js} JSON files and 0 PNGs under {base}: this run produced reports "
            f"about pixels that were never captured{note}",
            vid,
        )
    return PngJsonRatio(
        str(base),
        png,
        js,
        ratio,
        True,
        f"{png} PNG / {js} JSON under {base} (ratio {ratio:.2f})"
        + (f", plus {vid} video files" if vid else ""),
        vid,
    )


def _read_manifest(out: Path) -> dict[str, Any]:
    p = out / "capture_manifest.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return {}


def _shot_from_manifest(
    point: CapturePoint, out: Path, manifest: dict[str, Any], run_detail: str
) -> Shot:
    if not manifest:
        return Shot(
            point.id,
            False,
            detail=f"no capture manifest was written for this point; {run_detail}",
        )
    if manifest.get("refused"):
        return Shot(
            point.id,
            False,
            refused=True,
            refusal_reason=str(manifest.get("refusal_reason", "")),
            display_server=str(manifest.get("display_server", "")),
            frame_post_draw_count=int(manifest.get("frame_post_draw_count_2s", -1)),
            detail="the probe refused to capture: "
            + str(manifest.get("refusal_reason", ""))[:240],
        )
    caps = manifest.get("captures") or []
    if not caps:
        return Shot(
            point.id,
            False,
            display_server=str(manifest.get("display_server", "")),
            frame_post_draw_count=int(manifest.get("frame_post_draw_count_2s", -1)),
            frames_drawn=int(manifest.get("frame_post_draw_total", 0)),
            inject_applied=list(manifest.get("inject_state_applied") or []),
            inject_unapplied=list(manifest.get("inject_state_unapplied") or []),
            detail=(
                "the run drew "
                f"{manifest.get('frame_post_draw_total', 0)} frames but captured "
                f"none of {manifest.get('requested_frames')}; "
                + "; ".join(str(e) for e in (manifest.get("errors") or []))[:240]
            ),
        )
    c = caps[-1]
    ran = str(manifest.get("scene_file", ""))
    wrong = bool(point.level) and bool(ran) and ran != point.level
    return Shot(
        point.id,
        not wrong,
        path=str(c.get("path", "")),
        bytes=int(c.get("bytes", 0)),
        resolution=str(c.get("resolution", "")),
        frame=int(c.get("frame", 0)),
        display_server=str(manifest.get("display_server", "")),
        frame_post_draw_count=int(manifest.get("frame_post_draw_count_2s", -1)),
        frames_drawn=int(manifest.get("frame_post_draw_total", 0)),
        inject_applied=list(manifest.get("inject_state_applied") or []),
        inject_unapplied=list(manifest.get("inject_state_unapplied") or []),
        requested_level=point.level,
        actual_scene=ran,
        wrong_scene=wrong,
        anchor_camera=str(manifest.get("anchor_camera", "")),
        anchor_camera_applied=bool(manifest.get("anchor_camera_applied", False)),
        detail=(
            (
                f"a PNG exists but it is of {ran}, not the requested "
                f"{point.level}: the engine fell back to main_scene or the game "
                "transitioned away. This frame is not evidence about the "
                "requested level."
            )
            if wrong
            else (
                f"{c.get('resolution')} PNG, {c.get('bytes')} bytes, at drawn frame "
                f"{c.get('frame')} of {ran or point.level}"
                + (
                    f"; state keys not applied: "
                    f"{manifest.get('inject_state_unapplied')}"
                    if manifest.get("inject_state_unapplied")
                    else ""
                )
            )
        ),
    )


def capture_b2(
    scratch: str | os.PathLike[str],
    points: Sequence[CapturePoint],
    out_dir: str | os.PathLike[str],
    *,
    mode: RenderMode | None = None,
    ms_per_frame_p95: float = 20.0,
    resolution: tuple[int, int] = DEFAULT_RESOLUTION,
    settle_frames: int = SETTLE_FRAMES,
    boot_frames: int = BOOT_FRAMES,
    interface_args: Sequence[str] = (),
) -> CaptureRun:


    import time

    started = time.time()
    root = Path(scratch)
    out_root = Path(out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    chosen = mode or select_mode(
        frames_needed=(boot_frames + settle_frames) * max(1, len(points)),
        ms_per_frame=ms_per_frame_p95,
        needs_pixels=True,
    )
    shots: list[Shot] = []

    for point in points:
        pdir = out_root / point.id
        pdir.mkdir(parents=True, exist_ok=True)
        shoot_at = boot_frames + settle_frames
        probe_args = [
            "--gb-capture-out",
            str(pdir),
            "--gb-capture-frames",
            str(shoot_at),
            "--gb-capture-quit-after",
            str(shoot_at + 30),
        ]
        probe_args.extend(interface_args)
        if point.inject_state:
            state_path = pdir / "inject_state.json"
            state_path.write_text(
                json.dumps(point.inject_state, indent=2, sort_keys=True), encoding="utf-8"
            )
            probe_args += ["--gb-inject-state", str(state_path)]
        if point.anchor_camera:
            probe_args += ["--gb-anchor-camera", point.anchor_camera]

        cmd = build_command(
            chosen,
            root,
            scene=point.level or None,
            quit_after=shoot_at + 60,
            resolution=resolution,
            user_args=probe_args,
        )
        budget = wall_timeout_s(shoot_at + 60, ms_per_frame_p95)
        result = run(cmd, cwd=root, timeout=budget, env=build_env(chosen))
        manifest = _read_manifest(pdir)
        shot = _shot_from_manifest(
            point,
            pdir,
            manifest,
            f"rc={result.returncode}, timed_out={result.timed_out}",
        )
        if result.timed_out and not shot.ok:
            shot.refused = True
            shot.detail = (
                f"the capture run for {point.id} hit the {budget}s deadline; "
                "a deadline we set is our gap, not the submission's"
            )
        shots.append(shot)

    return CaptureRun(
        strategy="B2",
        mode=chosen.value,
        out_dir=str(out_root),
        shots=shots,
        seconds=time.time() - started,
        detail=(
            f"B2 inject-and-shoot: {sum(1 for s in shots if s.ok)}/{len(shots)} "
            f"points captured in {chosen.value} at {resolution[0]}x{resolution[1]}, "
            f"{boot_frames}+{settle_frames} frames per point"
        ),
    )


def capture_b1(
    scratch: str | os.PathLike[str],
    points: Sequence[CapturePoint],
    out_dir: str | os.PathLike[str],
    *,
    mode: RenderMode | None = None,
    ms_per_frame_p95: float = 20.0,
    resolution: tuple[int, int] = DEFAULT_RESOLUTION,
    level: str | None = None,
    extra_args: Sequence[str] = (),
    interface_args: Sequence[str] = (),
    env_overrides: Mapping[str, str] | None = None,
    allow_scene_changes: bool = False,
    fixed_fps: int | None = None,
) -> CaptureRun:


    import time

    started = time.time()
    root = Path(scratch)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    levels = {p.level for p in points if p.level}
    route_level = level or (sorted(levels)[0] if levels else None)
    on_route = [p for p in points if not p.level or p.level == route_level]
    off_route = [p for p in points if p not in on_route]

    frames = sorted({p.frame for p in on_route})
    last = max(frames) if frames else 0
    chosen = mode or select_mode(
        frames_needed=last, ms_per_frame=ms_per_frame_p95, needs_pixels=True
    )
    cmd = build_command(
        chosen,
        root,
        scene=route_level,
        quit_after=last + 120,
        resolution=resolution,
        fixed_fps=fixed_fps,
        user_args=[
            "--gb-capture-out",
            str(out),
            "--gb-capture-frames",
            ",".join(str(f) for f in frames),
            "--gb-capture-quit-after",
            str(last + 60),
            *extra_args,
            *interface_args,
        ],
    )
    budget = wall_timeout_s(last + 120, ms_per_frame_p95)
    capture_env = build_env(chosen)
    capture_env.update(dict(env_overrides or {}))
    result = run(cmd, cwd=root, timeout=budget, env=capture_env)
    manifest = _read_manifest(out)
    by_frame = {int(c["frame"]): c for c in (manifest.get("captures") or [])}
    ran_scene = str(manifest.get("scene_file", ""))

    wrong_scene = (
        not allow_scene_changes
        and bool(route_level)
        and bool(ran_scene)
        and ran_scene != route_level
    )

    shots: list[Shot] = []
    for p in on_route:
        c = by_frame.get(p.frame)
        if c is None:
            shots.append(
                Shot(
                    p.id,
                    False,
                    frame=p.frame,
                    refused=bool(manifest.get("refused")) or result.timed_out,
                    refusal_reason=str(manifest.get("refusal_reason", "")),
                    display_server=str(manifest.get("display_server", "")),
                    frame_post_draw_count=int(
                        manifest.get("frame_post_draw_count_2s", -1)
                    ),
                    detail=(
                        f"frame {p.frame} was never reached or never drawn "
                        f"(run drew {manifest.get('frame_post_draw_total', 0)}, "
                        f"rc={result.returncode}, timed_out={result.timed_out})"
                    ),
                )
            )
            continue
        shots.append(
            Shot(
                p.id,
                not wrong_scene,
                path=str(c.get("path", "")),
                bytes=int(c.get("bytes", 0)),
                resolution=str(c.get("resolution", "")),
                frame=int(c.get("frame", 0)),
                display_server=str(manifest.get("display_server", "")),
                frame_post_draw_count=int(manifest.get("frame_post_draw_count_2s", -1)),
                frames_drawn=int(manifest.get("frame_post_draw_total", 0)),
                requested_level=route_level or "",
                actual_scene=ran_scene,
                wrong_scene=wrong_scene,
                detail=(
                    f"a PNG exists but the run was on {ran_scene}, not the "
                    f"requested {route_level}"
                    if wrong_scene
                    else f"{c.get('resolution')} PNG, {c.get('bytes')} bytes, "
                    f"replayed to frame {c.get('frame')} of {ran_scene}"
                ),
            )
        )
    for p in off_route:
        shots.append(
            Shot(
                p.id,
                False,
                frame=p.frame,
                detail=f"point declares level {p.level!r}, this replay ran "
                f"{route_level!r}; it needs its own B1 run",
            )
        )

    return CaptureRun(
        strategy="B1",
        mode=chosen.value,
        out_dir=str(out),
        shots=shots,
        seconds=time.time() - started,
        detail=(
            f"B1 full replay of {route_level} to frame {last}: "
            f"{sum(1 for s in shots if s.ok)}/{len(shots)} points captured"
        ),
    )


def capture(
    project_dir: str | os.PathLike[str],
    points: Sequence[CapturePoint],
    out_dir: str | os.PathLike[str],
    *,
    strategy: str = "B2",
    scratch: str | os.PathLike[str] | None = None,
    interface: Any = None,
    **kw: Any,
) -> CaptureRun:


    if interface is None:
        from ..interface import load_submission_interface

        interface = load_submission_interface(project_dir)
    prep = prepare_scratch(project_dir, scratch)
    if not prep.ok:
        return CaptureRun(
            strategy=strategy,
            mode="",
            out_dir=str(out_dir),
            shots=[
                Shot(p.id, False, refused=True, detail="scratch preparation failed")
                for p in points
            ],
            detail=json.dumps(prep.to_dict())[:400],
        )
    from ..interface.runtime import runtime_args, write_runtime_interface

    interface_path = write_runtime_interface(interface, prep.path.parent / "_interface_io")
    fn = capture_b1 if strategy.upper() == "B1" else capture_b2
    return fn(
        prep.path, points, out_dir,
        interface_args=runtime_args(interface, interface_path), **kw
    )
