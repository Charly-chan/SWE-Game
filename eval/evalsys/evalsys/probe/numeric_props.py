


from __future__ import annotations


NUMERIC_PROPERTIES: tuple[str, ...] = (
    "score",
    "health",
    "hp",
    "lives",
    "shields",
    "combo",
    "inventory",
    "coins",
    "ammo",
    "energy",
    "shield",
    "kills",
    "charge",
    "fuse_left",
    "heat",
    "score_earned",
    "cells_delivered",
    "clock_left",
    "progress",
    "timer",
    "coins_taken",
)


def gdscript_array_literal() -> str:

    inner = ", ".join(f'"{name}"' for name in NUMERIC_PROPERTIES)
    return f"[{inner}]"
