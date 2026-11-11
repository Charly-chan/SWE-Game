


from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mode:
    id: str
    number: int
    title: str

    returns: tuple[str, ...]

    hands: tuple[str, ...]


UNITY_ACTIONS: tuple[str, ...] = (
    "gb_left",
    "gb_right",
    "gb_up",
    "gb_down",
    "gb_jump",
    "gb_action",
    "gb_attack",
    "gb_dash",
)


BRIEF = Mode(
    "brief",
    1,
    "assets + video + a statement of what kind of game to build",
    returns=("GDD.md", "ops.json", "godot_project"),
    hands=("statement.md", "assets", "video"),
)
GDD = Mode(
    "gdd",
    2,
    "GDD + assets + video",
    returns=("ops.json", "godot_project"),
    hands=("GDD.md", "assets", "video"),
)
SKELETON = Mode(
    "skeleton",
    3,
    "minimal code skeleton + assets + video",
    returns=("ops.json", "godot_project"),
    hands=("skeleton", "assets", "video"),
)
BUGFIX = Mode(
    "bugfix",
    4,
    "corpus game with injected bugs",
    returns=("godot_project",),
    hands=("game", "GAMEPLAY_REQUIREMENTS.md", "BUG_REPORT.md"),
)
PORT = Mode(
    "port",
    5,
    "complete sanitized Godot source + GDD + assets + video, ported to Unity",
    returns=("unity_project", "ops.json", "BUILD.md"),
    hands=("source_godot", "GDD.md", "assets", "video", "unity_interface", "target_unity"),
)

MODES: dict[str, Mode] = {
    BRIEF.id: BRIEF,
    GDD.id: GDD,
    SKELETON.id: SKELETON,
    BUGFIX.id: BUGFIX,
    PORT.id: PORT,
}

_ALIASES = {
    "1": BRIEF.id,
    "m1": BRIEF.id,
    "m1-brief": BRIEF.id,
    "from-brief": BRIEF.id,
    "2": GDD.id,
    "m2": GDD.id,
    "m2-gdd": GDD.id,
    "from-gdd": GDD.id,
    "3": SKELETON.id,
    "m3": SKELETON.id,
    "m3-skeleton": SKELETON.id,
    "from-skeleton": SKELETON.id,
    "4": BUGFIX.id,
    "m4": BUGFIX.id,
    "m4-bugfix": BUGFIX.id,
    "fix": BUGFIX.id,
    "5": PORT.id,
    "m5": PORT.id,
    "m5-port": PORT.id,
    "port": PORT.id,
    "unity": PORT.id,
}


def parse_mode(value: str) -> Mode:
    key = (value or "").strip().lower().replace("_", "-")
    if key in MODES:
        return MODES[key]
    canonical = _ALIASES.get(key)
    if canonical is None:
        raise ValueError(
            f"unknown mode {value!r}; expected one of "
            f"{sorted(MODES)} or aliases {sorted(_ALIASES)}"
        )
    return MODES[canonical]


def needs_ops(mode: Mode | str) -> bool:
    resolved = mode if isinstance(mode, Mode) else parse_mode(mode)
    return "ops.json" in resolved.returns


def needs_gdd(mode: Mode | str) -> bool:
    resolved = mode if isinstance(mode, Mode) else parse_mode(mode)
    return "GDD.md" in resolved.returns
