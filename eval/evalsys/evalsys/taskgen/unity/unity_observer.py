


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .unity_semantic import UNITY_SEMANTIC_REPORT_SCHEMA, validate_unity_semantic_report


_ZERO_BOUNDS = {
    "min": {"x": 0.0, "y": 0.0, "z": 0.0},
    "max": {"x": 0.0, "y": 0.0, "z": 0.0},
}


@dataclass
class UnitySemanticReportBuilder:
    run_id: str
    declared_level_count: int
    rows: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0

    def add_observation(self, message: Mapping[str, Any]) -> None:
        row = message.get("row")
        events = message.get("events", ())
        if not isinstance(row, Mapping):
            raise ValueError("observer message has no semantic row")
        if self.rows and int(row.get("f", -1)) <= int(self.rows[-1].get("f", -1)):
            raise ValueError("observer row frames are not strictly increasing")
        self.rows.append(dict(row))
        if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
            raise ValueError("observer events must be an array")
        for event in events:
            if not isinstance(event, Mapping):
                raise ValueError("observer event must be an object")
            self.events.append(dict(event))

    def add_session(self, rows: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]) -> None:
        self.rows = [dict(row) for row in rows]
        self.events = [dict(event) for event in events]

    def build(self, *, stop_reason: str, detail: str = "") -> dict[str, Any]:
        group_totals: dict[str, int] = {}
        for row in self.rows:
            for role, count in dict(row.get("g") or {}).items():
                group_totals[str(role)] = max(group_totals.get(str(role), 0), int(count))

        bounds = None
        errors: list[str] = []
        success_frames: list[int] = []
        failure_count = 0
        for event in self.events:
            kind = str(event.get("kind") or "")
            if kind == "bounds_frozen" and bounds is None:
                candidate = {"min": event.get("min"), "max": event.get("max")}
                if isinstance(candidate["min"], Mapping) and isinstance(candidate["max"], Mapping):
                    bounds = candidate
            elif kind == "error":
                errors.append(str(event.get("message") or "Unity observer error"))
            elif kind == "outcome_success":
                success_frames.append(int(event.get("frame", 0)))
            elif kind == "outcome_failure":
                failure_count += 1

        last = self.rows[-1] if self.rows else {}
        device_index: dict[str, Any] = {}
        device_contacts: dict[str, int] = {}
        for row in self.rows:
            device_index.update(dict(row.get("d") or {}))
            for device_id in row.get("c") or []:
                device_contacts[device_id] = device_contacts.get(device_id, 0) + 1
        max_level = max((int(row.get("lv", 0)) for row in self.rows), default=0)
        report: dict[str, Any] = {
            "schema": UNITY_SEMANTIC_REPORT_SCHEMA,
            "run_id": self.run_id,
            "rows": self.rows,
            "group_totals": group_totals,
            "bounds": bounds or _ZERO_BOUNDS,
            "device_contacts": device_contacts,
            "device_index": device_index,
            "stop_reason": stop_reason,
            "steps": self.steps,
            "deaths": failure_count,
            "stall_frames": 0,
            "turns": 1,
            "injects": 0,
            "out_of_bounds": 0,
            "physics_frames_at_end": int(last.get("f", 0)),
            "process_frames_at_end": int(last.get("f", 0)),
            "success_before_all_levels": bool(
                success_frames and self.declared_level_count > 0 and max_level < self.declared_level_count
            ),
            "errors": errors,
            "detail": detail,


            "events": self.events,
        }
        validate_unity_semantic_report(report)
        return report


__all__ = ["UnitySemanticReportBuilder"]
