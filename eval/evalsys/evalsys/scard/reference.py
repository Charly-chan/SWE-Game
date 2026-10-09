
from pathlib import Path

from .judge import Frame, load_frame


def reference_video_frames(
    video: str | Path | None, out: Path, *, max_frames: int = 6,
) -> tuple[tuple[Frame, ...], str]:
    if video is None or not Path(video).is_file():
        return (), "GT reference video unavailable; candidate-only visual reading"
    from .reference_video import extract_reference_frames

    try:
        paths, times = extract_reference_frames(video, out, max_frames=max_frames)
        frames = tuple(
            load_frame(path, source="evalsys.taskgen.reference_video",
                       point_id=f"reference_at_{timestamp:.3f}s", level="reference")
            for path, timestamp in zip(paths, times)
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return (), f"GT reference frames unavailable: {exc}"
    if not frames:
        return (), "GT reference extraction produced no frames; candidate-only visual reading"
    return frames, (
        "GT reference frames set the visual-quality ceiling. Compare corresponding "
        "gameplay roles and stages when visible, not timestamps, node names or exact "
        "pixel layouts. Different correct implementations are allowed. A stage absent "
        "from these samples is unobserved, not proof that the feature is missing."
    )
