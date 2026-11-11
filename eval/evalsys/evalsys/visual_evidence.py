


from __future__ import annotations

from pathlib import Path
from typing import Any


SCHEMA = "gamebench.candidate-visual-evidence.v1"


def _package_path(path: str, package: Path) -> str:

    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(package.resolve()).as_posix()
    except (OSError, ValueError):
        return str(candidate)


def build_visual_evidence_manifest(
    frame_capture: Any,
    *,
    package: str | Path,
) -> dict[str, Any]:

    package_path = Path(package)
    records: list[dict[str, Any]] = []
    for item in list(getattr(frame_capture, "frame_evidence", ()) or ()):
        if not isinstance(item, dict) or not item.get("path"):
            continue
        records.append({
            "path": _package_path(str(item["path"]), package_path),
            "source_frame": str(item.get("source_frame") or ""),
            "frame_index": int(item["frame_index"]),
            "timestamp_seconds": float(item["timestamp_seconds"]),
        })

    invocation = getattr(frame_capture, "invocation", None)
    provenance = (
        invocation.to_dict() if invocation is not None and hasattr(invocation, "to_dict")
        else {}
    )
    fps = float(getattr(frame_capture, "fixed_fps", 0.0) or 0.0)
    generated = int(getattr(frame_capture, "frames_captured", 0) or 0)


    candidate = {
        "role": "candidate_evaluator_capture",
        "owner": "evaluator",
        "subject": "candidate_submission",
        "is_reference_input": False,
        "capture_kind": "frame_sequence" if records else "none",
        "video_status": "not_recorded",
        "video_file": None,
        "level": str(getattr(frame_capture, "level", "") or ""),
        "mode": str(getattr(frame_capture, "mode", "") or ""),
        "timeline": {
            "basis": "frame_index / fixed_fps from evaluator capture start",
            "fixed_fps": fps,
            "frames_generated": generated,
        },
        "frames": records,
        "provenance": provenance,
    }
    return {
        "schema": SCHEMA,
        "reference_input": {
            "role": "reference_input",
            "owner": "benchmark_task",
            "subject": "reference_game",
            "artifact_status": "separate_task_material",
            "is_candidate_evidence": False,
            "note": (
                "Reference gameplay belongs to the model-facing task package; "
                "it is not evidence captured from this candidate submission."
            ),
        },
        "candidate_evaluator_capture": candidate,
    }
