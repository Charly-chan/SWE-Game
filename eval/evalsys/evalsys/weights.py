


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .verdict import Gate, Ladder
from .interface.contract import CHANNEL_INPUTS

REGISTRY_VERSION = "2026-09-04.0"

REGISTRY_HISTORY: list[dict[str, str]] = [
    {
        "version": "2026-08-13.0",
        "note": "Initial nine O-channels. Audio excluded from scoring entirely.",
    },
    {
        "version": "2026-08-13.1",
        "note": (
            "O10 audio fidelity added at 0.030; the other O-channels scaled by "
            "0.9667. Supersedes .0, whose ceilings were inflated because audio "
            "had been dropped from the denominator rather than marked "
            "unreachable in it (§17.3)."
        ),
    },
    {
        "version": "2026-08-18.0",
        "note": (
            "Attribution registration; numeric weights are unchanged. A missing fixed "
            "submission diagnostic interface is charged by O2 only. Channels that need "
            "that interface report unmeasurable instead of manufacturing mechanic failures."
        ),
    },
    {
        "version": "2026-08-18.1",
        "note": (
            "Attribution correction; numeric weights are unchanged. Missing prompt-declared "
            "submission obligations remain in each dependent assertion's denominator. Only "
            "missing reference-only observe/anchor/travel_t capabilities are unmeasurable."
        ),
    },
    {
        "version": "2026-08-22.0",
        "note": (
            "Observability registration; numeric weights are unchanged. O6 is "
            "formally unobservable@D1 because D1 supplies no asset pack; D1.5/D2/D3 "
            "retain the live-scene asset probe requirement and may never substitute a "
            "filesystem inventory. O9 and O10a now consume evaluator-rendered anchor "
            "and live mixer readings in X mode."
        ),
    },
    {
        "version": "2026-08-23.0",
        "note": (
            "O2 semantics migrated from the historical Agent Bridge/CF-10/B1-B4 "
            "ladder to submission-interface v2. Channel weight remains 0.070. "
            "Old rungs: player_exists .20, player_unique .20, l3_actions_bound .15, "
            "gb_levels_loadable .15, bridge_files_generated .15, b1_b2 .075, "
            "b3_b4 .075. New rungs: groups .20, actions .25, levels .20, "
            "endings .20, optional_fields_valid .05, task_required_observables .10."
        ),
    },
    {
        "version": "2026-09-04.0",
        "note": (
            "O10 audio_fidelity removed (owner decision: audio is not measured and "
            "will not be; it had been unobservable on every card). Its 0.030 is "
            "redistributed proportionally over O1-O9 (x 0.900/0.870, rounded to "
            "three decimals, O1 carrying the +0.001 rounding residual), which "
            "restores the nine-channel register of 2026-08-13.0 exactly: O1 .063, "
            "O2 .072, O3 .180, O4 .099, O5 .081, O6 .063, O7 .126, O8 .126, O9 .090. "
            "Stored cards that still carry an O10 row remain loadable; the scorer "
            "ignores the row (see RETIRED_CHANNELS). audio_buses stays a valid "
            "optional interface field, validated by O2 only."
        ),
    },
]


RenderMode = Literal["H", "X", "either"]


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    weight: float
    """Final weight, already including the 0.90 O-card share."""
    mode: RenderMode
    summary: str


    never_unmeasurable: bool = False


O_CARD_SHARE = 0.900
S_CARD_SHARE = 0.100

CHANNELS: dict[str, Channel] = {
    c.id: c
    for c in [
        Channel(
            "O1", "runnable", 0.063, "either",
            "Cold-start import, boot, draw a non-trivial frame, answer one action. "
            "Measured on gb_levels.json[0], never on main_scene (§18.5).",
        ),
        Channel(
            "O2", "interface_conformance", 0.072, "H",
            "Submission-interface v2 conformance, including the frozen task-conditional "
            "O9 anchor address. Agent Bridge and CF-10 are evaluator/legacy checks.",
            never_unmeasurable=True,
        ),
        Channel(
            "O3", "mechanic_fidelity", 0.180, "H",
            "T-class assertions, filtered by inferable_from for the tier.",
        ),
        Channel(
            "O4", "level_topology", 0.099, "H",
            "Level count, inter-level digraph, per-level required device.",
        ),
        Channel(
            "O5", "spatial_ordinal", 0.081, "H",
            "Normalised ordinal and topological assertions. Scale-invariant.",
        ),
        Channel(
            "O6", "asset_use", 0.063, "H",
            "Engine-observed coverage of the supplied asset pack. "
            "Dropped entirely at D1, which supplies no assets (§4.5).",
        ),
        Channel(
            "O7", "authored_clear", 0.126, "H",
            "Zero-teleport, zero-inject continuous clear, scored by checkpoint.",
        ),
        Channel(
            "O8", "gui_agent_routes", 0.126, "either",
            "L0-L5 route pass rate. Op cap is per-game, from measured frame cost.",
        ),
        Channel(
            "O9", "anchor_composition", 0.090, "X",
            "Nine-box bearing + 30% mass + mirror similarity against the "
            "designated anchor frame. Pure computation, no VLM.",
        ),
    ]
}


