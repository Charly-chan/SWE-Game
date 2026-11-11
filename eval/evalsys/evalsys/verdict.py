


from __future__ import annotations

import enum
import json
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Sequence


class Verdict(str, enum.Enum):


    PASSED = "passed"
    FAILED = "failed"
    MALFORMED = "malformed"
    SKIPPED = "skipped"


    EXEMPT = "exempt"
    INCONCLUSIVE = "inconclusive"
    UNMEASURABLE = "unmeasurable"
    UNOBSERVABLE = "unobservable"
    ERROR = "error"

    @property
    def in_denominator(self) -> bool:
        return self in _IN_DENOMINATOR

    @property
    def is_decided(self) -> bool:

        return self in (Verdict.PASSED, Verdict.FAILED, Verdict.MALFORMED)


_IN_DENOMINATOR = frozenset(
    {
        Verdict.PASSED,
        Verdict.FAILED,
        Verdict.MALFORMED,
        Verdict.SKIPPED,
        Verdict.EXEMPT,
    }
)


_OUT_OF_DENOMINATOR = frozenset(
    {Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE, Verdict.UNOBSERVABLE}
)


class Attribution(str, enum.Enum):


    HARNESS = "harness"
    SUBMISSION = "submission"
    UNATTRIBUTABLE = "unattributable"


def attribute_error(attribution: Attribution) -> Verdict:


    if attribution is Attribution.SUBMISSION:
        return Verdict.FAILED
    return Verdict.INCONCLUSIVE


@dataclass
class Item:


    id: str
    weight: float = 1.0
    verdict: Verdict = Verdict.INCONCLUSIVE
    credit: float = 0.0
    detail: str = ""
    attribution: Attribution | None = None

    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.verdict, str) and not isinstance(self.verdict, Verdict):
            self.verdict = Verdict(self.verdict)
        if self.verdict is Verdict.ERROR:
            if self.attribution is None:
                raise ValueError(
                    f"item {self.id!r}: an error verdict must carry an attribution; "
                    "an unattributed error is an item that vanished (§17.4)"
                )
            self.verdict = attribute_error(self.attribution)
        if self.verdict not in (Verdict.PASSED, Verdict.FAILED):
            self.credit = 0.0
        if self.weight < 0:
            raise ValueError(f"item {self.id!r}: negative weight")
        if not 0.0 <= self.credit <= 1.0:
            raise ValueError(f"item {self.id!r}: credit {self.credit} out of [0,1]")

    @property
    def lo_credit(self) -> float:

        if self.verdict in (Verdict.SKIPPED, Verdict.EXEMPT):
            return 0.0
        return self.credit

    @property
    def hi_credit(self) -> float:

        if self.verdict in (Verdict.SKIPPED, Verdict.EXEMPT):
            return 1.0
        return self.credit

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        d["attribution"] = self.attribution.value if self.attribution else None
        return d


def passed(id: str, weight: float = 1.0, credit: float = 1.0, **kw: Any) -> Item:
    return Item(id=id, weight=weight, verdict=Verdict.PASSED, credit=credit, **kw)


def failed(id: str, weight: float = 1.0, **kw: Any) -> Item:
    return Item(id=id, weight=weight, verdict=Verdict.FAILED, **kw)


def skipped(id: str, weight: float = 1.0, **kw: Any) -> Item:

    return Item(id=id, weight=weight, verdict=Verdict.SKIPPED, **kw)


def inconclusive(id: str, weight: float = 1.0, **kw: Any) -> Item:

    return Item(id=id, weight=weight, verdict=Verdict.INCONCLUSIVE, **kw)


def unmeasurable(id: str, weight: float = 1.0, **kw: Any) -> Item:
    return Item(id=id, weight=weight, verdict=Verdict.UNMEASURABLE, **kw)


def unobservable(id: str, weight: float = 1.0, **kw: Any) -> Item:

    return Item(id=id, weight=weight, verdict=Verdict.UNOBSERVABLE, **kw)


def exempt(id: str, weight: float = 1.0, **kw: Any) -> Item:


    return Item(id=id, weight=weight, verdict=Verdict.EXEMPT, **kw)


