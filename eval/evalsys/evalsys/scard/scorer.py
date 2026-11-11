


from __future__ import annotations

import enum
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from ..verdict import (
    Interval,
    Item,
    Verdict,
    dump,
    inconclusive,
    passed,
    score_items,
    unobservable,
)
from ..weights import O_CARD_SHARE, S_CARD_SHARE, S_CARD_SUBWEIGHTS
from .judge import (
    RETEST_STABLE_RANGE,
    CalibrationReport,
    ChannelSpread,
    JudgeResult,
    RetestReport,
    channel_spreads,
)
from .rubric import RUBRIC_VERSION, RUBRICS, HumanRecord, score_s4


O1_GATE_LO = 0.75


DOMINANCE_RHO = 0.95


class CalibrationState(str, enum.Enum):


    UNCALIBRATED = "uncalibrated"
    CALIBRATED = "calibrated"
    FAILED = "failed"


class UncalibratedError(RuntimeError):
    pass


@dataclass
class O1Gate:


    passed: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def gate_from_interval(interval: Interval, *, required_lo: float = O1_GATE_LO) -> O1Gate:


    if interval.denominator <= 0:
        return O1Gate(False, "O1 produced no reading at all; without evidence that the "
                             "game runs, its frames are not evidence about anything",
                      interval.to_dict())
    ok = interval.lo >= required_lo - 1e-9
    return O1Gate(
        ok,
        (f"O1 score_lo {interval.lo:.3f} "
         f"{'clears' if ok else 'is below'} the {required_lo} gate "
         f"(cold_import + boots + draws_nontrivial)"),
        interval.to_dict(),
    )


def _as_gate(objective_o1: Any) -> O1Gate:
    if isinstance(objective_o1, O1Gate):
        return objective_o1
    if isinstance(objective_o1, Interval):
        return gate_from_interval(objective_o1)
    if isinstance(objective_o1, bool):
        return O1Gate(objective_o1, f"caller asserted O1 runnable={objective_o1}")
    if objective_o1 is None:
        return O1Gate(False, "no O1 reading was supplied; the gate is closed by "
                             "default, because an unmeasured gate is not an open one")
    raise TypeError(f"objective_o1 must be O1Gate, Interval, bool or None; got "
                    f"{type(objective_o1)}")


@dataclass
class SCardResult:


    project: str
    applicable: bool
    interval: Interval
    items: list[Item]
    per_channel: dict[str, dict[str, Any]]
    retest_spread: dict[str, dict[str, Any]]
    calibration_state: CalibrationState
    calibration_detail: str
    judge_id: str
    judge_kind: str
    model: str
    o1_gate: O1Gate
    rubric_version: str = RUBRIC_VERSION
    notes: list[str] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )

    @property
    def score_lo(self) -> float | None:

        if not self.applicable or self.interval.denominator <= 0:
            return None
        return self.interval.lo

    @property
    def score_hi(self) -> float | None:
        if not self.applicable or self.interval.denominator <= 0:
            return None
        return self.interval.hi

    @property
    def measured_weight_share(self) -> float:


        registered = sum(S_CARD_SUBWEIGHTS.values())
        measured = sum(
            i.weight for i in self.items if i.verdict.in_denominator
        )
        return measured / registered if registered else 0.0

    @property
    def weight_in_total(self) -> float:

        return S_CARD_SHARE if self.score_lo is not None else 0.0

    @property
    def main_table_eligible(self) -> bool:

        return (
            self.calibration_state is CalibrationState.CALIBRATED
            and self.applicable
            and self.score_lo is not None
        )

    def assert_main_table_eligible(self) -> None:
        if self.main_table_eligible:
            return
        raise UncalibratedError(
            f"{self.project or 'unnamed'}: this S-card may not enter a main "
            f"table (calibration_state={self.calibration_state.value}, "
            f"applicable={self.applicable}, score_lo={self.score_lo}). "
            f"{self.calibration_detail} A judge that has not been shown to "
            "separate a constructed defect from its own null control has not "
            "been shown to measure anything, and a number from it in a main "
            "table is a claim the evidence does not support."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "generated_at": self.generated_at,
            "rubric_version": self.rubric_version,
            "applicable": self.applicable,
            "score_lo": self.score_lo,
            "score_hi": self.score_hi,
            "weight_in_total": self.weight_in_total,
            "measured_weight_share": round(self.measured_weight_share, 6),
            "s_card_share": S_CARD_SHARE,
            "interval": self.interval.to_dict(),
            "calibration_state": self.calibration_state.value,
            "calibration_detail": self.calibration_detail,
            "main_table_eligible": self.main_table_eligible,
            "judge_id": self.judge_id,
            "judge_kind": self.judge_kind,
            "model": self.model,
            "o1_gate": self.o1_gate.to_dict(),
            "per_channel": self.per_channel,
            "retest_spread": self.retest_spread,
            "items": [i.to_dict() for i in self.items],
            "notes": self.notes,
        }

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dump(self.to_dict()), encoding="utf-8")
        return p


