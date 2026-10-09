"""Trusted retained-evidence publication; never executes candidate code.

Unity runs offline. Only the controller's frozen observations and retained
media metadata are republished; there is no judge API or alternate score path.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..evaluate import TaskEvalResult
from ..package import TaskPackage
from ..submission import Submission
from .report_items import item_from_report as _item
from .scoring import REGISTRY_VERSION, annotate_report


def retained_path(value: str, root: Path) -> Path | None:
    """Map a controller-attested runtime path to the safely extracted media."""
    if not value:
        return None
    prefix = "/runtime/work/runs/"
    if not value.startswith(prefix):
        raise ValueError("capture path is not inside evaluator runtime runs")
    relative = Path(value[len(prefix):])
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("capture path escapes retained evidence")
    return target if target.is_file() else None


def complete_fidelity(
    report: dict[str, Any], package: Path, submission: Path, evidence: Path,
    out: Path, *, judge: str = "none",
) -> dict[str, Any]:
    if judge != "none":
        raise ValueError("Mode 5 publishes the fixed non-VLM evidence proxy")
    pkg = TaskPackage.read(package)
    items = [_item(raw) for raw in report.get("items", [])]
    runtime = (report.get("engine") or {}).get("runtime") or {}
    witness = runtime.get("witness") or {}
    movie = retained_path(str(witness.get("video_path") or ""), evidence)
    frames = [path for value in witness.get("frame_paths", [])
              if (path := retained_path(str(value), evidence)) is not None]
    result = TaskEvalResult(
        pkg, Submission(root=submission, project=submission, engine="unity"),
        items=items, engine=dict(report.get("engine") or {}),
        registry_version=REGISTRY_VERSION,
    )
    regenerated = result.to_dict()
    report.update(regenerated)
    report["fidelity_measurement"] = {
        "judge": judge, "phase": "trusted-post-runtime",
        "video_retained": movie is not None, "frames_retained": len(frames),
        "retained_video": str(movie) if movie else None,
        "retained_frames": [str(path) for path in frames],
        "paper_method_equivalence": "not_attested",
    }
    return annotate_report(report)
