
from __future__ import annotations

import json
import os
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
import shutil
import subprocess

from .package import write_json

CORPUS = Path(__file__).resolve().parents[4] / "eval/taskgen/vlm_rubrics"
MAX_FRAMES_PER_RECORDING = 120
MAX_TOTAL_FRAMES = 240
SAMPLE_FPS = 2.0
DENSE_FPS = 15
MAX_DENSE_FRAMES = 180


class VisualEvidenceError(ValueError):


    def __init__(self, code: str, owner: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.owner = owner
        self.detail = detail

    def to_dict(self):
        return {"status": "failed" if self.owner == "candidate" else "retry_required",
                "owner": self.owner, "failure_code": self.code, "detail": self.detail}


def freeze_visual_rubric(hidden: Path, game_id: str) -> bool:
    source = CORPUS / game_id
    if not (source / "rubric.json").is_file():
        return False
    target = hidden / "vlm"
    shutil.copytree(source, target, dirs_exist_ok=True)


    from ..scard.game_visual import build_response_contract
    rubric = json.loads((target / "rubric.json").read_text(encoding="utf-8"))
    write_json(target / "response_contract.json", build_response_contract(rubric))
    return True


@lru_cache(maxsize=128)
def _video_timing(movie: str) -> tuple[float, float]:

    payload = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=duration,avg_frame_rate:format=duration", "-of", "json", movie],
        check=True, capture_output=True, text=True,
    ).stdout)
    stream = (payload.get("streams") or [{}])[0]
    duration = float(stream.get("duration") or (payload.get("format") or {}).get("duration") or 0.0)
    try:
        fps = float(Fraction(stream.get("avg_frame_rate") or "30/1"))
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    return duration, max(fps, 1.0)


def extract_frame(movie: Path, at: float, target: Path) -> float:

    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    duration, fps = _video_timing(str(movie))
    if duration <= 0:
        raise ValueError(f"Reference video is empty: {movie}")
    safe_at = min(max(float(at), 0.0), max(0.0, duration - (1.0 / fps) - 1e-3))
    command = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", str(safe_at),
               "-i", str(movie), "-frames:v", "1", "-q:v", "2", str(target)]
    try:
        subprocess.run(command, check=True, capture_output=True)
    except subprocess.CalledProcessError:


        target.unlink(missing_ok=True)
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(movie),
                        "-ss", str(safe_at), "-frames:v", "1", "-q:v", "2", str(target)],
                       check=True, capture_output=True)
    if not target.is_file():
        raise ValueError(f"Reference video has no frame at {at}s (clamped={safe_at}s)")
    return safe_at


