


from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..tasks import all_task_metadata
from ..weights import CHANNELS, MODALITIES, TIERS, InputTier, Modality


INFERABLE_LABELS = frozenset({"V", "V_audio", "A", "A_seen", "G", "S", "N"})


LAYERS = frozenset({"M", "O", "F"})


@dataclass
class Assertion:


    id: str
    channel: str
    layer: str
    inferable_from: frozenset[str]
    weight: float = 1.0
    statement: str = ""


    label_rationale: str = ""

    annotators: dict[str, str] = field(default_factory=dict)


    #: passing it. An unverified label is a guess in a serious-looking table.
    blind_verified: bool | None = None
    negative_mechanic: bool = False
    """A 'this game does NOT have X' claim. Structurally invisible in video
    unless the recording demonstrates a failed attempt (IV3)."""
    tolerance: dict[str, float] = field(default_factory=dict)
    """Per-tier tolerance. The predicate never changes across tiers; only how
    close counts as equal, because that depends on whether the tier gave the
    agent a scale anchor (§7.4)."""

    def __post_init__(self) -> None:
        self.inferable_from = frozenset(self.inferable_from)
        bad = self.inferable_from - INFERABLE_LABELS
        if bad:
            raise ValueError(f"{self.id}: unknown inferable_from labels {sorted(bad)}")
        if not self.inferable_from:
            raise ValueError(f"{self.id}: inferable_from must not be empty; use {{'N'}}")
        if self.layer not in LAYERS:
            raise ValueError(f"{self.id}: unknown layer {self.layer!r}")
        if self.channel not in CHANNELS:
            raise ValueError(f"{self.id}: unknown channel {self.channel!r}")

    def reachable(self, tier: InputTier, modality: Modality) -> bool:

        available = tier.provides | modality.provides
        return bool(self.inferable_from & available)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["inferable_from"] = sorted(self.inferable_from)
        return d


@dataclass
class AuditFinding:
    level: str
    code: str
    message: str


class AssertionSet:


    def __init__(self, task_id: str, assertions: Sequence[Assertion]) -> None:
        self.task_id = task_id
        self.assertions = list(assertions)
        seen: set[str] = set()
        for a in self.assertions:
            if a.id in seen:
                raise ValueError(f"duplicate assertion id {a.id!r}")
            seen.add(a.id)


    def audit(self, *, require_blind: bool = True) -> list[AuditFinding]:

        out: list[AuditFinding] = []

        n_class = [a for a in self.assertions if "N" in a.inferable_from]
        if n_class:
            out.append(AuditFinding(
                "block", "IA-4",
                "scored items labelled N (inferable from no tier): "
                + ", ".join(a.id for a in n_class)
                + ". Either drop them from scoring or state them in the brief; "
                  "scoring them measures luck.",
            ))

        for a in self.assertions:
            if len(a.annotators) < 2:
                out.append(AuditFinding(
                    "warn", "IA-1",
                    f"{a.id}: fewer than two independent annotators. The label is "
                    "not auditable.",
                ))
            if require_blind and a.blind_verified is None and "V" in a.inferable_from:
                out.append(AuditFinding(
                    "block", "IA-2",
                    f"{a.id}: labelled V but no blind-test result. 'I think you can "
                    "see it' is not 'somebody did see it'; without IA-2 the whole "
                    "audit is self-report.",
                ))
            if a.blind_verified is False and "V" in a.inferable_from:
                out.append(AuditFinding(
                    "block", "IA-2",
                    f"{a.id}: labelled V but the blind annotator could not answer it. "
                    "Downgrade to A/G/S.",
                ))
            if a.negative_mechanic and "V" in a.inferable_from and not a.label_rationale:
                out.append(AuditFinding(
                    "block", "IV3",
                    f"{a.id}: a negative mechanic claimed as video-inferable must cite "
                    "the recording segment that demonstrates the failed attempt. "
                    "Absence of the mechanic on screen is not evidence of its absence.",
                ))
            if "S" in a.inferable_from and not a.label_rationale:
                out.append(AuditFinding(
                    "warn", "IA-1",
                    f"{a.id}: moved into the brief without a written reason. Every "
                    "N -> S move costs reproduction signal and must be justified.",
                ))
        return out

    def blind_readability(self) -> float | None:


        v = [a for a in self.assertions if "V" in a.inferable_from]
        answered = [a for a in v if a.blind_verified is not None]
        if not answered:
            return None
        return sum(1 for a in answered if a.blind_verified) / len(answered)


    def channel_reachable_fraction(
        self, channel: str, tier: InputTier, modality: Modality
    ) -> float:

        items = [a for a in self.assertions if a.channel == channel]
        total = sum(a.weight for a in items)
        if total <= 0:
            return 1.0
        got = sum(a.weight for a in items if a.reachable(tier, modality))
        return got / total

    def ceiling(
        self,
        tier: InputTier | str,
        modality: Modality | str,
        *,
        channel_overrides: dict[str, float] | None = None,
    ) -> "Ceiling":


        t = TIERS[tier] if isinstance(tier, str) else tier
        m = MODALITIES[modality] if isinstance(modality, str) else modality
        overrides = channel_overrides or {}

        per_channel: dict[str, float] = {}
        for cid, ch in CHANNELS.items():
            frac = overrides.get(cid)
            if frac is None:
                frac = self.channel_reachable_fraction(cid, t, m)
            per_channel[cid] = frac

        o_weight = sum(c.weight for c in CHANNELS.values())
        o_reach = sum(CHANNELS[c].weight * f for c, f in per_channel.items())


        from ..weights import S_CARD_SHARE

        total_w = o_weight + S_CARD_SHARE
        total = (o_reach + S_CARD_SHARE) / total_w


        fid = FIDELITY_CHANNELS
        fid_w = sum(CHANNELS[c].weight for c in fid)
        fid_reach = sum(CHANNELS[c].weight * per_channel[c] for c in fid)

        return Ceiling(
            tier=t.id,
            modality=m.id,
            total=total,
            fidelity_subset=(fid_reach / fid_w) if fid_w else 1.0,
            per_channel=per_channel,
        )