def _na_card(project: str, gate: O1Gate, note: str) -> SCardResult:
    return SCardResult(
        project=project,
        applicable=False,
        interval=Interval(0.0, 0.0, 0.0, 0.0, {}),
        items=[],
        per_channel={},
        retest_spread={},
        calibration_state=CalibrationState.UNCALIBRATED,
        calibration_detail="not evaluated: the card does not apply",
        judge_id="",
        judge_kind="",
        model="",
        o1_gate=gate,
        notes=[note],
    )


def _normalise_runs(judge_results: Any) -> list[JudgeResult]:
    if judge_results is None:
        return []
    if isinstance(judge_results, JudgeResult):
        return [judge_results]
    if isinstance(judge_results, RetestReport):
        return list(judge_results.runs)
    runs: list[JudgeResult] = []
    for r in judge_results:
        if isinstance(r, RetestReport):
            runs.extend(r.runs)
        else:
            runs.append(r)
    return runs


def score_scard(
    judge_results: Any,
    human_records: Sequence[HumanRecord] = (),
    objective_o1: Any = None,
    *,
    project: str = "",
    calibration: CalibrationReport | None = None,
    unstable_policy: str = "inconclusive",
    notes: Sequence[str] = (),
) -> SCardResult:


    gate = _as_gate(objective_o1)
    if not gate.passed:
        return _na_card(
            project, gate,
            "S-CARD n/a: " + gate.detail + ". The card leaves the weighting "
            "entirely (weight_in_total=0.0) and the total is the objective "
            "score alone. It is not scored 0 -- nobody was asked to judge a "
            "black screen, and O1 has already priced this failure.",
        )

    runs = _normalise_runs(judge_results)
    judge_ids = {r.judge_id for r in runs}
    if len(judge_ids) > 1:
        raise ValueError(
            f"runs from {sorted(judge_ids)} cannot be folded into one card. "
            "Score each judge separately and compare them; a mixed average "
            "cannot be traced back to the instrument that produced it."
        )
    judge_id = next(iter(judge_ids), "")
    judge_kind = runs[0].judge_kind if runs else ""
    model = runs[0].model if runs else ""

    spreads: dict[str, ChannelSpread] = channel_spreads(runs) if runs else {}
    if isinstance(judge_results, RetestReport):
        spreads = judge_results.per_channel

    human_by_channel: dict[str, list[HumanRecord]] = {}
    for rec in human_records:
        human_by_channel.setdefault(rec.channel, []).append(rec)

    items: list[Item] = []
    per_channel: dict[str, dict[str, Any]] = {}
    note_list = list(notes)

    for cid, rubric in RUBRICS.items():
        weight = S_CARD_SUBWEIGHTS[cid]
        if rubric.human_only:
            item = score_s4(human_records, weight=weight)
            items.append(item)
            per_channel[cid] = {
                "name": rubric.name, "weight": weight, "scored_by": "human",
                "verdict": item.verdict.value, "credit": item.credit,
                "detail": item.detail,
            }
            continue

        humans = human_by_channel.get(cid, [])
        if humans:
            credit = sum(h.credit for h in humans) / len(humans)
            item = passed(
                cid, weight=weight, credit=credit,
                detail=(f"scored by {len(humans)} human rater(s) "
                        f"({', '.join(h.rater_id for h in humans)}); the judge's "
                        "reading is retained in evidence but does not vote when a "
                        "human has looked"),
                evidence={"human": [h.to_dict() for h in humans],
                          "judge_runs": [r.channels[cid].to_dict()
                                         for r in runs if cid in r.channels]},
            )
            items.append(item)
            per_channel[cid] = {
                "name": rubric.name, "weight": weight, "scored_by": "human",
                "verdict": item.verdict.value, "credit": credit, "detail": item.detail,
            }
            continue

        readings = [r.channels[cid] for r in runs if cid in r.channels]
        scored = [c for c in readings if c.verdict is Verdict.PASSED and c.credit is not None]
        spread = spreads.get(cid)

        if not readings or any(not r.provider_available for r in runs):
            item = inconclusive(
                cid, weight=weight,
                detail=("a judge run produced no available reading; an unread channel is "
                        "out of the denominator and reported, never a pass"),
            )
        elif not scored:
            first = readings[0]
            verdict = first.verdict
            detail = first.rationale
            if all(c.verdict is Verdict.UNOBSERVABLE for c in readings) and len(readings) == len(runs):
                item = unobservable(cid, weight=weight, detail=detail)
            else:
                item = inconclusive(cid, weight=weight, detail=detail,
                                    evidence={"provider_available":
                                              all(r.provider_available for r in runs)})
        elif len(scored) != len(runs):
            item = inconclusive(
                cid, weight=weight,
                detail=f"only {len(scored)}/{len(runs)} repeat judgments measured this channel",
                evidence={"runs": [c.to_dict() for c in readings]},
            )
        elif (spread is not None and spread.n_scored >= 2 and not spread.stable
              and unstable_policy == "inconclusive"):
            item = inconclusive(
                cid, weight=weight,
                detail=(f"{spread.n_scored} runs of the same judge on the same "
                        f"frames produced {spread.values} (range "
                        f"{spread.range:.3f}, bands {spread.distinct_bands}). A "
                        "reading that does not reproduce is not a reading; the "
                        "mean of it would look exactly like a measurement."),
                evidence=spread.to_dict(),
            )
            note_list.append(
                f"{cid} was dropped to inconclusive for retest instability: "
                f"range {spread.range:.3f} over the {RETEST_STABLE_RANGE} bar, "
                f"bands {spread.distinct_bands}."
            )
        else:
            credit = sum(c.credit for c in scored) / len(scored)
            item = passed(
                cid, weight=weight, credit=credit,
                detail=(f"{judge_kind} judge, mean of {len(scored)} run(s): "
                        f"{credit:.3f} ({rubric.band_for(credit).label})"),
                evidence={"runs": [c.to_dict() for c in scored],
                          "spread": spread.to_dict() if spread else None},
            )
        items.append(item)
        per_channel[cid] = {
            "name": rubric.name, "weight": weight,
            "scored_by": judge_kind or "none",
            "verdict": item.verdict.value, "credit": item.credit,
            "detail": item.detail,
        }

    interval = score_items(items)

    state, cal_detail = _calibration_state(calibration, runs)
    if state is not CalibrationState.CALIBRATED:
        note_list.append(
            "This card is not main-table eligible: " + cal_detail
        )
    if judge_kind == "local_heuristic":
        note_list.append(
            "Scored by a LOCAL HEURISTIC, not a VLM. Its numbers are pixel "
            "statistics -- flatness, edge ink, palette distance -- and must not "
            "be reported as a model's visual judgement."
        )

    return SCardResult(
        project=project,
        applicable=True,
        interval=interval,
        items=items,
        per_channel=per_channel,
        retest_spread={c: s.to_dict() for c, s in spreads.items()},
        calibration_state=state,
        calibration_detail=cal_detail,
        judge_id=judge_id,
        judge_kind=judge_kind,
        model=model,
        o1_gate=gate,
        notes=note_list,
    )