def prepare_visual_manifest(pkg, captured, out: Path):

    if pkg.manifest.get("mode") not in {"brief", "gdd", "skeleton", "port"}:
        raise ValueError("Game-rubric VLM judging applies only to Modes 1–3 and Mode 5")
    if pkg.manifest.get("reference_video") == "off":
        raise ValueError("Video-conditioned rubric is not valid for the no-video input ablation")
    frozen = pkg.hidden / "vlm"
    origin = "package/hidden/vlm"
    if not (frozen / "rubric.json").is_file():
        frozen = CORPUS / pkg.manifest["game_id"]
        origin = "evaluator catalog (historical package has no frozen VLM rubric)"
    rubric = json.loads((frozen / "rubric.json").read_text(encoding="utf-8"))
    contract_path = frozen / "response_contract.json"
    if contract_path.is_file():
        response_contract = json.loads(contract_path.read_text(encoding="utf-8"))
    else:


        from ..scard.game_visual import build_response_contract
        response_contract = build_response_contract(rubric)
    source = json.loads((frozen / "source.json").read_text(encoding="utf-8"))
    if rubric["game_id"] != pkg.manifest["game_id"] or source["game_id"] != rubric["game_id"]:
        raise ValueError("VLM rubric does not belong to this game")
    movies = sorted(path for path in (pkg.visible / "video").rglob("*")
                    if path.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm", ".avi"})
    if len(movies) != 1:
        raise ValueError("Expected the task's single supplied reference video")
    out = Path(out).resolve()
    frames = []
    for frame in source["reference_video"]["frames"]:
        target = out / "reference" / (frame["id"] + ".jpg")
        actual_time = extract_frame(movies[0], frame["time_s"], target)
        frames.append({**frame, "path": str(target), "source": "reference_video",
                       "actual_time_s": actual_time,
                       "time_clamped": actual_time != float(frame["time_s"])})
    assets = []
    for frame in source["provided_assets"]["frames"]:
        original = ((pkg.visible / "assets" / frame["asset_path"]) if frame.get("asset_path")
                    else frozen / frame["bundled_path"])
        if frame.get("preview_of") and not (pkg.visible / "assets" / frame["preview_of"]).is_file():
            raise FileNotFoundError(f"Previewed asset was not supplied: {frame['preview_of']}")
        target = out / "assets" / (frame["id"] + original.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        assets.append({**frame, "path": str(target), "source": "provided_assets"})
    manifest = {**captured, "schema_version": 2, "game_id": rubric["game_id"],
                "mode": pkg.manifest["mode"], "game_rubric": rubric,
                "response_contract": response_contract,
                "rubric_origin": origin, "reference_frames": frames,
                "provided_assets": {**source["provided_assets"], "frames": assets},
                "reference_scope": {k: v for k, v in source["reference_video"].items() if k != "frames"},
                "sampling": {"scope": "entire recording", "fps": SAMPLE_FPS,
                             "max_frames_per_recording": MAX_FRAMES_PER_RECORDING,
                             "max_total_frames": MAX_TOTAL_FRAMES,
                             "dense_motion": "three evenly spaced excerpts at 15 fps; up to 180 additional frames across all recordings",
                             "resolution": "native full frames; contact sheets are navigation aids"}}
    write_json(out / "visual_inputs.json", manifest)
    return manifest


def prepare_candidate_frames(demonstrations, out: Path):

    frames = []
    if not demonstrations:
        raise VisualEvidenceError("recorder_output_missing", "evaluator", "No evaluator recordings")
    for index, demo in enumerate(demonstrations, 1):
        film = demo.get("film") or {}
        error = str(demo.get("error") or film.get("error") or "").strip()
        movie_text = str(film.get("mp4") or "").strip()
        movie = Path(movie_text) if movie_text else None
        if "no ops tape" in error.lower():
            raise VisualEvidenceError("candidate_ops_missing", "candidate", error)
        if film.get("stop_reason") == "scene_missing":
            raise VisualEvidenceError("candidate_scene_missing", "candidate",
                                      error or "The candidate's declared scene was not delivered")
        if film.get("stop_reason") == "scene_load_failed":
            raise VisualEvidenceError("candidate_scene_load_failed", "candidate",
                                      error or "The candidate's declared scene could not load")
        if error:
            raise VisualEvidenceError("recorder_failed", "evaluator", error)
        if movie is None or not movie.is_file():
            raise VisualEvidenceError(
                "recorder_output_missing", "evaluator",
                f"Missing evaluator recording {demo.get('id', index)}",
            )

        try:
            metadata = json.loads(subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                "stream=duration,avg_frame_rate:format=duration", "-of", "json", str(movie)
            ], check=True, capture_output=True, text=True).stdout)
            stream = metadata["streams"][0]
            duration = float(stream.get("duration") or metadata["format"]["duration"])
            movie_fps = float(Fraction(stream["avg_frame_rate"]))
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, IndexError,
                TypeError, ValueError, ZeroDivisionError) as exc:
            raise VisualEvidenceError(
                "video_decode_failed", "evaluator", f"Cannot decode evaluator recording: {exc}",
            ) from exc
        if duration <= 0:
            raise VisualEvidenceError("video_decode_failed", "evaluator", "Empty evaluator recording")
        frame_limit = min(MAX_FRAMES_PER_RECORDING, MAX_TOTAL_FRAMES // len(demonstrations))
        rate = min(SAMPLE_FPS, (frame_limit - 1) / duration)
        directory = Path(out) / f"clip-{index:03d}"
        directory.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(movie),
                        "-vf", f"fps={rate}:start_time=0", "-frames:v", str(frame_limit - 1),
                        "-q:v", "2", str(directory / "%04d.jpg")], check=True, capture_output=True)
        paths = sorted(directory.glob("[0-9][0-9][0-9][0-9].jpg"))
        for n, path in enumerate(paths):
            frames.append({"id": f"C{index:03d}F{n + 1:04d}", "path": str(path.resolve()),
                           "clip": index, "time_s": round(n / rate, 4), "source": "candidate", "sampling_kind": "overview"})

        target = directory / "last.jpg"
        last_time = max(0.0, duration - 1.0 / movie_fps - 1e-5)
        extract_frame(movie, last_time, target)
        frames.append({"id": f"C{index:03d}F{len(paths) + 1:04d}", "path": str(target.resolve()),
                       "clip": index, "time_s": round(last_time, 4), "source": "candidate", "sampling_kind": "ending"})


        dense_count = MAX_DENSE_FRAMES // len(demonstrations) // 3
        window = min(4.0, duration / 3, dense_count / DENSE_FPS)
        for excerpt, fraction in enumerate((1 / 6, .5, 5 / 6), 1):
            start = max(0, min(duration - window, duration * fraction - window / 2))
            dest = directory / f"motion-{excerpt}"
            dest.mkdir(exist_ok=True)
            subprocess.run(["ffmpeg", "-nostdin", "-threads", "1", "-y", "-loglevel", "error", "-ss", str(start),
                            "-i", str(movie), "-t", str(window), "-vf", f"fps={DENSE_FPS}",
                            "-frames:v", str(dense_count), "-q:v", "2", "-pix_fmt", "yuvj420p",
                            str(dest / "%04d.jpg")],
                           check=True, capture_output=True)
            for n, path in enumerate(sorted(dest.glob("*.jpg"))):
                frames.append({"id": f"C{index:03d}D{excerpt}F{n + 1:04d}", "path": str(path.resolve()),
                               "clip": index, "time_s": round(start + n / DENSE_FPS, 4),
                               "source": "candidate", "sampling_kind": "dense_motion",
                               "excerpt": excerpt})
    return sorted(frames, key=lambda f: (f["clip"], f["time_s"], f["id"]))


