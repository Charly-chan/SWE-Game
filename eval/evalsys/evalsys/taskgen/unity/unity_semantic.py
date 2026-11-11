


from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ...routes.runner import RouteReading, reading_from_report
from ...routes.schema import Route


UNITY_SEMANTIC_REPORT_SCHEMA = "gamebench.unity-semantic-report.v1"
_TOP_LEVEL = {
    "schema",
    "run_id",
    "rows",
    "group_totals",
    "bounds",
    "device_contacts",
    "device_index",
    "stop_reason",
    "steps",
    "deaths",
    "stall_frames",
    "turns",
    "injects",
    "out_of_bounds",
    "physics_frames_at_end",
    "process_frames_at_end",
    "success_before_all_levels",
    "errors",
    "detail",
}
_ROW_FIELDS = {"f", "g", "o", "px", "py", "pz", "n", "wgc", "lv", "d", "c", "so", "vx", "vy", "vz", "s"}
_SIGNATURE_FIELDS = {"numeric", "audio_events", "anim", "node_count", "visible_count", "text"}


class UnitySemanticReportError(ValueError):
    pass


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise UnitySemanticReportError(f"{path} must be an object")
    return value


def _finite(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnitySemanticReportError(f"{path} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise UnitySemanticReportError(f"{path} must be finite")
    return number


def validate_unity_semantic_report(report: Mapping[str, Any]) -> None:


    missing = sorted(_TOP_LEVEL - set(report))
    if missing:
        raise UnitySemanticReportError("missing report fields: " + ", ".join(missing))
    if report.get("schema") != UNITY_SEMANTIC_REPORT_SCHEMA:
        raise UnitySemanticReportError(
            f"schema must be {UNITY_SEMANTIC_REPORT_SCHEMA!r}"
        )
    if not str(report.get("run_id") or "").strip():
        raise UnitySemanticReportError("run_id must be non-empty")
    rows = report.get("rows")
    if not isinstance(rows, list):
        raise UnitySemanticReportError("rows must be an array")
    _mapping(report.get("group_totals"), "group_totals")
    _mapping(report.get("device_contacts"), "device_contacts")
    _mapping(report.get("device_index"), "device_index")
    bounds = _mapping(report.get("bounds"), "bounds")
    for edge in ("min", "max"):
        point = _mapping(bounds.get(edge), f"bounds.{edge}")
        for axis in ("x", "y", "z"):
            _finite(point.get(axis), f"bounds.{edge}.{axis}")
    errors = report.get("errors")
    if not isinstance(errors, list) or any(not isinstance(item, str) for item in errors):
        raise UnitySemanticReportError("errors must be an array of strings")
    previous_frame = -1
    for index, raw in enumerate(rows):
        row = _mapping(raw, f"rows[{index}]")
        missing_row = sorted(_ROW_FIELDS - set(row))
        if missing_row:
            raise UnitySemanticReportError(
                f"rows[{index}] missing fields: " + ", ".join(missing_row)
            )
        frame = int(_finite(row.get("f"), f"rows[{index}].f"))
        if frame <= previous_frame:
            raise UnitySemanticReportError("row frames must be strictly increasing")
        previous_frame = frame
        for field in ("g", "o", "n", "d", "s"):
            _mapping(row.get(field), f"rows[{index}].{field}")
        if "rc" in row:
            for role, count in _mapping(row["rc"], f"rows[{index}].rc").items():
                if not isinstance(role, str) or isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise UnitySemanticReportError("rendered-role counts must be nonnegative integers")
        signature = _mapping(row.get("s"), f"rows[{index}].s")
        missing_signature = sorted(_SIGNATURE_FIELDS - set(signature))
        if missing_signature:
            raise UnitySemanticReportError(
                f"rows[{index}].s missing fields: " + ", ".join(missing_signature)
            )
        _mapping(signature.get("numeric"), f"rows[{index}].s.numeric")
        for field in ("px", "py", "pz", "vx", "vy", "vz"):
            _finite(row.get(field), f"rows[{index}].{field}")
        if not isinstance(row.get("c"), list):
            raise UnitySemanticReportError(f"rows[{index}].c must be an array")
    for field in (
        "steps",
        "deaths",
        "stall_frames",
        "turns",
        "injects",
        "out_of_bounds",
        "physics_frames_at_end",
        "process_frames_at_end",
    ):
        value = report.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise UnitySemanticReportError(f"{field} must be a non-negative integer")


def reading_from_unity_report(
    route: Route,
    report: Mapping[str, Any],
    *,
    wall_ms: float = 0.0,
    mode: str = "H",
    idle_baseline_id: str = "",
    refusals: Sequence[dict[str, Any]] = (),
    declared_numeric: Mapping[str, str] | None = None,
) -> RouteReading:


    validate_unity_semantic_report(report)
    return reading_from_report(
        route,
        dict(report),
        wall_ms=wall_ms,
        mode=mode,
        idle_baseline_id=idle_baseline_id,
        refusals=refusals,
        log="",
        declared_numeric=declared_numeric,
    )


__all__ = [
    "UNITY_SEMANTIC_REPORT_SCHEMA",
    "UnitySemanticReportError",
    "reading_from_unity_report",
    "validate_unity_semantic_report",
]
