


from __future__ import annotations

from pathlib import Path

from .interface import (
    ACTIONS, DEVICE_KINDS, FAILURE_ENDINGS, GROUPS, NUMERIC_SLOTS,
    SUCCESS_ENDINGS, ConformanceReport, TaskInterfaceRequirements,
    load_submission_interface,
)


def scan_conformance(
    project: str | Path,
    requirements: TaskInterfaceRequirements | None = None,
) -> ConformanceReport:
    return load_submission_interface(project, requirements=requirements).report


__all__ = [
    "ACTIONS", "DEVICE_KINDS", "FAILURE_ENDINGS", "GROUPS", "NUMERIC_SLOTS",
    "SUCCESS_ENDINGS", "ConformanceReport", "TaskInterfaceRequirements",
    "load_submission_interface", "scan_conformance",
]
