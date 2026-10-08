


from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..assertions import Ceiling
from ..verdict import (
    Attribution,
    Interval,
    Item,
    LOW_COVERAGE_THRESHOLD,
    Verdict,
    combine,
    dump,
)
from ..weights import CHANNELS, O_CARD_SHARE, RETIRED_CHANNELS
from .channels import ChannelResult


@dataclass
class ShortcutFinding:


    id: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "detail": self.detail, "evidence": self.evidence}


@dataclass
class OCardResult:


    project: str
    tier: str
    modality: str
    score_lo: float
    score_hi: float
    coverage: float
    denominator: float
    ceiling: float
    normalised_lo: float | None
    normalised_hi: float | None
    restricted_ceiling: float | None
    normalised_lo_restricted: float | None
    measured_weight_share: float
    """Share of registered O-card weight that produced any reading at all.

    Separate from `coverage`, which only sees skipped items *inside* the
    channels that ran. A card where seven of nine channels never reported can
    still show `coverage == 1.0`, and that number on its own is an invitation to
    misread two measured channels as a whole evaluation.
    """
    per_channel: dict[str, dict[str, Any]]
    unmeasurable_channels: list[str]
    unobservable_channels: list[str]
    inconclusive_rate: float
    unattributable_error_rate: float
    shortcut_detected: bool
    shortcuts: list[ShortcutFinding]
    low_coverage_channels: list[str]
    low_coverage_total: bool
    by_verdict: dict[str, float]

    exempt_items: list[dict[str, Any]] = field(default_factory=list)
    exempt_weight: float = 0.0
    exempt_count: int = 0
    generated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                    time.gmtime()))
    notes: list[str] = field(default_factory=list)

    @property
    def rank_key(self) -> float:

        return self.score_lo

    @property
    def width(self) -> float:
        return self.score_hi - self.score_lo

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "tier": self.tier,
            "modality": self.modality,
            "generated_at": self.generated_at,
            "score_lo": round(self.score_lo, 6),
            "score_hi": round(self.score_hi, 6),
            "width": round(self.width, 6),
            "coverage": round(self.coverage, 6),
            "denominator": round(self.denominator, 6),
            "ceiling": round(self.ceiling, 6),
            "normalised_lo": (None if self.normalised_lo is None
                              else round(self.normalised_lo, 6)),
            "normalised_hi": (None if self.normalised_hi is None
                              else round(self.normalised_hi, 6)),
            "restricted_ceiling": (None if self.restricted_ceiling is None
                                   else round(self.restricted_ceiling, 6)),
            "normalised_lo_restricted": (None if self.normalised_lo_restricted is None
                                         else round(self.normalised_lo_restricted, 6)),
            "measured_weight_share": round(self.measured_weight_share, 6),
            "rank_key": round(self.rank_key, 6),
            "per_channel": self.per_channel,
            "unmeasurable_channels": self.unmeasurable_channels,
            "unobservable_channels": self.unobservable_channels,
            "exempt_count": self.exempt_count,
            "exempt_weight": round(self.exempt_weight, 6),
            "exempt_items": self.exempt_items,
            "inconclusive_rate": round(self.inconclusive_rate, 6),
            "unattributable_error_rate": round(self.unattributable_error_rate, 6),
            "shortcut_detected": self.shortcut_detected,
            "shortcuts": [s.to_dict() for s in self.shortcuts],
            "low_coverage_channels": self.low_coverage_channels,
            "low_coverage_total": self.low_coverage_total,
            "low_coverage_threshold": LOW_COVERAGE_THRESHOLD,
            "by_verdict": {k: round(v, 6) for k, v in self.by_verdict.items()},
            "notes": self.notes,
            "reading_order": [
                "shortcut_detected is a fact reported beside the score, never folded into it",
                "rank on score_lo; a wide interval means coverage, not uncertainty about quality",
                "compare across tiers only through normalised_lo",
            ],
        }

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dump(self.to_dict()), encoding="utf-8")
        return p