def _sheets(frames, out, label):
    from PIL import Image, ImageDraw

    attachments = []
    for start in range(0, len(frames), 12):
        group = frames[start:start + 12]
        sheet = Image.new("RGB", (1280, 3 * 220), "#202020")
        draw = ImageDraw.Draw(sheet)
        for index, frame in enumerate(group):
            with Image.open(frame["path"]) as image:
                image = image.convert("RGB")
                image.thumbnail((320, 190))
                x, y = (index % 4) * 320, (index // 4) * 220
                sheet.paste(image, (x + (320 - image.width) // 2, y + 24))
            draw.text((x + 5, y + 5), f"{frame['id']}  {frame.get('time_s', '')}s", fill="white")
        path = out / f"{label}-{start // 12 + 1:03d}.jpg"
        sheet.save(path, quality=92)
        attachments.append({"path": str(path.resolve()), "label": label + " overview: " + ", ".join(f["id"] for f in group)})


    if frames:
        for index in sorted({0, len(frames) // 2, len(frames) - 1}):
            attachments.append({"path": frames[index]["path"], "label": label + " full frame " + frames[index]["id"]})
    return attachments


def evidence_images(manifest, candidate, items, folder):
    cited = {fid for item in items for basis in item["basis"] for fid in basis.get("frame_ids", [])}
    reference = [f for f in manifest["reference_frames"] if f["id"] in cited]
    assets = [f for f in manifest["provided_assets"]["frames"] if f["id"] in cited]
    if not reference:
        raise ValueError("Game-specific group has no actual GT image evidence")
    images = []
    for clip in sorted({f["clip"] for f in candidate}):
        images.extend(_sheets([f for f in candidate if f["clip"] == clip], folder, f"CANDIDATE-{clip:03d}"))
    images.extend(_sheets(reference, folder, "REFERENCE"))
    images.extend(_sheets(assets, folder, "SUPPLIED-ASSETS"))
    def anonymous(frames):
        rows = []
        for frame in frames:
            alias = folder / "evidence" / (frame["id"] + Path(frame["path"]).suffix)
            alias.parent.mkdir(exist_ok=True)
            if not alias.exists():
                try:
                    os.link(frame["path"], alias)
                except OSError:
                    shutil.copy2(frame["path"], alias)
            rows.append({**frame, "path": str(alias.relative_to(folder))})
        return rows

    return {"candidate_frames": anonymous(candidate), "reference_frames": anonymous(reference),
            "reference_scope": manifest["reference_scope"],
            "provided_assets": {**manifest["provided_assets"], "frames": anonymous(assets)},
            "sampling": manifest.get("sampling", {}),
            "full_frame_instruction": "Read the original paths for detail; contact sheets establish coverage, not detail resolution."}, images
