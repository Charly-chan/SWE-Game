


from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from ..routes.agent import Op, ScriptedAgent
from ..routes.budget import derive
from ..routes.schema import Milestone
from ..scard.replay import ReplayFilm, film_from_movie
from .engine import fresh_user_data, godot_available, prepare_session, self_clear_route


FILM_MS_PER_FRAME = 60.0

FILM_TAIL_FRAMES = 120
FILM_FIXED_FPS = 60


def _completed_import_errors(preparation: Mapping[str, Any]) -> Sequence[Any]:

    attempts = preparation.get("attempts") or []
    if not attempts:
        return ()
    last = attempts[-1]
    imported = last.get("import") or {}
    if not ((last.get("scratch") or {}).get("ok") is True
            and (last.get("inject") or {}).get("ok") is True
            and imported.get("ok") is False
            and imported.get("timed_out") is False
            and isinstance(imported.get("returncode"), int)
            and imported["returncode"] >= 0):
        return ()
    return imported.get("errors") or ()


def _missing_import_scene(project: Path, preparation: Mapping[str, Any]) -> str | None:

    for error in _completed_import_errors(preparation):
        match = re.fullmatch(r"ERROR: Cannot open file '(res://[^']+\.tscn)'\.", str(error))
        if match and not (project / match[1].removeprefix("res://")).is_file():
            return match[1]
    return None


def _invalid_import_scene(project: Path, preparation: Mapping[str, Any]) -> str | None:

    for error in _completed_import_errors(preparation):
        match = re.fullmatch(r"ERROR: (res://.+\.tscn):\d+ - Parse Error: .+", str(error))
        if match is None:
            match = re.fullmatch(
                r"ERROR: Parse Error: .+ \[Resource file (res://.+\.tscn):\d+\]", str(error)
            )
        if match and (project / match[1].removeprefix("res://")).is_file():
            return match[1]
    return None


@contextmanager
def _engine_extra_args(extra: str) -> Iterator[None]:
    previous = os.environ.get("GB_GODOT_EXTRA_ARGS")
    os.environ["GB_GODOT_EXTRA_ARGS"] = " ".join(
        part for part in [previous or "", extra] if part
    ).strip()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("GB_GODOT_EXTRA_ARGS", None)
        else:
            os.environ["GB_GODOT_EXTRA_ARGS"] = previous


def film_submission_replay(
    project: Path,
    frames_dir: Path,
    *,
    interface: Any,
    ops: Sequence[Op],
    predicate: str,
    milestones: Sequence[Milestone] = (),
    witness: Mapping[str, Any] | None = None,
    session: Any = None,
    feature_demo: bool = False,
) -> ReplayFilm:

    from ..routes.runner import RouteSession

    frames_dir.mkdir(parents=True, exist_ok=True)
    if godot_available() is None:
        return ReplayFilm(directory=str(frames_dir), error="no Godot binary (GODOT_BIN unset)")
    if not ops:
        return ReplayFilm(directory=str(frames_dir), error="the submission has no ops tape to film")
    if session is None:
        scene = interface.levels[0].scene if interface.levels else ""
        if (scene.startswith("res://")
                and not (interface.project_root / scene.removeprefix("res://")).is_file()):
            return ReplayFilm(
                directory=str(frames_dir), stop_reason="scene_missing",
                error=f"the submission's first declared scene is missing: {scene}",
            )
        session = RouteSession(project, interface=interface)
        record: dict[str, Any] = {}
        if not prepare_session(session, record):
            preparation = record.get("prepare") or {}
            missing = _missing_import_scene(interface.project_root, preparation)
            invalid = _invalid_import_scene(interface.project_root, preparation)
            return ReplayFilm(
                directory=str(frames_dir),
                stop_reason="scene_missing" if missing else "scene_load_failed" if invalid else "",
                error="route scratch could not be prepared: "
                      + str(record.get("prepare", {}).get("driver_error") or "unknown"),
            )
    frames = sum(op.frames for op in ops) + FILM_TAIL_FRAMES
    route = self_clear_route(
        task_id=project.name, ops=ops, predicate=predicate, frames=frames, milestones=milestones,
        feature_demo=feature_demo,
    )
    budget = derive(
        5, FILM_MS_PER_FRAME, needs_pixels=True,
        declared_steps=len(ops), declared_frames=frames,
    )
    movie = frames_dir / "raw_replay.avi"
    if movie.exists():
        movie.unlink()
    started = time.monotonic()
    previous_fps = os.environ.get("GB_ROUTE_FIXED_FPS")
    os.environ["GB_ROUTE_FIXED_FPS"] = str(FILM_FIXED_FPS)
    try:
        with _engine_extra_args(f"--write-movie {movie}"), (fresh_user_data() if feature_demo else nullcontext()):
            reading = session.run(
                route,
                ScriptedAgent(ops, name="submission_ops_filmed"),
                budget,
                run_tag="taskgen_replay_film",
                override_ops=ops,
                expose_cmdline_plan=False,
            )
    finally:
        if previous_fps is None:
            os.environ.pop("GB_ROUTE_FIXED_FPS", None)
        else:
            os.environ["GB_ROUTE_FIXED_FPS"] = previous_fps
    wall = time.monotonic() - started
    film = film_from_movie(
        movie,
        frames_dir,
        fps=FILM_FIXED_FPS,
        goal_frame_index=int(getattr(reading, "goal_frame", -1) or -1),
        stop_reason=str(getattr(reading, "stop_reason", "") or ""),
        witness=witness,
        wall_seconds=round(wall, 1),


        sample_interval_s=0.25 if feature_demo else 2.0,
    )
    film.notes.insert(
        0,
        f"filmed run: {getattr(reading, 'frames', 0)} trace rows, stop_reason="
        f"{getattr(reading, 'stop_reason', '')!r}, goal_frame={getattr(reading, 'goal_frame', -1)}, "
        f"mode={getattr(reading, 'mode', '')}, {wall:.1f} s wall",
    )
    return film


def make_replay_filmer(
    project: Path,
    *,
    interface: Any,
    ops: Sequence[Op],
    predicate: str,
    milestones: Sequence[Milestone] = (),
    witness: Mapping[str, Any] | None = None,
) -> Callable[[Path], ReplayFilm]:


    def _film(frames_dir: Path) -> ReplayFilm:
        return film_submission_replay(
            project, Path(frames_dir), interface=interface, ops=ops, predicate=predicate,
            milestones=milestones, witness=witness,
        )

    return _film


__all__ = ["FILM_FIXED_FPS", "FILM_MS_PER_FRAME", "film_submission_replay", "make_replay_filmer"]
