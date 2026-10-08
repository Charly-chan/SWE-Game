


from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ..verdict import Item, passed, unmeasurable
from ..weights import S_CARD_HUMAN_ONLY, S_CARD_SUBWEIGHTS, S_CARD_VLM_CHANNELS


RUBRIC_VERSION = "2026-09-09.1"


RUBRIC_MARKDOWN_PATH = Path(__file__).with_name("RUBRIC.md")


LEVELS: tuple[int, ...] = (0, 1, 2, 3, 4)
DEFAULT_LEVEL = 2
LEVEL_CREDITS: dict[int, float] = {level: level / 4 for level in LEVELS}


def level_to_credit(level: int) -> float:

    if level not in LEVEL_CREDITS:
        raise ValueError(f"level {level!r} is not one of {LEVELS}")
    return LEVEL_CREDITS[level]


def credit_to_level(credit: float) -> int:

    for level in sorted(LEVELS, reverse=True):
        if credit >= LEVEL_CREDITS[level] - 1e-9:
            return level
    return LEVELS[0]


def rubric_markdown() -> str:
    return RUBRIC_MARKDOWN_PATH.read_text(encoding="utf-8")


def rubric_markdown_sha256() -> str:

    return hashlib.sha256(rubric_markdown().encode("utf-8")).hexdigest()


def judging_rules() -> list[str]:


    rules: list[str] = []
    in_section = False
    for line in rubric_markdown().splitlines():
        if line.startswith("## "):
            in_section = line.strip() == "## Judging rules"
            continue
        if not in_section:
            continue
        if line.startswith("- "):
            rules.append(line[2:].strip())
        elif line.startswith("  ") and rules:
            rules[-1] = f"{rules[-1]} {line.strip()}"
    if not rules:
        raise ValueError(f"{RUBRIC_MARKDOWN_PATH}: no `## Judging rules` bullets found")
    return rules


class Scorer(str, enum.Enum):


    JUDGE_OR_HUMAN = "judge_or_human"
    HUMAN_ONLY = "human_only"


@dataclass(frozen=True)
class Band:


    credit: float
    label: str
    descriptor: str

    @property
    def level(self) -> int:
        return credit_to_level(self.credit)

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "credit": self.credit, "label": self.label,
                "descriptor": self.descriptor}


@dataclass(frozen=True)
class Rubric:


    id: str
    name: str
    weight: float
    question: str
    bands: tuple[Band, ...]
    scorer: Scorer

    evidence_required: tuple[str, ...]


    inferable_from: tuple[str, ...]


    calibration_fixtures: tuple[str, ...]
    rationale: str

    @property
    def human_only(self) -> bool:
        return self.scorer is Scorer.HUMAN_ONLY

    def band_for(self, credit: float) -> Band:

        for band in self.bands:
            if credit >= band.credit - 1e-9:
                return band
        return self.bands[-1]

    def band_for_level(self, level: int) -> Band:
        return self.band_for(level_to_credit(level))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "weight": self.weight,
            "question": self.question,
            "scorer": self.scorer.value,
            "human_only": self.human_only,
            "evidence_required": list(self.evidence_required),
            "inferable_from": list(self.inferable_from),
            "calibration_fixtures": list(self.calibration_fixtures),
            "rationale": self.rationale,
            "bands": [b.to_dict() for b in self.bands],
            "rubric_version": RUBRIC_VERSION,
        }


