"""Shared reference-video extraction for the construction-mode visual judges."""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def extract_reference_frames(
    video: str | Path,
    out_dir: str | Path,
    *,
    max_frames: int = 6,
    ffmpeg_bin: str | Path | None = None,
    ffprobe_bin: str | Path | None = None,
) -> tuple[tuple[str, ...], tuple[float, ...]]:
    """Extract an evenly spaced native-resolution reference sequence."""
    source = Path(video).resolve()
    output_root = Path(out_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    # Extract into a fresh owned child; never recursively clear caller data.
    destination = Path(tempfile.mkdtemp(prefix="sequence-", dir=output_root))
    ffmpeg = _tool(ffmpeg_bin, "ffmpeg")
    ffprobe = _tool(ffprobe_bin, "ffprobe")
    if ffmpeg is None or ffprobe is None or not source.is_file():
        return (), ()
    probe = subprocess.run(
        [
            str(ffprobe), "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(source),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        duration = float((probe.stdout or "").strip())
    except ValueError:
        return (), ()
    if probe.returncode != 0 or duration <= 0:
        return (), ()
    count = max(1, int(max_frames))
    times = tuple(duration * (index + 0.5) / count for index in range(count))
    frames: list[str] = []
    kept_times: list[float] = []
    for index, timestamp in enumerate(times):
        output = destination / f"reference_{index:03d}.png"
        process = subprocess.run(
            [
                str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{timestamp:.6f}", "-i", str(source),
                "-frames:v", "1", "-vsync", "0", str(output),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode == 0 and output.is_file():
            frames.append(str(output))
            kept_times.append(timestamp)
    return tuple(frames), tuple(kept_times)


def _tool(explicit: str | Path | None, default: str) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
        found = shutil.which(str(explicit))
        return Path(found).resolve() if found else None
    found = shutil.which(default)
    return Path(found).resolve() if found else None
