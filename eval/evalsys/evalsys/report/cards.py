


from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..verdict import Interval, LOW_COVERAGE_THRESHOLD
from ..weights import CHANNELS, MAIN_TABLE_MODALITY, REGISTRY_VERSION


@dataclass
class ChannelRecord:
    channel: str
    interval: Interval
    weight: float
    mode_used: str = ""
    note: str = ""

    @property
    def measured(self) -> bool:
        return self.interval.denominator > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "name": CHANNELS[self.channel].name if self.channel in CHANNELS else "",
            "weight": self.weight,
            "measured": self.measured,
            "mode_used": self.mode_used,
            "note": self.note,
            **self.interval.to_dict(),
        }


@dataclass
class Card:


    submission: str
    task_id: str
    tier: str
    modality: str = MAIN_TABLE_MODALITY

    channels: list[ChannelRecord] = field(default_factory=list)
    ocard: Interval | None = None
    scard: Interval | None = None
    scard_state: str = "absent"
    """`absent` | `uncalibrated` | `calibrated` | `n/a`. Only `calibrated` may
    contribute to a printed total; an uncalibrated judge in a main table is a
    number with no demonstrated relationship to anything."""

    ceiling_total: float = 1.0
    ceiling_fidelity: float = 1.0
    restricted_ceiling: float | None = None
    """The ceiling recomputed over ONLY the channels that reported.

    `ceiling_total` covers all nine channels. Dividing a roll-up that covers six
    of them by it mixes two denominators, and the mixture is not merely noisy:
    the gold `corpus_reference` card came back at `normalised = 1.011`, a score
    above the stated maximum. A reader who trusts that number concludes the
    submission beat the ceiling; what actually happened is that O8 and O9 (and
    the since-withdrawn O10)
    contributed nothing to the numerator while still shrinking the divisor.

    `None` means the scorer had no per-channel ceiling to restrict, so
    `normalised_lo` falls back to the whole-card figure and the `partial-card`
    flag is the reader's only warning."""

    shortcut_detected: bool = False
    shortcut_evidence: list[str] = field(default_factory=list)
    """Reported beside the score, never inside it. Whether a clear used a
    shortcut is a categorical fact, not a magnitude; docking some number of
    points for it would be wrong at every possible number."""

    unmeasurable_channels: list[str] = field(default_factory=list)
    inconclusive_rate: float = 0.0
    unattributable_error_rate: float = 0.0
    unobservable_hit_rate: float | None = None
    """Pass rate on items this tier cannot convey. Not a score -- it measures
    the model's game-design prior, i.e. how much it guessed right with no
    information, which is a different capability from reproduction."""


    exempt_count: int = 0
    exempt_weight: float = 0.0
    exempt_items: list[dict[str, Any]] = field(default_factory=list)

    provenance: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


    replay_reading: dict[str, Any] | None = None


    @property
    def objective_lo(self) -> float:
        return self.ocard.lo if self.ocard else 0.0

    @property
    def combined_lo(self) -> float:


        if self.scard is None or self.scard_state != "calibrated":
            return self.objective_lo
        return 0.9 * self.objective_lo + 0.1 * self.scard.lo

    @property
    def combined_hi(self) -> float:
        o_hi = self.ocard.hi if self.ocard else 0.0
        if self.scard is None or self.scard_state != "calibrated":
            return o_hi
        return 0.9 * o_hi + 0.1 * self.scard.hi

    @property
    def normalising_ceiling(self) -> float:


        if self.is_partial and self.restricted_ceiling:
            return self.restricted_ceiling
        return self.ceiling_total

    @property
    def is_partial(self) -> bool:
        return self.measured_weight_share < 0.99

    @property
    def normalised_lo(self) -> float:

        ceiling = self.normalising_ceiling
        return self.combined_lo / ceiling if ceiling else 0.0

    @property
    def main_table_eligible(self) -> tuple[bool, str]:


        if self.is_partial:
            return False, (
                f"partial-card: only {self.measured_weight_share:.0%} of the registered "
                f"O-card weight produced a reading ({', '.join(self.unmeasurable_channels)} "
                "reported nothing). Its normalised score has a different denominator "
                "from a complete card and the two must not share a column."
            )
        if self.scard_state == "uncalibrated":
            return False, "the S-card judge is uncalibrated and may not contribute a number"
        return True, ""

    @property
    def coverage(self) -> float:
        return self.ocard.coverage if self.ocard else 0.0

    @property
    def measured_weight_share(self) -> float:


        total = sum(c.weight for c in CHANNELS.values())
        got = sum(c.weight for c in self.channels if c.measured)
        return got / total if total else 0.0

    @property
    def flags(self) -> list[str]:
        out: list[str] = []
        if self.coverage < LOW_COVERAGE_THRESHOLD:
            out.append(f"low-coverage({self.coverage:.2f})")
        if self.measured_weight_share < 0.99:
            out.append(f"partial-card({self.measured_weight_share:.2f} of registered weight)")
        if self.shortcut_detected:
            out.append("shortcut")
        if self.unmeasurable_channels:
            out.append(f"unmeasurable:{','.join(self.unmeasurable_channels)}")
        if self.exempt_count:
            out.append(f"exempt:{self.exempt_count}(w={self.exempt_weight:.3f})")
        if self.scard_state == "uncalibrated":
            out.append("scard-uncalibrated(excluded)")


        for ch in self.channels:
            if ch.measured and ch.interval.lo == 0.0:
                out.append(f"zero:{ch.channel}")
        for axis in self.provenance.get("failed_axes") or []:
            flag = f"failed:{axis}"
            if flag not in out:
                out.append(flag)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission": self.submission,
            "task_id": self.task_id,
            "tier": self.tier,
            "modality": self.modality,
            "weights_registry": REGISTRY_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "score": {
                "combined_lo": round(self.combined_lo, 6),
                "combined_hi": round(self.combined_hi, 6),
                "objective_lo": round(self.objective_lo, 6),
                "normalised_lo": round(self.normalised_lo, 6),
                "coverage": round(self.coverage, 6),
                "measured_weight_share": round(self.measured_weight_share, 6),
            },
            "ceiling": {
                "total": self.ceiling_total,
                "fidelity_subset": self.ceiling_fidelity,
                "restricted": self.restricted_ceiling,
                "used_for_normalisation": round(self.normalising_ceiling, 6),
                "note": (
                    "Cross-tier comparison uses normalised_lo only. A partial card "
                    "normalises against `restricted`, computed over the channels that "
                    "reported; mixing that numerator with `total` produced 1.011 once."
                ),
            },
            "main_table_eligible": self.main_table_eligible[0],
            "main_table_refusal": self.main_table_eligible[1],
            "ocard": self.ocard.to_dict() if self.ocard else None,
            "scard": self.scard.to_dict() if self.scard else None,
            "scard_state": self.scard_state,
            "channels": [c.to_dict() for c in self.channels],
            "separately_reported": {
                "shortcut_detected": self.shortcut_detected,
                "shortcut_evidence": self.shortcut_evidence,
                "unmeasurable_channels": self.unmeasurable_channels,
                "inconclusive_rate": round(self.inconclusive_rate, 6),
                "unattributable_error_rate": round(self.unattributable_error_rate, 6),
                "unobservable_hit_rate": self.unobservable_hit_rate,
                "exempt_count": self.exempt_count,
                "exempt_weight": round(self.exempt_weight, 6),
                "exempt_items": self.exempt_items,
            },
            "flags": self.flags,
            "provenance": self.provenance,
            "notes": self.notes,
            "replay_reading": self.replay_reading,
        }

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        return p

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Card":


        def interval(raw: Any) -> Interval | None:
            if not raw:
                return None
            return Interval(
                lo=float(raw["lo"]),
                hi=float(raw["hi"]),
                coverage=float(raw["coverage"]),
                denominator=float(raw["denominator"]),
                by_verdict={k: float(v) for k, v in (raw.get("by_verdict") or {}).items()},
            )

        sep = data.get("separately_reported", {}) or {}
        card = cls(
            submission=data["submission"],
            task_id=data["task_id"],
            tier=data["tier"],
            modality=data.get("modality", MAIN_TABLE_MODALITY),
            ocard=interval(data.get("ocard")),
            scard=interval(data.get("scard")),
            scard_state=data.get("scard_state", "absent"),
            ceiling_total=float((data.get("ceiling") or {}).get("total", 1.0)),
            ceiling_fidelity=float((data.get("ceiling") or {}).get("fidelity_subset", 1.0)),
            restricted_ceiling=(
                None if (data.get("ceiling") or {}).get("restricted") is None
                else float((data.get("ceiling") or {})["restricted"])
            ),
            shortcut_detected=bool(sep.get("shortcut_detected", False)),
            shortcut_evidence=list(sep.get("shortcut_evidence") or []),
            unmeasurable_channels=list(sep.get("unmeasurable_channels") or []),
            inconclusive_rate=float(sep.get("inconclusive_rate", 0.0)),
            unattributable_error_rate=float(sep.get("unattributable_error_rate", 0.0)),
            unobservable_hit_rate=sep.get("unobservable_hit_rate"),
            exempt_count=int(sep.get("exempt_count", 0) or 0),
            exempt_weight=float(sep.get("exempt_weight", 0.0) or 0.0),
            exempt_items=list(sep.get("exempt_items") or []),
            provenance=dict(data.get("provenance") or {}),
            notes=list(data.get("notes") or []),
            replay_reading=(
                dict(data["replay_reading"])
                if isinstance(data.get("replay_reading"), dict) else None
            ),
        )
        for raw in data.get("channels") or []:
            iv = interval(raw)
            if iv is None:
                continue
            card.channels.append(
                ChannelRecord(
                    channel=raw["channel"],
                    interval=iv,
                    weight=float(raw["weight"]),
                    mode_used=raw.get("mode_used", ""),
                    note=raw.get("note", ""),
                )
            )
        return card

    @classmethod
    def read(cls, path: str | Path) -> "Card":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def rank(cards: Sequence[Card]) -> list[Card]:


    return sorted(
        cards,
        key=lambda c: (-c.combined_lo, c.combined_hi - c.combined_lo, c.submission),
    )