def _calibration_state(
    calibration: CalibrationReport | None, runs: Sequence[JudgeResult]
) -> tuple[CalibrationState, str]:
    if calibration is None:
        return (
            CalibrationState.UNCALIBRATED,
            "no calibration report was supplied. The judge has not been shown "
            "to separate rubric.CALIBRATION_FIXTURES' constructed defects from "
            "the JC-A0 null control, so its readings are numbers, not "
            "measurements.",
        )
    judgeable = [c for c in RUBRICS if not RUBRICS[c].human_only]
    ok = calibration.calibrated_channels
    missing = [c for c in judgeable if c not in ok]
    if not missing:
        return (
            CalibrationState.CALIBRATED,
            f"{calibration.judge_id} passed the defect fixture and the null "
            f"control for {ok}",
        )
    failed = [o.fixture_id for o in calibration.outcomes if not o.passed]
    state = CalibrationState.FAILED if failed else CalibrationState.UNCALIBRATED
    return (
        state,
        f"channels {missing} are not calibrated for {calibration.judge_id}"
        + (f"; fixtures that failed: {failed}. A channel whose existence test "
           "fails must be removed, not carried at reduced weight." if failed
           else "; their fixtures were never run."),
    )


def main_table(results: Sequence[SCardResult]) -> list[SCardResult]:


    for r in results:
        r.assert_main_table_eligible()
    return list(results)