def _as_channel_map(
    channel_intervals: Mapping[str, Interval] | Sequence[ChannelResult],
) -> tuple[dict[str, Interval], list[Item], list[str]]:


    retired: list[str] = []
    if isinstance(channel_intervals, Mapping):
        intervals = {
            cid: iv for cid, iv in channel_intervals.items() if cid not in RETIRED_CHANNELS
        }
        retired = sorted(set(channel_intervals) - set(intervals))
        return intervals, [], retired
    intervals = {}
    items: list[Item] = []
    for cr in channel_intervals:
        if cr.channel in RETIRED_CHANNELS:
            retired.append(cr.channel)
            continue
        intervals[cr.channel] = cr.interval
        items.extend(cr.items)
    return intervals, items, sorted(set(retired))


def score_ocard(
    channel_intervals: Mapping[str, Interval] | Sequence[ChannelResult],
    tier: str,
    modality: str,
    ceiling: float | Ceiling,
    *,
    project: str = "",
    shortcuts: Sequence[ShortcutFinding] = (),
    notes: Sequence[str] = (),
) -> OCardResult:


    intervals, items, retired = _as_channel_map(channel_intervals)
    unknown = sorted(set(intervals) - set(CHANNELS))
    if unknown:
        raise KeyError(f"unregistered channels {unknown}; add them to weights.CHANNELS "
                       "(a re-registration) rather than scoring them here")

    weighted: list[tuple[float, Interval]] = []
    per_channel: dict[str, dict[str, Any]] = {}
    unmeasurable: list[str] = []
    unobservable: list[str] = []
    low_cov: list[str] = []

    for cid in sorted(intervals):
        iv = intervals[cid]
        ch = CHANNELS[cid]
        counted = iv.denominator > 0
        per_channel[cid] = {
            "name": ch.name,
            "weight": ch.weight,
            "counted": counted,
            **iv.to_dict(),
        }
        if counted:
            weighted.append((ch.weight, iv))
            if iv.low_coverage:
                low_cov.append(cid)
        elif iv.by_verdict.get(Verdict.UNOBSERVABLE.value):


            unobservable.append(cid)
        else:


            unmeasurable.append(cid)
            weighted.append((ch.weight, Interval(0.0, 1.0, 0.0, 1.0, dict(iv.by_verdict))))
        per_channel[cid]["exempt_weight"] = round(
            iv.by_verdict.get(Verdict.EXEMPT.value, 0.0), 6
        )

    exempt_items: list[dict[str, Any]] = []
    if isinstance(channel_intervals, Mapping):
        for it in items:
            if it.verdict is Verdict.EXEMPT:
                exempt_items.append({
                    "id": it.id,
                    "weight": it.weight,
                    "detail": it.detail,
                    "family": it.id.split("/", 1)[0],
                })
    else:
        for cr in channel_intervals:
            if cr.channel in RETIRED_CHANNELS:
                continue
            for it in cr.items:
                if it.verdict is Verdict.EXEMPT:
                    exempt_items.append({
                        "id": it.id,
                        "weight": it.weight,
                        "detail": it.detail,
                        "channel": cr.channel,
                        "family": it.id.split("/", 1)[0],
                    })
        for cid, entry in per_channel.items():
            entry["exempt_count"] = sum(
                1 for e in exempt_items if e.get("channel") == cid
            )

    exempt_weight = sum(float(e["weight"]) for e in exempt_items)

    total = combine(weighted)

    ceiling_value = ceiling.total if isinstance(ceiling, Ceiling) else float(ceiling)
    if ceiling_value > 0:
        normalised_lo: float | None = total.lo / ceiling_value
        normalised_hi: float | None = total.hi / ceiling_value
    else:
        normalised_lo = normalised_hi = None


    counted = [c for c in intervals if intervals[c].denominator > 0]
    rolled = [c for c in intervals if c not in unobservable]
    restricted_ceiling: float | None = None
    normalised_lo_restricted: float | None = None
    if rolled and isinstance(ceiling, Ceiling):
        w = sum(CHANNELS[c].weight for c in rolled)
        reach = sum(CHANNELS[c].weight * ceiling.per_channel.get(c, 1.0) for c in rolled)
        restricted_ceiling = (reach / w) if w else None
        if restricted_ceiling:
            normalised_lo_restricted = total.lo / restricted_ceiling

    registered_weight = sum(c.weight for c in CHANNELS.values())
    measured_weight_share = (
        sum(CHANNELS[c].weight for c in counted) / registered_weight
        if registered_weight else 0.0
    )

    weight_total = sum(total.by_verdict.values())
    inconclusive_w = total.by_verdict.get(Verdict.INCONCLUSIVE.value, 0.0)
    inconclusive_rate = (inconclusive_w / weight_total) if weight_total else 0.0

    unattributable_w = sum(
        i.weight for i in items if i.attribution is Attribution.UNATTRIBUTABLE
    )
    item_weight = sum(i.weight for i in items)
    unattributable_rate = (unattributable_w / item_weight) if item_weight else 0.0

    note_list = list(notes)
    for cid in retired:
        note_list.append(
            f"{cid} row ignored: retired channel ({RETIRED_CHANNELS[cid]})")
    if measured_weight_share < LOW_COVERAGE_THRESHOLD:
        note_list.append(
            f"only {measured_weight_share * 100:.0f}% of registered O-card weight produced any "
            f"reading ({', '.join(sorted(counted))}); coverage={total.coverage:.2f} describes "
            "those channels alone and must not be read as coverage of the card.")
    if normalised_lo is not None and normalised_lo > 1.0:
        note_list.append(
            "normalised_lo exceeds 1.0: the roll-up covers a subset of channels while the "
            "ceiling covers the whole card. Use normalised_lo_restricted for this run.")
    if unmeasurable:
        note_list.append(
            "channels with no reading stay in the denominator as [0, weight]: "
            + ", ".join(unmeasurable)
            + ". score_lo treats them as unknown, not as zero; score_hi keeps their full weight.")
    for cid in unmeasurable:
        if CHANNELS[cid].never_unmeasurable:
            note_list.append(
                f"{cid} is registered never_unmeasurable and still produced no reading; that is "
                "a harness bug, because being unmeasurable is what that channel measures.")
    if exempt_items:
        note_list.append(
            f"{len(exempt_items)} genre-exempt item(s) (weight {exempt_weight:.3f}) stay in "
            "the denominator with lo=0/hi=full; interval width records the exemption. "
            "See exempt_items."
        )

    return OCardResult(
        project=project,
        tier=tier,
        modality=modality,
        score_lo=total.lo,
        score_hi=total.hi,
        coverage=total.coverage,
        denominator=total.denominator,
        ceiling=ceiling_value,
        normalised_lo=normalised_lo,
        normalised_hi=normalised_hi,
        restricted_ceiling=restricted_ceiling,
        normalised_lo_restricted=normalised_lo_restricted,
        measured_weight_share=measured_weight_share,
        per_channel=per_channel,
        unmeasurable_channels=unmeasurable,
        unobservable_channels=unobservable,
        exempt_items=exempt_items,
        exempt_weight=exempt_weight,
        exempt_count=len(exempt_items),
        inconclusive_rate=inconclusive_rate,
        unattributable_error_rate=unattributable_rate,
        shortcut_detected=bool(shortcuts),
        shortcuts=list(shortcuts),
        low_coverage_channels=low_cov,
        low_coverage_total=total.low_coverage if total.denominator > 0 else True,
        by_verdict=dict(total.by_verdict),
        notes=note_list,
    )


def rank(results: Sequence[OCardResult]) -> list[OCardResult]:


    return sorted(results, key=lambda r: (-r.rank_key, r.width, r.project))


def write_artifact(
    result: OCardResult,
    out_dir: str | Path,
    *,
    channels: Sequence[ChannelResult] = (),
    provenance: Mapping[str, Any] | None = None,
) -> Path:


    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ocard": result.to_dict(),
        "channels": [c.to_dict() for c in channels],
        "provenance": dict(provenance or {}),
        "o_card_share": O_CARD_SHARE,
    }
    path = out_dir / f"ocard-{result.project or 'unnamed'}-{result.tier}-{result.modality}.json"
    path.write_text(dump(payload), encoding="utf-8")
    return path
