


from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw
from ..report.terminology import PERCEPTUAL_ASSESSMENT, display_terms

from .rubric import (
    LEVEL_CREDITS,
    RUBRIC_VERSION,
    credit_to_level,
    judging_rules,
    level_to_credit,
    rubric_markdown_sha256,
)


REPLAY_RUBRIC_VERSION = "2026-09-09.replay2"


FEATURE_DEMO_RUBRIC_VERSION = "2026-09-09.feature-demo2"
REPLAY_READING_ID = "S4_replay"


SAMPLE_INTERVAL_S = 2.0
MAX_SAMPLED_FRAMES = 24


FULL_FRAMES_TO_JUDGE = 3
CONTACT_SHEET_COLUMNS = 6
CONTACT_THUMB = (320, 180)

REPLAY_FRAME_SOURCE = "evalsys.scard.replay"


@dataclass(frozen=True)
class ReplayBand:
    credit: float
    label: str
    descriptor: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplayCriterion:
    id: str
    question: str
    bands: tuple[ReplayBand, ...]

    def band_for(self, credit: float) -> ReplayBand:
        for band in self.bands:
            if credit >= band.credit - 1e-9:
                return band
        return self.bands[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "bands": [b.to_dict() for b in self.bands],
        }


def _bands(top: str, good: str, mid: str, poor: str, none: str) -> tuple[ReplayBand, ...]:
    return (
        ReplayBand(1.00, "clear", top),
        ReplayBand(0.75, "mostly", good),
        ReplayBand(0.50, "partly", mid),
        ReplayBand(0.25, "barely", poor),
        ReplayBand(0.00, "absent", none),
    )


REPLAY_CRITERIA: tuple[ReplayCriterion, ...] = (
    ReplayCriterion(
        "motion_legible",
        "Across the sampled frames, can a viewer tell what is moving and where "
        "the player is going? (camera framing, player identifiable, scene "
        "changes readable between consecutive samples)",
        _bands(
            "The player entity and its direction of travel are identifiable in every "
            "sampled frame; consecutive frames read as one continuous run.",
            "Identifiable in nearly every frame; one or two samples are ambiguous "
            "(camera cut, occlusion, transition screen).",
            "The player is identifiable in about half the frames; motion between "
            "samples is often not inferable.",
            "The player can rarely be located; frames read as unrelated stills.",
            "Nothing legible moves: blank, frozen or identical frames throughout.",
        ),
    ),
    ReplayCriterion(
        "action_feedback_visible",
        "Is there visible feedback to the player's actions -- a jump, a shot, a "
        "pickup, damage taken -- as a state change between samples (HUD value, "
        "effect, removed or added entity, animation pose)?",
        _bands(
            "Several distinct action consequences are visible (counters change, "
            "entities disappear, effects appear) and each can be attributed to an action.",
            "Feedback is visible for most action kinds; one kind leaves no trace in "
            "the frames.",
            "Some state changes are visible but most actions leave no visible trace.",
            "A single visible change (e.g. one counter) over the whole run.",
            "No visible consequence of any action; the frames could be an idle run.",
        ),
    ),
    ReplayCriterion(
        "progression_visible",
        "Does the run visibly progress -- new areas, a progress or level "
        "indicator advancing, distinct level scenes appearing in order?",
        _bands(
            "Progression is evident both spatially (new content) and on the HUD "
            "(progress/level/score advancing) across the run.",
            "Progression is evident on one axis (either new content or an advancing "
            "indicator).",
            "Some sign of advance but long stretches look identical.",
            "Nearly indistinguishable frames with a single late change.",
            "No visible progression at all.",
        ),
    ),
    ReplayCriterion(
        "ending_shown",
        "Is a clear end state shown in the final frames (the GOAL or END tile): "
        "an explicit end-of-run screen -- victory, results, finish, or a defeat/"
        "game-over screen when the run ended in failure -- that communicates the "
        "outcome to the player? (Whether the outcome was the intended one is "
        "the witness's question, not this one.)",
        _bands(
            "An explicit ending screen or completion state is shown and reads as "
            "the outcome of the run (e.g. results, 'FINISH', victory or defeat text).",
            "An ending is shown but is generic or barely distinguishable from a menu.",
            "The run visibly stops (freeze, fade) without an explicit ending screen.",
            "Only an indirect hint of an ending (e.g. HUD progress at maximum).",
            "The final frames show ordinary gameplay or nothing; no ending is visible.",
        ),
    ),
)
CRITERIA_BY_ID = {c.id: c for c in REPLAY_CRITERIA}