def _rankdata(values: Sequence[float]) -> np.ndarray:

    a = np.asarray(values, dtype=float)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1, dtype=float)

    sorted_vals = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return ranks


def spearman(a: Sequence[float], b: Sequence[float]) -> float:


    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} vs {len(b)}")
    if len(a) < 2:
        return float("nan")
    ra, rb = _rankdata(a), _rankdata(b)
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


@dataclass
class RankMove:
    project: str
    from_rank: int
    to_rank: int
    objective_score: float
    combined_score: float

    @property
    def delta(self) -> int:
        return self.from_rank - self.to_rank

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"delta": self.delta}


@dataclass
class TotalTable:


    objective_only: dict[str, float]
    combined: dict[str, float]
    scard_lo: dict[str, float | None]
    scard_weight_used: dict[str, float]
    objective_order: list[str]
    combined_order: list[str]
    spearman: float
    moves: list[RankMove]
    notes: list[str] = field(default_factory=list)

    @property
    def ranking_changed(self) -> bool:
        return self.objective_order != self.combined_order

    def to_dict(self) -> dict[str, Any]:
        return {
            "o_card_share": O_CARD_SHARE,
            "s_card_share": S_CARD_SHARE,
            "objective_only": {k: round(v, 6) for k, v in self.objective_only.items()},
            "combined": {k: round(v, 6) for k, v in self.combined.items()},
            "scard_lo": {k: (None if v is None else round(v, 6))
                         for k, v in self.scard_lo.items()},
            "scard_weight_used": self.scard_weight_used,
            "objective_order": self.objective_order,
            "combined_order": self.combined_order,
            "spearman": None if math.isnan(self.spearman) else round(self.spearman, 6),
            "ranking_changed": self.ranking_changed,
            "moves": [m.to_dict() for m in self.moves],
            "notes": self.notes,
        }


def score_totals(
    objective_lo: Mapping[str, float],
    scards: Mapping[str, SCardResult],
) -> TotalTable:


    combined: dict[str, float] = {}
    scard_lo: dict[str, float | None] = {}
    weight_used: dict[str, float] = {}
    notes: list[str] = []

    for project, o in objective_lo.items():
        card = scards.get(project)
        s = card.score_lo if card else None
        scard_lo[project] = s
        if s is None:
            combined[project] = float(o)
            weight_used[project] = 0.0
            notes.append(
                f"{project}: S-card n/a"
                + (f" ({card.notes[0]})" if card and card.notes else "")
                + "; total is the objective score with the S-card out of the "
                "weighting, not an S-card of 0."
            )
        else:
            combined[project] = O_CARD_SHARE * float(o) + S_CARD_SHARE * float(s)
            weight_used[project] = S_CARD_SHARE
            if card is not None and not card.main_table_eligible:
                notes.append(
                    f"{project}: the S-card contributing to this total is "
                    f"{card.calibration_state.value} and may not appear in a "
                    "main table; this combined number is a development figure."
                )

    def order(scores: Mapping[str, float]) -> list[str]:
        return sorted(scores, key=lambda p: (-scores[p], p))

    o_order = order(objective_lo)
    c_order = order(combined)
    projects = list(objective_lo)
    rho = spearman([objective_lo[p] for p in projects], [combined[p] for p in projects])

    moves = [
        RankMove(p, o_order.index(p) + 1, c_order.index(p) + 1,
                 float(objective_lo[p]), combined[p])
        for p in projects
        if o_order.index(p) != c_order.index(p)
    ]
    moves.sort(key=lambda m: (-abs(m.delta), m.project))
    if moves:
        notes.append(
            "the S-card changed the ranking: "
            + "; ".join(f"{m.project} {m.from_rank}->{m.to_rank} "
                        f"({m.delta:+d} places)" for m in moves)
        )
    return TotalTable(
        objective_only={p: float(v) for p, v in objective_lo.items()},
        combined=combined,
        scard_lo=scard_lo,
        scard_weight_used=weight_used,
        objective_order=o_order,
        combined_order=c_order,
        spearman=rho,
        moves=moves,
        notes=notes,
    )


