

from __future__ import annotations

from pathlib import Path
import re
from typing import Any


CONCEPTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("target_experience", ("player experience", "target experience", "玩家体验", "目标体验")),
    ("design_pillars", ("design pillars", "设计支柱")),
    ("core_loop", ("core loop", "核心循环")),
    ("scope", ("scope", "范围")),
    ("non_goals", ("non-goals", "non goals", "非目标")),
    ("creative_freedom", ("creative freedom", "创作自由", "设计自由")),
    ("mechanics", ("mechanics", "机制")),
    ("progression", ("progression", "进程", "关卡节奏", "encounter beats")),
    ("victory", ("victory", "full-game completion", "胜利", "整局通关")),
    ("failure", ("failure", "失败", "death", "死亡")),
    ("restart", ("restart", "retry", "重试", "重新开始")),
    ("acceptance", ("acceptance", "验收")),
    ("evidence", ("evidence", "证据", "observed_dynamic", "agent_invented")),
    ("gb_interface", ("gb interface", "submission interface", "评测接口", "gb 接口")),
)

TOKEN = re.compile(r"`([a-z][a-z0-9_]{1,31})`")
_EMPHASIS = re.compile(r"[*_~]+")
CANONICAL = frozenset({
    "gb_left", "gb_right", "gb_up", "gb_down", "gb_jump", "gb_action",
    "gb_pause", "gb_reset",
})


def _logical_units(text: str) -> list[str]:

    units: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            units.append(" ".join(current).strip())
            current.clear()

    structural = re.compile(r"^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\|)")
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if structural.match(line):
            flush()
        current.append(line)
    flush()
    return units


def audit_authored_gdd(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    text = source.read_text(encoding="utf-8", errors="replace") if source.is_file() else ""
    lowered = text.lower()
    missing = [
        label for label, words in CONCEPTS
        if not any(word.lower() in lowered for word in words)
    ]
    errors: list[str] = []
    if len(text.strip()) < 1200:
        errors.append("GDD is too short for the required design and acceptance contract")
    if missing:
        errors.append("missing concepts: " + ", ".join(missing))
    if "system_prompt" in lowered or "system prompt" in lowered or "process.md" in lowered:
        errors.append("GDD depends on an external system prompt")
    return {
        "ready": not errors,
        "chars": len(text),
        "missing": missing,
        "errors": errors,
    }


def declared_interface_contract(path: str | Path) -> dict[str, Any]:

    source = Path(path)
    text = source.read_text(encoding="utf-8", errors="replace") if source.is_file() else ""
    canonical: set[str] = set()
    extended: set[str] = set()
    axes: set[str] = set()
    vague: list[str] = []
    for raw in _logical_units(text):


        lowered = _EMPHASIS.sub("", raw.lower())
        tokens = set(TOKEN.findall(raw))
        canonical.update(tokens & CANONICAL)


        candidates = {item for item in tokens if item not in CANONICAL and not item.startswith("gb_")}
        if "extended action" in lowered or "扩展动作" in raw:
            explicit_none = bool(re.search(
                r"\bextended\s+actions?\s*[:=\-—]\s*(?:none|n/?a)\b",
                lowered,
            ))
            negated = "no extended" in lowered or explicit_none or "不需要" in raw


            declared = set() if negated else candidates
            extended.update(declared)
            if not declared and not negated:
                vague.append(raw.strip()[:200])
        if "analog axis" in lowered or "analog axes" in lowered or "模拟轴" in raw:
            no_axes = "no analog" in lowered or bool(re.search(
                r"\bno\s+extended\s+actions?\s+(?:or|and)\s+analog\s+axes?\b",
                lowered,
            )) or bool(re.search(
                r"\banalog\s+(?:axis|axes)\s*[:=\-—]\s*(?:none|n/?a)\b",
                lowered,
            )) or "不需要" in raw
            declared = set() if no_axes else candidates
            axes.update(declared)
            if not declared and not no_axes:
                vague.append(raw.strip()[:200])
    return {
        "canonical_actions": sorted(canonical),
        "extended_actions": sorted(extended),
        "analog_axes": sorted(axes),
        "vague_declarations": vague,
    }
