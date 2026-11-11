

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .interface import SubmissionInterface, TaskInterfaceRequirements


@dataclass(frozen=True)
class Diagnostic:
    code: str
    detail: str
    attribution: str = "evaluator"


@dataclass(frozen=True)
class AssetObservation:
    loaded_assets: tuple[str, ...] = ()
    report_produced: bool = False
    detail: str = ""


    loaded_asset_digests: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetAssignment:
    supplied_assets: tuple[str, ...] = ()
    observation: AssetObservation | None = None
    supplied_digests: Mapping[str, str] = field(default_factory=dict)
    reference_observed: bool = False


@dataclass(frozen=True)
class CaptureBundle:
    frame_capture: Any = None
    anchor_frame: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)


    level_captures: tuple[Any, ...] = ()


@dataclass
class EvaluationContext:
    project: Path
    task_id: str
    tier: str
    mode: str
    interface: SubmissionInterface
    requirements: TaskInterfaceRequirements
    expectations: Any = None
    registered_routes: Any = None
    truth: Any = None
    route_readings: tuple[Any, ...] = ()
    route_gold: tuple[Any, ...] = ()
    capture: CaptureBundle | None = None
    asset_assignment: AssetAssignment | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    cold_import: Any = None
    level_probe: Any = None
    pass_a_seconds: float = 0.0
    pass_b_seconds: float = 0.0
    bot_skip_reason: str | None = None


    task_mode: str = ""


    submission_design: dict[str, Any] = field(default_factory=dict)

    @property
    def interface_provenance(self) -> dict[str, Any]:
        return self.interface.provenance()

    def assert_interface_provenance(self, payload: Any, *, source: str) -> None:

        actual = dict(getattr(payload, "interface", {}) or {})
        if not actual and isinstance(payload, dict):
            actual = dict(payload.get("interface") or {})
        expected = self.interface_provenance
        if actual and any(actual.get(key) != value for key, value in expected.items()):
            raise ValueError(
                f"{source} interface provenance mismatch: expected {expected}, got {actual}"
            )
