

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, TYPE_CHECKING

from .requirements import TaskInterfaceRequirements

if TYPE_CHECKING:
    from .model import SubmissionInterface


@dataclass(frozen=True)
class ConformanceReport:
    project: str
    verdict: str
    checks: dict[str, dict[str, Any]]
    missing: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.verdict == "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "project": self.project,
            "verdict": self.verdict,
            "checks": self.checks,
            "missing": list(self.missing),
            "submission_obligation_excludes": ["observe()", "observe().kind", "Agent Bridge"],
        }


def with_requirements(
    report: ConformanceReport,
    interface: "SubmissionInterface",
    requirements: TaskInterfaceRequirements,
) -> ConformanceReport:
    checks = dict(report.checks)
    missing = list(report.missing)
    values = {
        "anchor_camera": interface.anchor_camera,
        "audio_buses": interface.audio_buses,
    }
    for channel, requirement in sorted(requirements.channels.items()):
        if not requirement.required:
            continue
        absent = [field for field in requirement.fields if not values.get(field)]
        key = f"task_required_{channel}"
        checks[key] = {
            "status": "fail" if absent else "pass",
            "detail": (
                f"missing task-required field(s): {', '.join(absent)}"
                if absent else f"task-required field(s) present: {', '.join(requirement.fields)}"
            ),
            "channel": channel,
            "fields": list(requirement.fields),
            "reason": requirement.reason,
        }
        missing.extend(f"gb_levels.json.{field}(required for {channel})" for field in absent)
    return replace(report, verdict="PASS" if not missing else "FAIL", checks=checks,
                   missing=tuple(missing))


def report_for(
    interface: "SubmissionInterface",
    requirements: TaskInterfaceRequirements | None = None,
) -> ConformanceReport:
    report = interface.report


    already_bound = any(
        key.startswith("task_required_") for key in report.checks
    )
    if requirements is not None and not already_bound:
        report = with_requirements(report, interface, requirements)
    return report
