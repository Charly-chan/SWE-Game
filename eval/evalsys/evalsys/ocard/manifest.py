


from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..verdict import Item, Verdict, failed, passed, skipped


@dataclass(frozen=True)
class ManifestEntry:


    id: str
    channel: str
    weight: float = 1.0
    detail: str = ""
    source: str = ""
    """Where the reading is expected to come from: a tool id, a bot mode, a probe."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "channel": self.channel, "weight": self.weight,
            "detail": self.detail, "source": self.source,
        }


@dataclass(frozen=True)
class CheckRecord:


    id: str
    ok: bool
    detail: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "ok": self.ok, "detail": self.detail, "source": self.source}


class CheckManifest:


    def __init__(self, run_id: str, entries: Sequence[ManifestEntry]) -> None:
        self.run_id = run_id
        self.entries: list[ManifestEntry] = list(entries)
        seen: set[str] = set()
        for e in self.entries:
            if e.id in seen:
                raise ValueError(f"duplicate manifest id {e.id!r}")
            seen.add(e.id)

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def ids(self) -> list[str]:
        return [e.id for e in self.entries]

    def for_channel(self, channel: str) -> "CheckManifest":
        return CheckManifest(
            f"{self.run_id}:{channel}", [e for e in self.entries if e.channel == channel]
        )

    def entry(self, check_id: str) -> ManifestEntry | None:
        return next((e for e in self.entries if e.id == check_id), None)

    def reconcile(self, reported: "ReportedResults") -> "ReconciliationResult":
        return reconcile(self, reported)

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "entries": [e.to_dict() for e in self.entries]}

    @classmethod
    def from_expectations(
        cls,
        run_id: str,
        channel: str,
        expectations: Mapping[str, int],
        *,
        source: str = "bot",
    ) -> "CheckManifest":


        entries: list[ManifestEntry] = []
        for mode, n in sorted(expectations.items()):
            for i in range(int(n)):
                entries.append(ManifestEntry(
                    id=f"{mode}/check[{i}]",
                    channel=channel,
                    detail=f"slot {i + 1} of {n} expected in mode {mode}",
                    source=f"{source}:{mode}",
                ))
        return cls(run_id, entries)


ReportedResults = (
    Mapping[str, bool] | Mapping[str, CheckRecord] | Sequence[CheckRecord] | Iterable[CheckRecord]
)


def _normalise(reported: ReportedResults) -> dict[str, CheckRecord]:
    out: dict[str, CheckRecord] = {}
    if isinstance(reported, Mapping):
        for k, v in reported.items():
            if isinstance(v, CheckRecord):
                out[k] = v
            elif isinstance(v, bool):
                out[k] = CheckRecord(id=k, ok=v)
            else:
                raise TypeError(f"reported[{k!r}] must be bool or CheckRecord, got {type(v)}")
        return out
    for r in reported:
        if not isinstance(r, CheckRecord):
            raise TypeError(f"reported results must be CheckRecord, got {type(r)}")
        out[r.id] = r
    return out


@dataclass
class ReconciliationResult:


    run_id: str
    manifest_ids: list[str]
    reported_ids: list[str]
    matched: list[str]
    missing: list[str]
    unexpected: list[str]
    items: list[Item]

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def reported_fraction(self) -> float:
        if not self.manifest_ids:
            return 1.0
        return len(self.matched) / len(self.manifest_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "expected": len(self.manifest_ids),
            "reported": len(self.reported_ids),
            "matched": len(self.matched),
            "missing": self.missing,
            "unexpected": self.unexpected,
            "complete": self.complete,
            "reported_fraction": round(self.reported_fraction, 6),
            "items": [i.to_dict() for i in self.items],
        }


def reconcile(
    manifest: CheckManifest | Sequence[str],
    reported: ReportedResults,
    *,
    channel: str = "O2",
    run_id: str = "",
) -> ReconciliationResult:


    if isinstance(manifest, CheckManifest):
        entries = manifest.entries
        rid = run_id or manifest.run_id
    else:
        entries = [ManifestEntry(id=i, channel=channel) for i in manifest]
        rid = run_id or "adhoc"

    records = _normalise(reported)
    manifest_ids = [e.id for e in entries]
    matched, missing, items = [], [], []

    for e in entries:
        rec = records.get(e.id)
        if rec is None:
            missing.append(e.id)
            items.append(failed(
                e.id, weight=e.weight,
                detail="no result reported for a manifest id; missing is not passing "
                       "(CONTRACT.md constraint 2)",
                evidence={"channel": e.channel, "expected_from": e.source},
            ))
            continue
        matched.append(e.id)
        if rec.ok:
            items.append(passed(e.id, weight=e.weight, detail=rec.detail,
                                evidence={"channel": e.channel, "source": rec.source}))
        else:
            items.append(failed(e.id, weight=e.weight, detail=rec.detail,
                                evidence={"channel": e.channel, "source": rec.source}))

    unexpected = sorted(set(records) - set(manifest_ids))
    return ReconciliationResult(
        run_id=rid, manifest_ids=manifest_ids, reported_ids=sorted(records),
        matched=matched, missing=missing, unexpected=unexpected, items=items,
    )


_RESULT_LINE = re.compile(
    r"^\s*(?:\[[\w .-]+\]\s*)?(PASS|FAIL|OK|ok)\b[ \t]+(?P<name>[\w./:\[\]-]+)\s*(?P<detail>.*)$",
    re.M,
)

_CHECKS_LINE = re.compile(r"checks?:\s*(\d+)\s*run(?:,\s*(\d+)\s*failed)?", re.I)
_VERDICT_LINE = re.compile(r"VERDICT:?\s*([A-Z]+)")

_PASSED_TOTAL = re.compile(r'"passed"\s*:\s*(\d+).{0,80}?"total"\s*:\s*(\d+)', re.S)

_FLOOR_LINE = re.compile(r"FLOOR\s*(\{.*\})")


_JSON_CHECK = re.compile(r"\{[^{}]*\"check\"[^{}]*\"ok\"[^{}]*\}")


@dataclass
class SelfReportedSummary:


    verdict: str | None = None
    checks_run: int | None = None
    checks_failed: int | None = None
    passed_count: int | None = None
    total_count: int | None = None

    @property
    def self_consistent_but_uninformative(self) -> bool:


        return (
            self.passed_count is not None
            and self.total_count is not None
            and self.passed_count == self.total_count
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict, "checks_run": self.checks_run,
            "checks_failed": self.checks_failed, "passed_count": self.passed_count,
            "total_count": self.total_count,
            "self_consistent_but_uninformative": self.self_consistent_but_uninformative,
            "note": "self-report; recorded for the report, never scored",
        }


@dataclass
class BotOutput:


    records: list[CheckRecord]
    self_report: SelfReportedSummary
    script_errors: list[str] = field(default_factory=list)
    executed: int = 0

    @property
    def by_id(self) -> dict[str, CheckRecord]:
        return {r.id: r for r in self.records}

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": [r.to_dict() for r in self.records],
            "executed": self.executed,
            "script_errors": self.script_errors[:12],
            "self_report": self.self_report.to_dict(),
        }


SCRIPT_ERROR_MARKERS = (
    "SCRIPT ERROR", "Nonexistent function", "Invalid call", "Invalid get index",
    "Invalid set index", "Attempt to call function", "Cannot call method",
    "Trying to assign", "Parse Error", "Compile Error", "Invalid access",
    "Lambda capture at index",
)


def parse_bot_records(text: str, *, source: str = "bot") -> BotOutput:


    records: list[CheckRecord] = []
    seen: set[str] = set()
    for m in _RESULT_LINE.finditer(text):
        name = m.group("name")
        ok = m.group(1).upper() in ("PASS", "OK")
        detail = (m.group("detail") or "").strip()[:200]
        if name in seen:


            records = [r for r in records if r.id != name]
        seen.add(name)
        records.append(CheckRecord(id=name, ok=ok, detail=detail, source=source))

    for m in _JSON_CHECK.finditer(text):
        try:
            blob = json.loads(m.group(0))
        except ValueError:
            continue
        name = str(blob.get("check") or "").strip()
        if not name or "ok" not in blob:
            continue
        records = [r for r in records if r.id != name]
        seen.add(name)
        records.append(CheckRecord(
            id=name, ok=bool(blob.get("ok")),
            detail=json.dumps({k: v for k, v in blob.items() if k != "check"},
                              sort_keys=True)[:200],
            source=source,
        ))

    for m in _FLOOR_LINE.finditer(text):
        try:
            blob = json.loads(m.group(1))
        except ValueError:
            continue
        name = str(blob.get("check") or "floor")
        records = [r for r in records if r.id != name]
        records.append(CheckRecord(
            id=name, ok=bool(blob.get("ok")), detail=json.dumps(blob, sort_keys=True)[:200],
            source=source,
        ))

    summary = SelfReportedSummary()
    vm = _VERDICT_LINE.search(text)
    if vm:
        summary.verdict = vm.group(1)
    cm = _CHECKS_LINE.search(text)
    if cm:
        summary.checks_run = int(cm.group(1))
        summary.checks_failed = int(cm.group(2)) if cm.group(2) else None
    pt = _PASSED_TOTAL.search(text)
    if pt:
        summary.passed_count = int(pt.group(1))
        summary.total_count = int(pt.group(2))

    errors = sorted({mk for mk in SCRIPT_ERROR_MARKERS if mk in text})
    return BotOutput(records=records, self_report=summary, script_errors=errors,
                     executed=len(records))


def bind_positional(
    manifest: CheckManifest, records: Sequence[CheckRecord]
) -> dict[str, CheckRecord]:


    out: dict[str, CheckRecord] = {}
    for slot, rec in zip(manifest.ids, records):
        out[slot] = CheckRecord(
            id=slot, ok=rec.ok,
            detail=f"reported as {rec.id!r}: {rec.detail}"[:220],
            source=rec.source + " (positional)",
        )
    return out


@dataclass
class FloorResult:


    key: str
    executed: int
    floor: int
    items: list[Item]

    @property
    def satisfied(self) -> bool:
        return self.executed >= self.floor

    @property
    def shortfall(self) -> int:
        return max(0, self.floor - self.executed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "executed": self.executed, "floor": self.floor,
            "satisfied": self.satisfied, "shortfall": self.shortfall,
            "items": [i.to_dict() for i in self.items],
        }


def check_count_floor(
    key: str,
    executed: int,
    expected_floor: int,
    *,
    channel: str = "O2",
    weight: float = 1.0,
) -> FloorResult:


    items: list[Item] = []
    short = max(0, int(expected_floor) - int(executed))
    for i in range(short):
        items.append(skipped(
            f"{key}/unrun[{i}]", weight=weight,
            detail=f"{executed} of {expected_floor} expected checks executed; this slot "
                   "never ran. A section died -- look for SCRIPT ERROR on stderr.",
            evidence={"channel": channel, "executed": executed, "floor": expected_floor},
        ))
    return FloorResult(key=key, executed=int(executed), floor=int(expected_floor), items=items)


BOT_SKIP_DOWNSTREAM = (
    ("bot/script_errors_scanned",
     "the per-mode log scan for SCRIPT ERROR never ran"),
    ("bot/check_count_compared",
     "executed-vs-expected check counts were never compared"),
    ("bot/bare_pass_detected",
     "a PASS with no countable check output would not have been noticed"),
    ("bot/zero_check_mode_detected",
     "a mode executing zero checks would not have been noticed"),
    ("bot/self_reported_failures_read",
     "the modes' own FAIL lines were never read"),
)


def bot_skip_items(
    reason: str, *, channel: str = "O2", weight: float = 1.0
) -> list[Item]:


    return [
        skipped(cid, weight=weight, detail=f"{why}; bot check reported skip: {reason}",
                evidence={"channel": channel, "cause": "bot:skip"})
        for cid, why in BOT_SKIP_DOWNSTREAM
    ]


def script_error_items(
    mode: str, markers: Sequence[str], *, channel: str = "O2", weight: float = 1.0
) -> list[Item]:


    if not markers:
        return []
    return [failed(
        f"bot/{mode}/no_script_errors", weight=weight,
        detail="script error inside the mode: " + ", ".join(sorted(markers)[:4])
               + " -- every check behind it never ran",
        evidence={"channel": channel, "mode": mode, "markers": list(markers)},
    )]


@dataclass
class CF10Result:


    satisfied: bool
    reconciliation: ReconciliationResult | None
    floors: list[FloorResult]
    items: list[Item]
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "reasons": self.reasons,
            "reconciliation": self.reconciliation.to_dict() if self.reconciliation else None,
            "floors": [f.to_dict() for f in self.floors],
            "items": [i.to_dict() for i in self.items],
        }


def evaluate_cf10(
    manifest: CheckManifest,
    reported: ReportedResults,
    *,
    floors: Sequence[FloorResult] = (),
    bot_skip_reason: str | None = None,
    script_errors_by_mode: Mapping[str, Sequence[str]] | None = None,
    channel: str = "O2",
) -> CF10Result:

    rec = reconcile(manifest, reported, channel=channel)
    items: list[Item] = list(rec.items)
    reasons: list[str] = []

    if rec.missing:
        reasons.append(
            f"{len(rec.missing)} of {len(rec.manifest_ids)} manifest ids had no reported "
            f"result: {', '.join(rec.missing[:6])}"
        )
    floors = list(floors)
    for f in floors:
        items.extend(f.items)
        if not f.satisfied:
            reasons.append(f"{f.key}: {f.executed} checks executed, floor {f.floor}")
    if bot_skip_reason:
        items.extend(bot_skip_items(bot_skip_reason, channel=channel))
        reasons.append(f"bot check skipped, taking its detectors with it: {bot_skip_reason}")
    for mode, markers in (script_errors_by_mode or {}).items():
        errs = script_error_items(mode, markers, channel=channel)
        items.extend(errs)
        if errs:
            reasons.append(f"script error in bot mode {mode}")

    return CF10Result(satisfied=not reasons, reconciliation=rec, floors=floors,
                      items=items, reasons=reasons)