S1 = Rubric(
    id="S1",
    name="surface_completeness",
    weight=S_CARD_SUBWEIGHTS["S1"],
    question=(
        "Are the major visible surfaces -- walls, floors, ceilings, large "
        "background planes -- actually textured, or are they flat colour with "
        "the art budget spent on props and weapons?"
    ),
    bands=(
        Band(1.00, "textured throughout",
             "Every large surface in every sampled frame carries visible "
             "material detail: grain, tiling, panel lines, wear, or a gradient "
             "that is not a single lighting ramp. No wall, floor or ceiling "
             "region reads as one uniform fill."),
        Band(0.75, "one flat surface class",
             "One class of large surface (typically ceilings) is untextured "
             "flat colour; walls and floors carry detail. A viewer notices it "
             "only when looking for it."),
        Band(0.50, "environment flat, props detailed",
             "Walls and floors are flat fills while props, weapons and "
             "characters are fully textured -- the corpus_reference signature. The "
             "frame reads as a detailed object set floating in an untextured "
             "box."),
        Band(0.25, "near-total flat fill",
             "Nearly every surface is a flat colour; a small number of "
             "textured elements remain. The scene reads as greybox with "
             "colours assigned."),
        Band(0.00, "untextured or absent",
             "Greybox, missing-texture magenta, single-colour void, or a "
             "surface set that did not load at all."),
    ),
    scorer=Scorer.JUDGE_OR_HUMAN,
    evidence_required=(
        "at least two evaluator-captured frames of distinct level geometry",
        "frames at native capture resolution, never downscaled",
    ),
    inferable_from=("V",),
    calibration_fixtures=("JC-A1", "JC-A0"),
    rationale=(
        "Highest weight (0.40) because this is the one failure mode where two "
        "independent static checks both went green while the picture was "
        "genuinely broken. A real project in this corpus shipped 72 texture "
        "files and 35 albedo_texture references -- an asset-count check passes, "
        "a material-reference check passes -- and its walls, floors and "
        "ceilings were flat colour, because every texture had gone to weapons "
        "and props.\n\n"
        "HONEST STATEMENT OF THE EVIDENCE BASE. The entire justification for "
        "S1 rests on that one case, and THE CASE WAS FOUND BY A HUMAN, NOT BY "
        "A VLM. There is at present no evidence that any VLM can find it. "
        "Fixture JC-A1 is the existence test: a constructed pair, identical "
        "geometry and lighting, one variant with textured surfaces and one "
        "with the surface materials replaced by their mean colour. If a judge "
        "cannot separate that pair -- while also reporting no difference on "
        "the JC-A0 null control, which is the good variant against itself -- "
        "then S1's own argument refutes it: the channel would be claiming to "
        "catch a failure that its instrument demonstrably cannot see. In that "
        "case S1 must be REMOVED, not kept at reduced weight and not kept as "
        "decoration. A channel that cannot discriminate does not contribute a "
        "weak signal; it contributes noise at weight 0.40."
    ),
)

S2 = Rubric(
    id="S2",
    name="ui_hygiene",
    weight=S_CARD_SUBWEIGHTS["S2"],
    question=(
        "Does the rendered UI hold together: text inside its container, "
        "nothing important occluded, and readable text sitting on something "
        "rather than directly on the sky?"
    ),
    bands=(
        Band(1.00, "clean",
             "All text fits its container with margin. Nothing overlaps "
             "anything it should not. Every text run has a backing plate, an "
             "outline, or a contrast ratio that survives the busiest "
             "background it appears over."),
        Band(0.75, "one cosmetic defect",
             "One instance of tight-but-legible text, or one element that "
             "clips a decorative edge. No information is lost."),
        Band(0.50, "information sometimes lost",
             "Text overflows its container or leaves the frame edge, or an "
             "unbacked label sits on a background it partly matches, in some "
             "of the sampled frames. A player would misread it at least once."),
        Band(0.25, "information routinely lost",
             "Overflow, occlusion or unbacked-on-sky text in most sampled "
             "frames. Numbers are truncated; labels run under other panels."),
        Band(0.00, "UI unreadable or absent",
             "No HUD where the game needs one, or a HUD whose text cannot be "
             "read at native resolution."),
    ),
    scorer=Scorer.JUDGE_OR_HUMAN,
    evidence_required=(
        "frames captured at a state where the HUD is populated, not at boot",
        "frames at native capture resolution, never downscaled",
    ),
    inferable_from=("V",),
    calibration_fixtures=("JC-B1", "JC-A0"),
    rationale=(
        "Needs semantic relationships in the rendered scene that static "
        "analysis cannot read. A Label's rect and its font size are both in "
        "the .tscn; whether the string that arrives at runtime fits inside "
        "that rect, on top of that particular sky, is only visible in pixels. "
        "0.25 rather than S1's 0.40 because a local heuristic recovers part of "
        "it honestly -- ink on the frame border is computable -- so the "
        "channel is less dependent on an unvalidated judge."
    ),
)

