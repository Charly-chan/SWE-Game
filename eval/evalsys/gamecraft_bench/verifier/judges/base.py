


from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path


class JudgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RequirementSpec:

    id: str
    description: str


@dataclass(frozen=True)
class JudgeRequest:

    demo_id: str
    video_path: Path
    frame_paths: list[Path]
    requirements: list[RequirementSpec]


@dataclass(frozen=True)
class JudgeResponse:

    scores: dict[str, float]
    rationales: dict[str, str] = field(default_factory=dict)
    raw: str = ""


class MultimodalJudge(abc.ABC):


    name: str = "base"
    default_model: str = ""

    def __init__(self, *, model: str | None = None) -> None:
        self.model = model or self.default_model

    @abc.abstractmethod
    def score(self, request: JudgeRequest) -> JudgeResponse:
        pass


    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.model!r})"