RETIRED_CHANNELS: dict[str, str] = {
    "O10": (
        "audio_fidelity, withdrawn 2026-09-04.0: audio is not measured. The row was "
        "unobservable on every card ever produced, so ignoring it changes no score."
    ),
}

if set(RETIRED_CHANNELS) & set(CHANNELS):
    raise ValueError(
        f"retired channels re-registered: {sorted(set(RETIRED_CHANNELS) & set(CHANNELS))}"
    )

if set(CHANNEL_INPUTS) != set(CHANNELS):
    raise ValueError(
        "contract CHANNEL_INPUTS must register exactly every weighted channel; "
        f"missing={sorted(set(CHANNELS) - set(CHANNEL_INPUTS))}, "
        f"extra={sorted(set(CHANNEL_INPUTS) - set(CHANNELS))}"
    )

S_CARD_SUBWEIGHTS: dict[str, float] = {
    "S1": 0.40,
    "S2": 0.25,
    "S3": 0.20,
    "S4": 0.15,
}


S_CARD_VLM_CHANNELS = ("S1", "S2", "S3")
S_CARD_HUMAN_ONLY = ("S4",)


def check_weights() -> None:
    total = sum(c.weight for c in CHANNELS.values())
    if abs(total - O_CARD_SHARE) > 5e-4:
        raise ValueError(f"O-card weights sum to {total}, expected {O_CARD_SHARE}")
    s = sum(S_CARD_SUBWEIGHTS.values())
    if abs(s - 1.0) > 1e-9:
        raise ValueError(f"S-card sub-weights sum to {s}, expected 1.0")


check_weights()


O1_LADDER = Ladder("O1", [
    Gate("cold_import", 0.25,
         "Cold-start (.godot deleted) --headless --import is clean and autoloads "
         "resolve. 1685 bare class_name uses across 27 projects, 107 in autoloads: "
         "a stale cache turns those into boot-time death (§17.6)."),
    Gate("boots", 0.25,
         "Launches gb_levels[0] without crashing or hanging."),
    Gate("draws_nontrivial", 0.25,
         "First frame is not black or flat. The previous batch of 11 imported with "
         "zero errors and all 11 had to be redone; a rung below this one does not "
         "stop them."),
    Gate("responds_to_input", 0.25,
         "An injected action moves the state signature away from the idle baseline. "
         "15/15 projects bound no L3 action names, so the evaluator could not even "
         "inject."),
])

O2_LADDER = Ladder("O2", [
    Gate("groups", 0.20, "Fixed group vocabulary and the required gb_player."),
    Gate("actions", 0.25,
         "All eight aliases have non-empty bindings and are read by submitted source."),
    Gate("levels", 0.20, "At least one independently addressable, resolvable level."),
    Gate("endings", 0.20,
         "At least one success ending, distinguishable from declared failures."),
    Gate("optional_fields_valid", 0.05,
         "Optional numeric/device/camera/audio/predicate declarations are valid when present."),
    Gate("task_required_observables", 0.10,
         "Frozen task-required O9 submission addresses are present."),
])


@dataclass(frozen=True)
class InputTier:


    id: str
    name: str
    provides: frozenset[str]
    """`inferable_from` labels reachable at this tier."""
    asset_mode: str
    summary: str


TIERS: dict[str, InputTier] = {
    t.id: t
    for t in [
        InputTier("D1", "video only", frozenset({"V", "S"}), "generated-assets",
                  "Frame sequence plus sampling rate, plus the brief."),
        InputTier("D1.5", "video + assets seen on screen", frozenset({"V", "S", "A_seen"}),
                  "reference-backed-assets (subset)",
                  "Isolates the single variable of assets leaking mechanics (§7.3)."),
        InputTier("D2", "video + full assets", frozenset({"V", "S", "A_seen", "A"}),
                  "reference-backed-assets",
                  "Adds the full asset pack."),
        InputTier("D3", "video + assets + GDD", frozenset({"V", "S", "A_seen", "A", "G"}),
                  "reference-backed-assets",
                  "Adds the GDD. By construction ceiling(D3, Mva) = 1.0."),
    ]
}


@dataclass(frozen=True)
class Modality:


    id: str
    provides: frozenset[str]
    summary: str


MODALITIES: dict[str, Modality] = {
    m.id: m
    for m in [
        Modality("Mv", frozenset(), "Frame sequence, no audio. Every model can eat this."),
        Modality("Mva", frozenset({"V_audio"}), "Frames plus an aligned audio track."),
    ]
}


MAIN_TABLE_MODALITY = "Mv"
