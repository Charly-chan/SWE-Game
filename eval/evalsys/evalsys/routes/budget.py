


from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from evalsys.render.modes import (
    RenderMode,
    select_mode,
    timeout_item,
    wall_timeout_s,
)
from evalsys.render.render_cost import (
    FRAMES_PER_OP,
    L5_FRAMES,
    L5_MS_PER_FRAME_MAX,
    RENDER_BUDGET_S,
    ROUTE_OP_CEILING,
    RenderCost,
    route_op_cap,
)
from evalsys.verdict import Attribution, Item, inconclusive


TIER_WEIGHTS: dict[int, float] = {
    0: 0.05,
    1: 0.10,
    2: 0.15,
    3: 0.25,
    4: 0.20,
    5: 0.25,
}

TIER_NAMES: dict[int, str] = {
    0: "survive/respond",
    1: "single action",
    2: "fixed sequence of 2-5",
    3: "timing window",
    4: "exploration, goal not in initial view",
    5: "whole game, zero inject",
}


TIER_STEPS: dict[int, int] = {0: 1, 1: 1, 2: 5, 3: 8, 4: 20, 5: 40}


SETTLE_FRAMES = 30


L0_IDLE_FRAMES = 420


WALL_SAFETY = 3.0


L5_WALL_SAFETY = 4.0
L5_WALL_FLOOR_S = 120


def op_cap(ms_per_frame: float) -> int:


    return route_op_cap(ms_per_frame)


def l5_eligible(ms_per_frame: float) -> bool:

    return ms_per_frame > 0 and ms_per_frame <= L5_MS_PER_FRAME_MAX


def frames_for_ops(ops: int) -> int:
    return int(math.ceil(ops * FRAMES_PER_OP)) + SETTLE_FRAMES


@dataclass
class RouteBudget:


    tier: int
    ops: int
    frames: int
    mode: RenderMode
    ms_per_frame_p50: float
    ms_per_frame_p95: float
    wall_timeout_s: int
    l5_eligible: bool
    eligible: bool = True
    op_cap: int = ROUTE_OP_CEILING
    capped_by: str = "design"
    detail: str = ""
    declared_steps: int = 0
    declared_frames: int = 0

    @property
    def expected_s(self) -> float:
        return self.frames * self.ms_per_frame_p50 / 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "tier_name": TIER_NAMES.get(self.tier, "?"),
            "ops": self.ops,
            "frames": self.frames,
            "mode": self.mode.value,
            "ms_per_frame_p50": round(self.ms_per_frame_p50, 2),
            "ms_per_frame_p95": round(self.ms_per_frame_p95, 2),
            "wall_timeout_s": self.wall_timeout_s,
            "expected_s": round(self.expected_s, 1),
            "l5_eligible": self.l5_eligible,
            "eligible": self.eligible,
            "op_cap": self.op_cap,
            "capped_by": self.capped_by,
            "detail": self.detail,
            "declared_steps": self.declared_steps,
            "declared_frames": self.declared_frames,
        }

    def ineligible_item(self, route_id: str, weight: float = 1.0) -> Item:

        return inconclusive(
            f"O8/L{self.tier}/{route_id}",
            weight=weight,
            detail=self.detail,
            attribution=Attribution.HARNESS,
            evidence=self.to_dict(),
        )


def derive(
    tier: int,
    ms_per_frame_p50: float,
    ms_per_frame_p95: float | None = None,
    *,
    needs_pixels: bool = False,
    declared_steps: int = 0,
    declared_frames: int = 0,
) -> RouteBudget:


    p95 = ms_per_frame_p95 if ms_per_frame_p95 and ms_per_frame_p95 > 0 else ms_per_frame_p50
    cap = op_cap(ms_per_frame_p50)
    want = declared_steps or TIER_STEPS.get(tier, ROUTE_OP_CEILING)
    ops = max(1, min(cap, want))
    capped_by = "measured render cost" if ops == cap and cap < want else "route request"

    frames = frames_for_ops(ops)
    if declared_frames > 0:


        frames = max(frames, declared_frames)
    eligible = True
    detail = (
        f"L{tier} on {ms_per_frame_p50:.1f} ms/frame: {ops} ops "
        f"(cap {cap}, requested {want}), {frames} frames"
    )

    l5_ok = l5_eligible(ms_per_frame_p50)
    if tier == 5:
        frames = max(frames, L5_FRAMES)
        if not l5_ok:
            eligible = False
            detail = (
                f"L5 needs >= {L5_FRAMES} frames and this project measured "
                f"{ms_per_frame_p50:.1f} ms/frame, i.e. "
                f"{L5_FRAMES * ms_per_frame_p50 / 1000.0:.0f} s of rendering for "
                f"one route. The ceiling is {L5_MS_PER_FRAME_MAX:.0f} ms/frame, so "
                "L5 is declared unaffordable up front rather than discovered as "
                "a timeout later"
            )

    mode = select_mode(frames, ms_per_frame_p50, needs_pixels)
    safety = L5_WALL_SAFETY if tier == 5 else WALL_SAFETY
    wall = wall_timeout_s(frames, p95, safety)
    if tier == 5:
        wall = max(wall, wall_timeout_s(frames, p95, WALL_SAFETY) + L5_WALL_FLOOR_S)
    return RouteBudget(
        tier=tier,
        ops=ops,
        frames=frames,
        mode=mode,
        ms_per_frame_p50=ms_per_frame_p50,
        ms_per_frame_p95=p95,
        wall_timeout_s=wall,
        l5_eligible=l5_ok,
        eligible=eligible,
        op_cap=cap,
        capped_by=capped_by,
        detail=detail,
        declared_steps=declared_steps,
        declared_frames=declared_frames,
    )


def derive_from_cost(
    tier: int,
    cost: RenderCost,
    *,
    needs_pixels: bool = False,
    declared_steps: int = 0,
    declared_frames: int = 0,
) -> RouteBudget:


    if not cost.measured:
        return RouteBudget(
            tier=tier,
            ops=0,
            frames=0,
            mode=RenderMode.H,
            ms_per_frame_p50=0.0,
            ms_per_frame_p95=0.0,
            wall_timeout_s=0,
            l5_eligible=False,
            eligible=False,
            op_cap=0,
            capped_by="unmeasured",
            detail=(
                "no per-frame cost was measured for this project, so no route "
                f"budget can be derived: {cost.detail}"
            ),
            declared_steps=declared_steps,
            declared_frames=declared_frames,
        )
    return derive(
        tier,
        cost.ms_per_frame_p50,
        cost.ms_per_frame_p95,
        needs_pixels=needs_pixels,
        declared_steps=declared_steps,
        declared_frames=declared_frames,
    )


def timeout(route_id: str, budget: RouteBudget, weight: float = 1.0) -> Item:

    return timeout_item(
        f"O8/L{budget.tier}/{route_id}",
        frames=budget.frames,
        timeout_s=budget.wall_timeout_s,
        mode=budget.mode,
        weight=weight,
    )


def budget_table(ms_per_frame_values: list[float]) -> list[dict[str, Any]]:


    rows = []
    for ms in ms_per_frame_values:
        ops = op_cap(ms)
        rows.append(
            {
                "ms_per_frame": ms,
                "route_op_cap": ops,
                "frames_at_cap": frames_for_ops(ops),
                "l5_eligible": l5_eligible(ms),
                "render_budget_s": RENDER_BUDGET_S,
                "l5_render_s": round(L5_FRAMES * ms / 1000.0, 1),
            }
        )
    return rows