S3 = Rubric(
    id="S3",
    name="atmosphere_consistency",
    weight=S_CARD_SUBWEIGHTS["S3"],
    question="Do level 1 and level 2 look like they belong to the same game?",
    bands=(
        Band(1.00, "one coherent world",
             "Palette, lighting temperature, contrast range and material "
             "vocabulary read as one art direction across levels. Differences "
             "between levels look intentional -- a different time of day, a "
             "different biome -- not accidental."),
        Band(0.75, "coherent with one outlier",
             "One level departs in a single axis (much brighter, one alien "
             "hue) while still reading as the same production."),
        Band(0.50, "two art directions",
             "The levels look like two different games' assets in one "
             "executable. Neither is broken; they do not belong together."),
        Band(0.25, "incoherent within a level",
             "Inconsistency inside a single level: adjacent surfaces from "
             "unrelated art sets, mismatched lighting on neighbouring props."),
        Band(0.00, "no discernible art direction",
             "Placeholder colours, or every element from a different source "
             "with no unifying treatment."),
    ),
    scorer=Scorer.JUDGE_OR_HUMAN,
    evidence_required=(
        "frames from at least two distinct levels",
        "frames at native capture resolution, never downscaled",
    ),
    inferable_from=("V",),
    calibration_fixtures=("JC-C1", "JC-A0"),
    rationale=(
        "Palette statistics are computable and the local heuristic computes "
        "them, but the computable part answers a narrower question: histogram "
        "distance says the colours differ, not whether the difference reads as "
        "one coherent world. Two levels can share a histogram and still look "
        "like different games, and a deliberate night level moves every "
        "statistic while remaining perfectly coherent. The gestalt judgement "
        "is what the 0.20 is for; the statistic is reported beside it as "
        "evidence, never as the answer."
    ),
)

S4 = Rubric(
    id="S4",
    name="feel",
    weight=S_CARD_SUBWEIGHTS["S4"],
    question=(
        "Does the game feel good to operate: input response, weight, "
        "animation follow-through, hit confirmation, camera behaviour?"
    ),
    bands=(
        Band(1.00, "responsive and weighted",
             "Input registers on the frame it is given, motion has "
             "acceleration and settle, and every consequential action confirms "
             "itself to the player."),
        Band(0.75, "responsive, unremarkable",
             "Input registers promptly and motion is legible, but nothing "
             "reinforces the player's actions: hits land without weight, "
             "landings do not settle, the camera merely follows. Nothing "
             "fights the player and nothing is memorable."),
        Band(0.50, "workable with friction",
             "Noticeable input lag, floaty or sticky movement, or actions that "
             "resolve without confirmation."),
        Band(0.25, "actively unpleasant",
             "The player fights the controls to do ordinary things: overshoot "
             "on every stop, inputs dropped during animations, a camera that "
             "has to be corrected before each action."),
        Band(0.00, "not operable as a game",
             "Input and outcome are effectively unrelated: the rater cannot "
             "form a model of what any control does, or the game stops "
             "responding partway through an ordinary session."),
    ),
    scorer=Scorer.HUMAN_ONLY,
    evidence_required=(
        "a hands-on session log by a named rater, with duration",
        "the rater's rubric version",
    ),
    inferable_from=("V_temporal", "interaction"),
    calibration_fixtures=(),
    rationale=(
        "HUMAN-SCORED ONLY. The VLM does not vote here and `score_s4` will not "
        "let it (§11.3, weights.S_CARD_HUMAN_ONLY). Feel is a property of the "
        "relationship between input and response over time; a judge handed "
        "still frames has no access to it, exactly as a model at tier D1 has "
        "no access to audio. The judge has an `inferable_from` too, and this "
        "channel is outside it.\n\n"
        "Absent a human record the honest output is `unmeasurable`, which "
        "leaves the denominator and is reported. It is not 0, not the mean of "
        "the other channels, and not a number the judge produced anyway -- a "
        "fabricated 0.6 here would be indistinguishable in the artifact from a "
        "measured 0.6, which is the specific defect this rule exists to "
        "prevent."
    ),
)

RUBRICS: dict[str, Rubric] = {r.id: r for r in (S1, S2, S3, S4)}


def _check_registration() -> None:

    if set(RUBRICS) != set(S_CARD_SUBWEIGHTS):
        raise ValueError(
            f"rubric ids {sorted(RUBRICS)} do not match registered S-card "
            f"sub-weights {sorted(S_CARD_SUBWEIGHTS)}"
        )
    for rid, r in RUBRICS.items():
        if abs(r.weight - S_CARD_SUBWEIGHTS[rid]) > 1e-9:
            raise ValueError(f"{rid}: rubric weight {r.weight} != registered "
                             f"{S_CARD_SUBWEIGHTS[rid]}")
        if r.human_only != (rid in S_CARD_HUMAN_ONLY):
            raise ValueError(f"{rid}: human_only disagrees with weights.S_CARD_HUMAN_ONLY")
        if (not r.human_only) != (rid in S_CARD_VLM_CHANNELS):
            raise ValueError(f"{rid}: judgeable flag disagrees with weights.S_CARD_VLM_CHANNELS")
        credits = [b.credit for b in r.bands]
        if credits != sorted(credits, reverse=True):
            raise ValueError(f"{rid}: bands must be listed best-first")
        if credits != [LEVEL_CREDITS[level] for level in sorted(LEVELS, reverse=True)]:
            raise ValueError(f"{rid}: bands must sit exactly on the five anchored levels")


