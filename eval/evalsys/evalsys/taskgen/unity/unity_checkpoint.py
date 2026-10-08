

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class CheckpointCapture:
    checkpoint_id: str
    run_id: str
    frame: int
    timestamp_seconds: float
    path: str
    digest: str
    engine: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CheckpointPair:
    checkpoint_id: str
    reference: CheckpointCapture
    candidate: CheckpointCapture

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "reference": self.reference.to_dict(),
            "candidate": self.candidate.to_dict(),
        }


@dataclass(frozen=True)
class CheckpointPairReport:
    pairs: tuple[CheckpointPair, ...]
    missing_candidate: tuple[str, ...]
    unexpected_candidate: tuple[str, ...]

    @property
    def observable(self) -> bool:
        return bool(self.pairs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pairs": [pair.to_dict() for pair in self.pairs],
            "missing_candidate": list(self.missing_candidate),
            "unexpected_candidate": list(self.unexpected_candidate),
            "pairing": "exact_checkpoint_id_only",
        }


def _index(captures: Iterable[CheckpointCapture], *, expected_engine: str) -> dict[str, CheckpointCapture]:
    result: dict[str, CheckpointCapture] = {}
    for capture in captures:
        if not capture.checkpoint_id:
            raise ValueError("checkpoint_id is required")
        if capture.checkpoint_id in result:
            raise ValueError(f"duplicate checkpoint capture: {capture.checkpoint_id}")
        if capture.engine != expected_engine:
            raise ValueError(
                f"checkpoint {capture.checkpoint_id} engine must be {expected_engine!r}"
            )
        if capture.frame < 0 or capture.timestamp_seconds < 0:
            raise ValueError(f"checkpoint {capture.checkpoint_id} has negative provenance")
        if not capture.path or not capture.digest.startswith("sha256:"):
            raise ValueError(f"checkpoint {capture.checkpoint_id} lacks path/digest provenance")
        result[capture.checkpoint_id] = capture
    return result


def pair_checkpoint_captures(
    reference: Iterable[CheckpointCapture],
    candidate: Iterable[CheckpointCapture],
) -> CheckpointPairReport:
    reference_by_id = _index(reference, expected_engine="godot")
    candidate_by_id = _index(candidate, expected_engine="unity")
    common = sorted(reference_by_id.keys() & candidate_by_id.keys())
    return CheckpointPairReport(
        pairs=tuple(
            CheckpointPair(checkpoint_id, reference_by_id[checkpoint_id], candidate_by_id[checkpoint_id])
            for checkpoint_id in common
        ),
        missing_candidate=tuple(sorted(reference_by_id.keys() - candidate_by_id.keys())),
        unexpected_candidate=tuple(sorted(candidate_by_id.keys() - reference_by_id.keys())),
    )


__all__ = [
    "CheckpointCapture",
    "CheckpointPair",
    "CheckpointPairReport",
    "pair_checkpoint_captures",
]
