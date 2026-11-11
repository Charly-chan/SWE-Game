


from __future__ import annotations

from ... import config as cfg
from .base import JudgeError, MultimodalJudge


_REGISTRY: dict[str, str] = {
    "stub":    "gamecraft_bench.verifier.judges.stub:StubJudge",
    "claude":  "gamecraft_bench.verifier.judges.claude:ClaudeJudge",
    "opus":    "gamecraft_bench.verifier.judges.claude:ClaudeOpusJudge",
    "kimi":    "gamecraft_bench.verifier.judges.kimi:KimiJudge",
    "openai":  "gamecraft_bench.verifier.judges.openai_gpt:OpenAIJudge",
    "gemini":  "gamecraft_bench.verifier.judges.gemini:GeminiJudge",
}


def _import_class(spec: str) -> type[MultimodalJudge]:
    module_path, _, name = spec.partition(":")
    mod = __import__(module_path, fromlist=[name])
    return getattr(mod, name)


def get_judge(backend: str | None = None, model: str | None = None) -> MultimodalJudge:


    name = (backend or cfg.JUDGE_BACKEND).strip().lower()
    if name not in _REGISTRY:
        raise JudgeError(
            f"unknown judge backend {name!r}; known: {sorted(_REGISTRY)}"
        )
    cls = _import_class(_REGISTRY[name])
    return cls(model=model or cfg.JUDGE_MODEL)


__all__ = ["MultimodalJudge", "JudgeError", "get_judge"]