_check_registration()


@dataclass(frozen=True)
class Exclusion:


    id: str
    description: str
    reason: str

    goes_to: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "reason": self.reason,
            "goes_to": self.goes_to,
        }


OUT_OF_SCOPE: dict[str, Exclusion] = {
    e.id: e
    for e in (
        Exclusion(
            "resemblance_to_reference",
            "Does the submission look like the reference game?",
            "This is a computation, not a judgement: nine-box bearing, 30% mass "
            "and mirror similarity against the designated anchor frame are "
            "deterministic and reproducible. Routing it through a judge would "
            "replace an exact answer with an opinion, and would make the same "
            "quantity appear twice in the total.",
            "O9 anchor_composition (0.087, pure computation, no VLM)",
        ),
        Exclusion(
            "fun",
            "Is the game fun?",
            "No ground truth exists, so no disagreement between raters can ever "
            "be resolved, and the judge's own priors -- genre familiarity, art "
            "style preference -- dominate the reading. This is the maximum-bias "
            "question and it earns weight for resembling the judge's taste.",
            "nowhere; it is not a benchmark quantity",
        ),
        Exclusion(
            "duplicate_of_objective_channel",
            "Anything an objective channel already scores: asset coverage, "
            "level topology, mechanic fidelity, whether it boots.",
            "A dimension collinear with an objective channel is provably "
            "redundant -- `scorer.dart_zero` will report it as inert, because "
            "removing it cannot move a ranking that the objective channel "
            "already fixes -- while still consuming S-card weight and adding "
            "the judge's variance to a number that was previously exact.",
            "the objective channel that already covers it (O3, O4, O6, O1)",
        ),
    )
}


class OutOfScope(ValueError):
    pass


def assert_in_scope(dimension_id: str) -> None:


    if dimension_id in OUT_OF_SCOPE:
        e = OUT_OF_SCOPE[dimension_id]
        raise OutOfScope(
            f"{dimension_id!r} is excluded from the S-card: {e.reason} "
            f"It belongs to: {e.goes_to}."
        )
    if dimension_id not in RUBRICS:
        raise KeyError(
            f"{dimension_id!r} is not an S-card channel; registered channels are "
            f"{sorted(RUBRICS)}. Adding one is a re-registration in weights.py."
        )


@dataclass(frozen=True)
class CalibrationFixture:


    id: str
    channel: str
    kind: str
    description: str
    expectation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "channel": self.channel,
            "kind": self.kind,
            "description": self.description,
            "expectation": self.expectation,
        }


CALIBRATION_FIXTURES: dict[str, CalibrationFixture] = {
    f.id: f
    for f in (
        CalibrationFixture(
            "JC-A0", "*", "null_control",
            "The good variant of JC-A1 judged against itself, captured twice "
            "from two separate evaluator runs so run-to-run render noise is "
            "inside the control.",
            "No difference reported on any channel. A judge that reports a "
            "difference here has no usable resolution and every defect it "
            "'finds' elsewhere is unattributable.",
        ),
        CalibrationFixture(
            "JC-A1", "S1", "defect",
            "Identical scene, identical geometry, identical lighting and "
            "identical prop set. Variant A keeps the surface materials; "
            "variant B replaces every wall, floor and ceiling material with "
            "its own mean colour. This reconstructs the corpus_reference failure "
            "under controlled conditions.",
            "The judge scores variant B at least two bands below variant A on "
            "S1. THIS IS THE EXISTENCE TEST FOR S1. No VLM has been run "
            "against it as of this writing; until one has, S1 is uncalibrated "
            "and the card carrying it may not enter a main table.",
        ),
        CalibrationFixture(
            "JC-B1", "S2", "defect",
            "Identical scene with one HUD label's string lengthened until it "
            "overflows its container and crosses the frame edge, and one "
            "unbacked label moved from a panel onto open sky.",
            "The judge scores the defect variant at least two bands below on "
            "S2 and names overflow or unbacked text in its rationale.",
        ),
        CalibrationFixture(
            "JC-C1", "S3", "defect",
            "Level 2's frames replaced by frames from an unrelated project "
            "with a different palette and art vocabulary, presented as the "
            "same submission's second level.",
            "The judge scores S3 at or below the 0.50 band. Paired with JC-A0 "
            "so that 'always says inconsistent' does not pass.",
        ),
    )
}