FIDELITY_CHANNELS = ("O3", "O4", "O5", "O6", "O9")


@dataclass
class Ceiling:
    tier: str
    modality: str
    total: float
    fidelity_subset: float
    per_channel: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "modality": self.modality,
            "total": round(self.total, 4),
            "fidelity_subset": round(self.fidelity_subset, 4),
            "per_channel": {k: round(v, 4) for k, v in self.per_channel.items()},
        }


def load_assertions(path: str | Path) -> AssertionSet:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return AssertionSet(
        task_id=data["task_id"],
        assertions=[Assertion(**a) for a in data["assertions"]],
    )


def write_ceilings(
    aset: AssertionSet,
    out_path: str | Path,
    *,
    channel_overrides_by_tier: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:


    out: dict[str, Any] = {
        "task_id": aset.task_id,
        "note": (
            "Reachable ceilings. Cross-tier comparison uses score/ceiling and "
            "nothing else. The cross-model main table normalises against Mv."
        ),
        "blind_readability": aset.blind_readability(),
        "ceilings": {},
    }
    for tid in TIERS:
        if channel_overrides_by_tier is not None:
            overrides = channel_overrides_by_tier.get(tid, {})
        else:
            overrides = channel_overrides_for(tid, aset.task_id)
        for mid in MODALITIES:
            c = aset.ceiling(tid, mid, channel_overrides=overrides)
            out["ceilings"][f"{tid}/{mid}"] = c.to_dict()

    Path(out_path).write_text(
        json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return out


DEFAULT_CHANNEL_OVERRIDES: dict[str, dict[str, float]] = {
    "D1": {"O6": 0.0},
    "D1.5": {"O6": 1.0},
    "D2": {"O6": 1.0},
    "D3": {"O6": 1.0},
}


TASK_CHANNEL_OVERRIDES: dict[str, dict[str, float]] = {
    task_id: {str(k): float(v) for k, v in metadata["channel_overrides"].items()}
    for task_id, metadata in all_task_metadata().items()
    if isinstance(metadata.get("channel_overrides"), dict)
}


def channel_overrides_for(
    tier: str,
    task_id: str | None = None,
) -> dict[str, float]:

    out = dict(DEFAULT_CHANNEL_OVERRIDES.get(tier, {}))
    if task_id:
        out.update(TASK_CHANNEL_OVERRIDES.get(task_id, {}))
    return out