def replay_rubric_document() -> dict[str, Any]:
    return {
        "id": REPLAY_READING_ID,
        "rubric_version": REPLAY_RUBRIC_VERSION,
        "weight_in_headline": 0.0,
        "criteria": [c.to_dict() for c in REPLAY_CRITERIA],
        "not_s4": (
            "S4 feel stays human-only (rubric.S4); this reading asks only what a "
            "sampled sequence of evaluator-captured frames can show"
        ),
    }


@dataclass
class ReplayFilm:


    directory: str
    frames: list[str] = field(default_factory=list)
    frame_times_s: list[float] = field(default_factory=list)
    goal_frame: str = ""
    goal_time_s: float | None = None
    contact_sheet: str = ""
    mp4: str = ""
    fps: int = 60
    sample_interval_s: float = SAMPLE_INTERVAL_S
    movie_frames: int = 0
    duration_s: float = 0.0
    resolution: str = ""
    stop_reason: str = ""
    goal_frame_index: int = -1
    witness_stop_reason: str = ""
    witness_goal_frame: int = -1
    wall_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.frames) and not self.error

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sampled_frames"] = len(self.frames)
        data["ok"] = self.ok
        return data


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


def movie_frame_count(path: Path) -> tuple[int, float]:

    probe = ffprobe_path()
    if probe is None or not path.is_file():
        return 0, 0.0
    try:
        out = subprocess.run(
            [probe, "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=nb_read_frames,duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=600, check=False,
        ).stdout
        data = json.loads(out or "{}")
        stream = (data.get("streams") or [{}])[0]
        frames = int(stream.get("nb_read_frames") or 0)
        duration = float(stream.get("duration") or 0.0)
        return frames, duration
    except (subprocess.SubprocessError, ValueError, OSError):
        return 0, 0.0


def sample_times(duration_s: float, interval_s: float = SAMPLE_INTERVAL_S,
                 cap: int = MAX_SAMPLED_FRAMES) -> tuple[list[float], float]:

    if duration_s <= 0:
        return [], interval_s
    interval = max(interval_s, math.ceil(duration_s / cap * 2) / 2) if duration_s / interval_s > cap else interval_s
    times = [round(t, 3) for t in _frange(0.0, duration_s, interval)]
    return times[:cap], interval


def _frange(start: float, stop: float, step: float) -> list[float]:
    out: list[float] = []
    t = start
    while t < stop - 1e-9:
        out.append(t)
        t += step
    return out


def extract_frame(movie: Path, at_s: float, dest: Path) -> bool:
    ff = ffmpeg_path()
    if ff is None:
        return False
    proc = subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, at_s):.3f}",
         "-i", str(movie), "-frames:v", "1", str(dest)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def build_contact_sheet(frames: Sequence[str], labels: Sequence[str], dest: Path,
                        columns: int = CONTACT_SHEET_COLUMNS,
                        thumb: tuple[int, int] = CONTACT_THUMB) -> str:


    if not frames:
        return ""
    rows = math.ceil(len(frames) / columns)
    tw, th = thumb
    label_h = 18
    sheet = Image.new("RGB", (columns * tw, rows * (th + label_h)), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    for index, (path, label) in enumerate(zip(frames, labels)):
        col, row = index % columns, index // columns
        x, y = col * tw, row * (th + label_h)
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((tw, th))
                sheet.paste(im, (x + (tw - im.width) // 2, y + label_h + (th - im.height) // 2))
        except OSError:
            draw.rectangle([x, y + label_h, x + tw, y + label_h + th], outline=(200, 40, 40))
        draw.text((x + 4, y + 2), label, fill=(240, 240, 240))
    sheet.save(dest)
    return str(dest)


def encode_mp4(movie: Path, dest: Path, *, width: int = 640, fps: int = 30) -> str:
    ff = ffmpeg_path()
    if ff is None:
        return ""
    proc = subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(movie),
         "-vf", f"scale={width}:-2", "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "28", "-pix_fmt", "yuv420p", "-an", str(dest)],
        capture_output=True, text=True, timeout=1800, check=False,
    )
    if proc.returncode != 0 or not dest.is_file():
        return ""
    return str(dest)


def film_from_movie(
    movie: Path,
    out_dir: Path,
    *,
    fps: int,
    goal_frame_index: int,
    stop_reason: str,
    witness: Mapping[str, Any] | None = None,
    wall_seconds: float = 0.0,
    keep_movie: bool = False,
    sample_interval_s: float = SAMPLE_INTERVAL_S,
) -> ReplayFilm:

    out_dir.mkdir(parents=True, exist_ok=True)
    film = ReplayFilm(
        directory=str(out_dir), fps=fps, stop_reason=stop_reason,
        goal_frame_index=goal_frame_index, wall_seconds=wall_seconds,
        witness_stop_reason=str((witness or {}).get("stop_reason") or ""),
        witness_goal_frame=int((witness or {}).get("goal_frame") or -1),
    )
    if ffmpeg_path() is None:
        film.error = "ffmpeg is not installed; frames cannot be extracted from the movie"
        return film
    if not movie.is_file() or movie.stat().st_size == 0:
        film.error = "Movie Maker wrote no video"
        return film
    frames, duration = movie_frame_count(movie)
    film.movie_frames = frames
    film.duration_s = duration if duration > 0 else (frames / fps if fps else 0.0)
    times, interval = sample_times(film.duration_s, interval_s=sample_interval_s)
    film.sample_interval_s = interval
    labels: list[str] = []
    for index, t in enumerate(times):
        dest = out_dir / f"replay_t{int(round(t * 1000)):07d}ms.png"
        if extract_frame(movie, t, dest):
            film.frames.append(str(dest))
            film.frame_times_s.append(t)
            labels.append(f"#{index} t={t:.1f}s")
    last_t = max(0.0, film.duration_s - 1.0 / fps) if fps > 0 else 0.0
    if goal_frame_index >= 0 and fps > 0:


        goal_t = min(max(0.0, (goal_frame_index + 3) / fps), last_t)
        dest = out_dir / "replay_goal.png"
        if extract_frame(movie, goal_t, dest):
            film.goal_frame = str(dest)
            film.goal_time_s = round(goal_t, 3)
    elif film.duration_s > 0:


        dest = out_dir / "replay_end.png"
        for back in (0.0, 0.1, 0.5, 1.0):
            end_t = max(0.0, last_t - back)
            if extract_frame(movie, end_t, dest):
                film.goal_frame = str(dest)
                film.goal_time_s = round(end_t, 3)
                film.notes.append("no goal frame: the final movie frame stands in as the end state")
                break
    if film.frames:
        with Image.open(film.frames[0]) as im:
            film.resolution = f"{im.width}x{im.height}"
        sheet_frames = list(film.frames) + ([film.goal_frame] if film.goal_frame else [])
        end_label = "GOAL" if goal_frame_index >= 0 else "END (no goal)"
        sheet_labels = labels + ([f"{end_label} t={film.goal_time_s:.1f}s"] if film.goal_frame else [])
        film.contact_sheet = build_contact_sheet(sheet_frames, sheet_labels, out_dir / "contact_sheet.png")
        film.mp4 = encode_mp4(movie, out_dir / "replay.mp4")
        if not film.mp4:
            film.notes.append("mp4 not written (ffmpeg encode failed)")
    else:
        film.error = "no frame could be extracted from the movie"
    if stop_reason and film.witness_stop_reason and stop_reason != film.witness_stop_reason:
        film.notes.append(
            f"filmed replay stopped with {stop_reason!r} while the headless witness stopped "
            f"with {film.witness_stop_reason!r}; the two runs diverged"
        )
    if not keep_movie:
        try:
            movie.unlink()
        except OSError:
            pass
    return film


@dataclass
class ReplayReading:
    id: str = REPLAY_READING_ID
    name: str = "replay_reading"
    rubric_version: str = REPLAY_RUBRIC_VERSION
    weight_in_headline: float = 0.0
    status: str = "not_filmed"
    criteria: dict[str, dict[str, Any]] = field(default_factory=dict)
    mean_credit: float | None = None
    judge: dict[str, Any] = field(default_factory=dict)
    film: dict[str, Any] = field(default_factory=dict)
    prompt_sha256: str = ""
    cost_note: str = ""
    detail: str = ""
    generated_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def build_replay_prompt(film: ReplayFilm, *, project: str, full_frames: Sequence[str],
                        context: Mapping[str, Any] | None = None) -> dict[str, Any]:

    feature_demo = bool((context or {}).get("feature_demo"))
    criteria = _replay_criteria(context)
    return {
        "task": (
            "You are shown frames the evaluator sampled while replaying, in the "
            "game engine, the clearing input tape a code-generation agent submitted "
            "for the game it built. Image 1 is a contact sheet of every sampled "
            "frame in time order (labels give sample index and time; the last "
            "tile labelled GOAL is the frame at which the run reached its goal; "
            "when the run did not reach a goal the last tile is labelled END and "
            "is the final frame of the run). The following images are full-resolution copies of "
            "some of those frames. Answer the listed criteria from what is visible "
            "only. Return a JSON object matching answer_format. "
            "Do not judge fun, art quality or resemblance to any other game."
        ),
        "scope": ("An independent partial feature demonstration, NOT a full-game clear. "
                  "Do not require an ending or whole-game progression. The description is a "
                  "submitter claim, not evidence; judge only what the frames show."
                  if feature_demo else "Whole-game replay witness"),
        "rubric_version": FEATURE_DEMO_RUBRIC_VERSION if feature_demo else REPLAY_RUBRIC_VERSION,
        "scard_rubric_version": RUBRIC_VERSION,
        "rubric_markdown_sha256": rubric_markdown_sha256(),
        "judging_rules": judging_rules(),
        "level_scale": {str(level): credit for level, credit in LEVEL_CREDITS.items()},
        "game": {"project": project, **dict(context or {})},
        "film": {
            "sampled_frames": len(film.frames),
            "sample_interval_s": film.sample_interval_s,
            "duration_s": round(film.duration_s, 2),
            "goal_frame_included": bool(film.goal_frame) and film.goal_frame_index >= 0,
            "end_frame_included": bool(film.goal_frame) and film.goal_frame_index < 0,
            "replay_stop_reason": film.stop_reason,
            "full_frames": [Path(p).name for p in full_frames],
        },
        "criteria": [c.to_dict() for c in criteria],
        "answer_format": {
            "<criterion id>": {
                "level": "integer 0-4 (band credit = level / 4)",
                "frames_cited": "list of contact-sheet tile indices or times that decided the level",
                "rationale": "one or two sentences citing frame indices or times",
                "confident": "true|false",
            },
            "summary": "one sentence on what the run shows",
        },
    }


def _replay_criteria(context: Mapping[str, Any] | None) -> tuple[ReplayCriterion, ...]:
    if not (context or {}).get("feature_demo"):
        return REPLAY_CRITERIA
    feedback = ReplayCriterion(
        "action_feedback_visible", "Is the outcome of the demonstrated action visibly communicated?",
        _bands("The demonstrated action has clear visible feedback and its outcome is unambiguous.",
               "The action's feedback is visible with a small ambiguity.",
               "The action has some feedback, but its outcome is unclear.",
               "Only an indirect hint of action feedback is visible.",
               "No visible feedback to the demonstrated action."),
    )
    return (REPLAY_CRITERIA[0], feedback, ReplayCriterion(
        "feature_visible", "Is the described feature visibly demonstrated, with its outcome shown?",
        _bands("The feature and its resulting state change are clearly visible.",
               "The feature is visible with a small gap in its outcome.",
               "Part of the feature is visible, but its outcome is ambiguous.",
               "Only an indirect hint of the described feature is visible.",
               "The described feature is not visible."),
    ))


def judge_replay(
    film: ReplayFilm,
    judge: Any,
    *,
    project: str,
    context: Mapping[str, Any] | None = None,
    criteria: Sequence[ReplayCriterion] | None = None,
    prompt: dict[str, Any] | None = None,
    reference_frames: Sequence[Any] = (),
) -> ReplayReading:


    reading = ReplayReading(film=film.to_dict())
    if (context or {}).get("feature_demo"):
        reading.rubric_version = FEATURE_DEMO_RUBRIC_VERSION
    if not film.ok:
        reading.status = "not_filmed"
        reading.detail = film.error or "the replay was not filmed"
        return reading
    from .judge import load_frame, assert_deliverable

    picks: list[str] = []
    if film.frames:
        picks.append(film.frames[0])
        if len(film.frames) > 2:
            picks.append(film.frames[len(film.frames) // 2])
    if film.goal_frame:
        picks.append(film.goal_frame)
    picks = list(dict.fromkeys(picks))[:FULL_FRAMES_TO_JUDGE]
    try:
        frames = [
            load_frame(p, source=REPLAY_FRAME_SOURCE, point_id=Path(p).stem) for p in picks
        ]
        assert_deliverable(frames)
        sheet = load_frame(
            film.contact_sheet, source=REPLAY_FRAME_SOURCE, point_id="contact_sheet",
            min_resolution=(320, 180),
        )
    except Exception as exc:
        reading.status = "unavailable"
        reading.detail = f"frames refused by the judge's provenance rules: {exc}"
        return reading

    criteria = tuple(criteria) if criteria is not None else _replay_criteria(context)
    prompt = dict(prompt) if prompt is not None else build_replay_prompt(
        film, project=project, full_frames=picks, context=context)
    if reference_frames:
        prompt["reference_images"] = [
            {"image": 2 + len(frames) + i, "id": frame.point_id}
            for i, frame in enumerate(reference_frames)
        ]
    reading.rubric_version = str(prompt.get("rubric_version") or reading.rubric_version)
    prompt_text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    reading.prompt_sha256 = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    model = str(getattr(judge, "model", "") or "")
    reading.judge = {
        "judge_id": f"{getattr(judge, 'judge_id', 'vlm')}+replay",
        "model": model,
        "endpoint": str(getattr(judge, "api_url", "") or ""),
        "images": 1 + len(frames) + len(reference_frames),
    }
    if not getattr(judge, "available", False):
        reading.status = "unavailable"
        reading.detail = "no API key for the S-card judge; the replay reading never ran"
        return reading

    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt_text}]
    for frame in [sheet, *frames, *reference_frames]:
        content.append({
            "type": "input_image",
            "image_url": f"data:image/png;base64,{_b64(frame.path)}",
            "detail": "high",
        })
    payload: dict[str, Any] = {
        "model": model,
        "store": False,
        "max_output_tokens": max(2048, 256 * len(criteria)),
        "input": [{"role": "user", "content": content}],
        "text": {"format": {"type": "json_object"}},
    }
    from .judge import _extract_json_object, _usage_summary, extract_responses_text

    started = time.monotonic()
    errors: list[str] = []
    response: Any = None
    data: dict | None = None
    attempts = 0
    for _attempt in range(max(1, int(getattr(judge, "max_attempts", 2)))):
        attempts += 1
        try:
            response = judge.requester(payload, judge.api_key, judge.api_url)
            data = _extract_json_object(extract_responses_text(response))
            break
        except Exception as exc:
            errors.append(str(exc))
    latency = round(time.monotonic() - started, 3)
    reading.judge.update({
        "latency_seconds": latency,
        "attempts": attempts,
        "first_error": errors[0] if errors else "",
    })
    if data is None:
        reading.status = "unavailable"
        reading.detail = f"provider error after {len(errors)} attempt(s): " + " || ".join(errors)
        return reading
    usage = _usage_summary(response)
    reading.judge["usage"] = usage
    reading.judge["served_model"] = (
        str(response.get("model") or "") if isinstance(response, Mapping) else ""
    )
    credits: list[float] = []
    for criterion in criteria:
        raw = data.get(criterion.id)
        row: dict[str, Any] = {"credit": None, "band": None, "rationale": "", "confident": False,
                               "level": None, "frames_cited": []}
        if isinstance(raw, Mapping):

            try:
                if raw.get("level") is not None:
                    if isinstance(raw["level"], bool) or float(raw["level"]) != int(raw["level"]):
                        raise ValueError("level must be an integer")
                    value = level_to_credit(int(raw["level"]))
                else:
                    value = float(raw.get("credit"))
                    if not math.isfinite(value):
                        raise ValueError("credit must be finite")
                    value = min(1.0, max(0.0, value))
                row["credit"] = value
                row["level"] = credit_to_level(value)
                row["band"] = criterion.band_for(value).label
                credits.append(value)
            except (TypeError, ValueError, OverflowError):
                row["rationale"] = (
                    f"level {raw.get('level')!r} / credit {raw.get('credit')!r} is not a valid answer"
                )
            cited = raw.get("frames_cited")
            row["frames_cited"] = [str(c) for c in cited] if isinstance(cited, (list, tuple)) else []
            row["rationale"] = str(raw.get("rationale") or row["rationale"])
            row["confident"] = bool(raw.get("confident"))
        else:
            row["rationale"] = f"the response carried no object for {criterion.id}"
        reading.criteria[criterion.id] = row
    reading.status = "measured" if len(credits) == len(criteria) else (
        "partial" if credits else "unavailable"
    )
    reading.mean_credit = round(sum(credits) / len(credits), 4) if credits else None
    reading.detail = str(data.get("summary") or "")
    tokens = usage.get("total_tokens")
    reading.cost_note = (
        f"1 Responses call, {reading.judge['images']} images at detail=high, "
        f"{usage.get('input_tokens', '?')} input / {usage.get('output_tokens', '?')} output tokens "
        f"(total {tokens if tokens is not None else '?'}), {latency:.1f} s; the gateway reports no "
        "price, so cost is given in tokens"
    )
    return reading


def write_replay_reading(reading: ReplayReading, package: Path) -> Path:


    frames_dir = package / "replay_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    out = frames_dir / "reading.json"
    out.write_text(json.dumps(reading.to_dict(), indent=2, ensure_ascii=False, sort_keys=True),
                   encoding="utf-8")
    scard_path = package / "scard.json"
    if scard_path.is_file():
        try:
            data = json.loads(scard_path.read_text(encoding="utf-8"))
        except ValueError:
            data = None
        if isinstance(data, dict):
            data["replay_reading"] = reading.to_dict()
            scard_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
    return out


def replay_reading_markdown(reading: Mapping[str, Any]) -> str:

    film = reading.get("film") or {}
    judge = reading.get("judge") or {}
    lines = [
        f"## {PERCEPTUAL_ASSESSMENT} — replay evidence (`S4_replay`, weight 0)",
        "",
        f"status `{reading.get('status')}`; rubric `{reading.get('rubric_version')}`; "
        f"judge `{judge.get('model') or '—'}`; mean credit "
        + (f"{reading['mean_credit']:.3f}" if reading.get("mean_credit") is not None else "—")
        + ". Reported beside the S-card; not a score axis until a calibration fixture covers it.",
        "",
    ]
    if film:
        lines.append(
            f"Film: {film.get('sampled_frames', 0)} frames every {film.get('sample_interval_s')} s"
            f" over {film.get('duration_s', 0):.1f} s, goal frame "
            f"{'included' if film.get('goal_frame') else 'absent'}, replay stop_reason="
            f"`{film.get('stop_reason')}` (witness `{film.get('witness_stop_reason')}`); "
            f"contact sheet `{Path(film['contact_sheet']).name if film.get('contact_sheet') else '—'}`, "
            f"mp4 `{Path(film['mp4']).name if film.get('mp4') else '—'}`."
        )
        lines.append("")
    if reading.get("criteria"):
        lines.extend(["| criterion | credit | band | rationale |", "|---|---:|---|---|"])
        for cid, row in reading["criteria"].items():
            credit = row.get("credit")
            rationale = str(row.get("rationale") or "").replace("|", "/").replace("\n", " ")
            lines.append(
                f"| `{cid}` | {'—' if credit is None else f'{credit:.2f}'} | "
                f"{row.get('band') or '—'} | {rationale[:300]} |"
            )
        lines.append("")
    if reading.get("cost_note"):
        lines.extend([f"Cost: {reading['cost_note']}", ""])
    if reading.get("detail"):
        lines.extend([str(reading["detail"]), ""])
    return display_terms("\n".join(lines))


__all__ = [
    "CRITERIA_BY_ID",
    "MAX_SAMPLED_FRAMES",
    "REPLAY_CRITERIA",
    "REPLAY_FRAME_SOURCE",
    "REPLAY_READING_ID",
    "REPLAY_RUBRIC_VERSION",
    "ReplayFilm",
    "ReplayReading",
    "SAMPLE_INTERVAL_S",
    "build_contact_sheet",
    "build_replay_prompt",
    "film_from_movie",
    "judge_replay",
    "replay_reading_markdown",
    "replay_rubric_document",
    "sample_times",
    "write_replay_reading",
]