@dataclass
class Interval:


    lo: float
    hi: float
    coverage: float
    denominator: float

    by_verdict: dict[str, float] = field(default_factory=dict)

    @property
    def width(self) -> float:
        return self.hi - self.lo

    @property
    def is_point(self) -> bool:
        return math.isclose(self.lo, self.hi, abs_tol=1e-9)

    @property
    def low_coverage(self) -> bool:

        return self.coverage < LOW_COVERAGE_THRESHOLD

    @property
    def exempt_weight(self) -> float:
        return self.by_verdict.get(Verdict.EXEMPT.value, 0.0)

    @property
    def point(self) -> float | None:


        live = self.denominator - self.exempt_weight
        if self.denominator <= 0 or live <= 1e-9:
            return None
        return min(1.0, max(0.0, self.lo * self.denominator / live))

    def to_dict(self) -> dict[str, Any]:
        return {
            "lo": round(self.lo, 6),
            "hi": round(self.hi, 6),
            "coverage": round(self.coverage, 6),
            "denominator": round(self.denominator, 6),
            "width": round(self.width, 6),
            "low_coverage": self.low_coverage,
            "by_verdict": {k: round(v, 6) for k, v in self.by_verdict.items()},
        }

    def __str__(self) -> str:
        if self.denominator == 0:
            return "n/a (empty denominator)"
        flag = "  [LOW COVERAGE]" if self.low_coverage else ""
        return f"[{self.lo:.3f}, {self.hi:.3f}] cov={self.coverage:.2f}{flag}"


LOW_COVERAGE_THRESHOLD = 0.8


EMPTY = Interval(lo=0.0, hi=0.0, coverage=0.0, denominator=0.0)


def score_items(items: Sequence[Item]) -> Interval:


    denom = 0.0
    lo = 0.0
    hi = 0.0
    skipped_w = 0.0
    by_verdict: dict[str, float] = {}

    for it in items:
        by_verdict[it.verdict.value] = by_verdict.get(it.verdict.value, 0.0) + it.weight
        if not it.verdict.in_denominator:
            continue
        denom += it.weight
        lo += it.weight * it.lo_credit
        hi += it.weight * it.hi_credit
        if it.verdict is Verdict.SKIPPED:
            skipped_w += it.weight

    if denom <= 0:
        return Interval(0.0, 0.0, 0.0, 0.0, by_verdict)

    return Interval(
        lo=lo / denom,
        hi=hi / denom,
        coverage=1.0 - skipped_w / denom,
        denominator=denom,
        by_verdict=by_verdict,
    )


def combine(weighted: Sequence[tuple[float, Interval]]) -> Interval:


    denom = 0.0
    lo = 0.0
    hi = 0.0
    covered = 0.0
    by_verdict: dict[str, float] = {}

    for w, iv in weighted:
        for k, v in iv.by_verdict.items():
            by_verdict[k] = by_verdict.get(k, 0.0) + v
        if iv.denominator <= 0:
            continue
        denom += w
        lo += w * iv.lo
        hi += w * iv.hi
        covered += w * iv.coverage

    if denom <= 0:
        return Interval(0.0, 0.0, 0.0, 0.0, by_verdict)

    return Interval(
        lo=lo / denom,
        hi=hi / denom,
        coverage=covered / denom,
        denominator=denom,
        by_verdict=by_verdict,
    )


@dataclass
class Gate:


    id: str
    credit: float
    detail: str = ""


class Ladder:


    def __init__(self, channel: str, gates: Sequence[Gate]) -> None:
        total = sum(g.credit for g in gates)
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"{channel}: ladder credits sum to {total}, expected 1.0")
        self.channel = channel
        self.gates = list(gates)

    def resolve(self, outcomes: dict[str, tuple[Verdict, str]]) -> list[Item]:


        items: list[Item] = []
        blocked_by: str | None = None
        blocked_verdict: Verdict | None = None

        for gate in self.gates:
            if blocked_by is not None:
                items.append(
                    Item(
                        id=f"{self.channel}/{gate.id}",
                        weight=gate.credit,
                        verdict=blocked_verdict,
                        detail=f"blocked by {blocked_by}",
                    )
                )
                continue

            verdict, detail = outcomes.get(
                gate.id,
                (Verdict.SKIPPED, "no outcome reported for this rung"),
            )
            items.append(
                Item(
                    id=f"{self.channel}/{gate.id}",
                    weight=gate.credit,
                    verdict=verdict,
                    credit=1.0 if verdict is Verdict.PASSED else 0.0,
                    detail=detail or gate.detail,
                )
            )
            if verdict is Verdict.PASSED:
                continue
            blocked_by = gate.id


            if verdict is Verdict.FAILED:
                blocked_verdict = Verdict.FAILED
            elif verdict is Verdict.INCONCLUSIVE:
                blocked_verdict = Verdict.INCONCLUSIVE
            else:
                blocked_verdict = Verdict.SKIPPED

        return items


def dump(obj: Any) -> str:

    def default(o: Any) -> Any:
        if isinstance(o, enum.Enum):
            return o.value
        if hasattr(o, "to_dict"):
            return o.to_dict()
        if hasattr(o, "__dataclass_fields__"):
            return asdict(o)
        raise TypeError(f"not serialisable: {type(o)}")

    return json.dumps(obj, indent=2, sort_keys=True, default=default, ensure_ascii=False)