@dataclass
class Subject:


    project: str
    dimensions: dict[str, float]
    weights: dict[str, float]

    def total(self, override: Mapping[str, float] | None = None) -> float:
        dims = dict(self.dimensions)
        if override:
            dims.update(override)
        num = sum(self.weights.get(k, 0.0) * v for k, v in dims.items())
        den = sum(self.weights.get(k, 0.0) for k in dims)
        return num / den if den else 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DartFinding:


    dimension: str
    mode: str
    n_subjects: int
    weight_share: float
    baseline_order: list[str]
    variant_order: list[str] = field(default_factory=list)
    spearman_vs_baseline: float = float("nan")
    dimension_alone_spearman: float = float("nan")
    moves: list[RankMove] = field(default_factory=list)
    permutation_p5: float | None = None
    permutation_median: float | None = None
    permutations: int = 0
    inert: bool = False
    dominant: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def r(v: float | None) -> float | None:
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return None
            return round(float(v), 6)

        return {
            "dimension": self.dimension,
            "mode": self.mode,
            "n_subjects": self.n_subjects,
            "weight_share": round(self.weight_share, 6),
            "baseline_order": self.baseline_order,
            "variant_order": self.variant_order,
            "spearman_vs_baseline": r(self.spearman_vs_baseline),
            "dimension_alone_spearman": r(self.dimension_alone_spearman),
            "moves": [m.to_dict() for m in self.moves],
            "permutations": self.permutations,
            "permutation_p5": r(self.permutation_p5),
            "permutation_median": r(self.permutation_median),
            "inert": self.inert,
            "dominant": self.dominant,
            "notes": self.notes,
        }


def _order(scores: Mapping[str, float]) -> list[str]:
    return sorted(scores, key=lambda p: (-scores[p], p))


def _weight_share(subjects: Sequence[Subject], dimension: str) -> float:
    tot = 0.0
    share = 0.0
    for s in subjects:
        tot += sum(s.weights.get(k, 0.0) for k in s.dimensions)
        share += s.weights.get(dimension, 0.0)
    return share / tot if tot else 0.0


def _check_dimension(subjects: Sequence[Subject], dimension: str) -> None:
    if len(subjects) < 3:
        raise ValueError(
            f"DART needs at least 3 subjects to say anything about a ranking; "
            f"got {len(subjects)}"
        )
    missing = [s.project for s in subjects if dimension not in s.dimensions]
    if missing:
        raise KeyError(f"{dimension!r} missing for {missing}")