def required_fixtures(channels: Sequence[str] = tuple(RUBRICS)) -> list[str]:

    out: list[str] = []
    for cid in channels:
        for fid in RUBRICS[cid].calibration_fixtures:
            if fid not in out:
                out.append(fid)
    return out


@dataclass
class HumanRecord:


    channel: str
    credit: float
    rater_id: str
    rubric_version: str = RUBRIC_VERSION
    detail: str = ""
    session_minutes: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.channel not in RUBRICS:
            raise KeyError(f"{self.channel!r} is not an S-card channel")
        if not 0.0 <= self.credit <= 1.0:
            raise ValueError(f"{self.channel}: credit {self.credit} out of [0,1]")
        if not self.rater_id:
            raise ValueError(
                f"{self.channel}: a human record without a named rater is not a "
                "human record; it is an anonymous number that cannot be re-asked"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "credit": self.credit,
            "rater_id": self.rater_id,
            "rubric_version": self.rubric_version,
            "detail": self.detail,
            "session_minutes": self.session_minutes,
            "evidence": self.evidence,
        }


def score_s4(human_records: Sequence[HumanRecord], *, weight: float | None = None) -> Item:


    w = S_CARD_SUBWEIGHTS["S4"] if weight is None else weight
    records = [r for r in human_records if r.channel == "S4"]
    if not records:
        return unmeasurable(
            "S4",
            weight=w,
            detail=(
                "no human record for feel. S4 is human-scored only (§11.3): a "
                "judge given still frames cannot perceive input response, "
                "weight or follow-through, so the honest output is "
                "'not measured', not a number."
            ),
            evidence={"rubric_version": RUBRIC_VERSION, "human_records": 0},
        )
    stale = [r.rater_id for r in records if r.rubric_version != RUBRIC_VERSION]
    credit = sum(r.credit for r in records) / len(records)
    return passed(
        "S4",
        weight=w,
        credit=credit,
        detail=(
            f"{len(records)} human record(s), mean credit {credit:.3f} "
            f"({RUBRICS['S4'].band_for(credit).label})"
            + (f"; scored against a stale rubric version by {stale}" if stale else "")
        ),
        evidence={
            "rubric_version": RUBRIC_VERSION,
            "records": [r.to_dict() for r in records],
            "stale_rubric_raters": stale,
            "spread": (max(r.credit for r in records) - min(r.credit for r in records)),
        },
    )


def rubric_document() -> dict[str, Any]:

    return {
        "rubric_version": RUBRIC_VERSION,
        "rubric_markdown_sha256": rubric_markdown_sha256(),
        "levels": {str(level): credit for level, credit in LEVEL_CREDITS.items()},
        "default_level": DEFAULT_LEVEL,
        "judging_rules": judging_rules(),
        "channels": {cid: r.to_dict() for cid, r in RUBRICS.items()},
        "out_of_scope": {k: e.to_dict() for k, e in OUT_OF_SCOPE.items()},
        "calibration_fixtures": {k: f.to_dict() for k, f in CALIBRATION_FIXTURES.items()},
        "human_only": list(S_CARD_HUMAN_ONLY),
        "judgeable": list(S_CARD_VLM_CHANNELS),
    }


__all__ = [
    "DEFAULT_LEVEL",
    "LEVELS",
    "LEVEL_CREDITS",
    "RUBRIC_MARKDOWN_PATH",
    "RUBRIC_VERSION",
    "Band",
    "CALIBRATION_FIXTURES",
    "CalibrationFixture",
    "Exclusion",
    "HumanRecord",
    "OUT_OF_SCOPE",
    "OutOfScope",
    "RUBRICS",
    "Rubric",
    "S1",
    "S2",
    "S3",
    "S4",
    "Scorer",
    "assert_in_scope",
    "credit_to_level",
    "judging_rules",
    "level_to_credit",
    "required_fixtures",
    "rubric_document",
    "rubric_markdown",
    "rubric_markdown_sha256",
    "score_s4",
]