def dart_zero(
    results: Sequence[Subject], dimension: str, *, constant: float | None = None
) -> DartFinding:


    _check_dimension(results, dimension)
    values = [s.dimensions[dimension] for s in results]
    const = float(np.mean(values)) if constant is None else float(constant)

    base = {s.project: s.total() for s in results}
    variant = {s.project: s.total({dimension: const}) for s in results}
    b_order, v_order = _order(base), _order(variant)
    projects = [s.project for s in results]

    rho = spearman([base[p] for p in projects], [variant[p] for p in projects])
    alone = spearman(values, [base[p] for p in projects])

    moves = [
        RankMove(p, b_order.index(p) + 1, v_order.index(p) + 1, base[p], variant[p])
        for p in projects if b_order.index(p) != v_order.index(p)
    ]
    inert = not moves
    dominant = (not math.isnan(alone)) and abs(alone) >= DOMINANCE_RHO

    notes: list[str] = []
    if inert:
        notes.append(
            f"flattening {dimension} to {const:.4f} moved nobody. On this "
            f"subject set the dimension carries no ranking information: it "
            f"consumes {_weight_share(results, dimension) * 100:.1f}% of the "
            "weight and changes no decision. Either the subjects do not vary "
            "on it, or it is collinear with a dimension already in the total."
        )
    if dominant:
        notes.append(
            f"{dimension} alone reproduces the full ranking (rho={alone:.3f}). "
            "That is a design defect in the other direction: every other "
            "dimension in the total is decorative, while the table still "
            "advertises them as contributing."
        )
    if not inert and not dominant:
        notes.append(
            f"{dimension} moved {len(moves)} of {len(results)} subjects "
            f"(rho vs baseline {rho:.3f}); it carries information without "
            "determining the result."
        )
    return DartFinding(
        dimension=dimension, mode="zero", n_subjects=len(results),
        weight_share=_weight_share(results, dimension),
        baseline_order=b_order, variant_order=v_order,
        spearman_vs_baseline=rho, dimension_alone_spearman=alone,
        moves=moves, inert=inert, dominant=dominant, notes=notes,
    )


def dart_permute(
    results: Sequence[Subject], dimension: str, n: int = 1000, *, seed: int = 0
) -> DartFinding:


    _check_dimension(results, dimension)
    rng = np.random.default_rng(seed)
    values = np.array([s.dimensions[dimension] for s in results], dtype=float)
    projects = [s.project for s in results]
    base = {s.project: s.total() for s in results}
    base_vec = [base[p] for p in projects]
    b_order = _order(base)

    rhos: list[float] = []
    for _ in range(n):
        perm = rng.permutation(values)
        variant = [s.total({dimension: float(perm[i])}) for i, s in enumerate(results)]
        r = spearman(base_vec, variant)
        rhos.append(0.0 if math.isnan(r) else r)

    arr = np.array(rhos, dtype=float)
    p5 = float(np.percentile(arr, 5))
    med = float(np.median(arr))
    alone = spearman(values.tolist(), base_vec)
    inert = p5 >= 1.0 - 1e-9
    dominant = (not math.isnan(alone)) and abs(alone) >= DOMINANCE_RHO

    notes = [
        f"{n} permutations of {dimension}: median rho {med:.4f}, 5th percentile "
        f"{p5:.4f}. The dimension holds "
        f"{_weight_share(results, dimension) * 100:.1f}% of the weight."
    ]
    if inert:
        notes.append(
            "every permutation left the ranking identical: on this subject set "
            "the dimension is inert, and the weight it holds is doing nothing."
        )
    if dominant:
        notes.append(
            f"the dimension alone reproduces the baseline ranking "
            f"(rho={alone:.3f}); the other dimensions are decorative."
        )
    return DartFinding(
        dimension=dimension, mode="permute", n_subjects=len(results),
        weight_share=_weight_share(results, dimension),
        baseline_order=b_order,
        spearman_vs_baseline=med,
        dimension_alone_spearman=alone,
        permutation_p5=p5, permutation_median=med, permutations=n,
        inert=inert, dominant=dominant, notes=notes,
    )


def write_scard(result: SCardResult, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = f"scard-{result.project or 'unnamed'}-{result.judge_kind or 'nojudge'}.json"
    return result.write(out / name)


__all__ = [
    "CalibrationState",
    "DOMINANCE_RHO",
    "DartFinding",
    "O1Gate",
    "O1_GATE_LO",
    "RankMove",
    "SCardResult",
    "Subject",
    "TotalTable",
    "UncalibratedError",
    "dart_permute",
    "dart_zero",
    "gate_from_interval",
    "main_table",
    "score_scard",
    "score_totals",
    "spearman",
    "write_scard",
]
