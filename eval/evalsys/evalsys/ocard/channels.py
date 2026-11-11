


from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from ..verdict import (
    Interval,
    Item,
    Ladder,
    Verdict,
    failed,
    inconclusive,
    passed,
    score_items,
    skipped,
    unmeasurable,
    unobservable,
)
from ..truth.expectations import EXPECTATIONS_VERSION
from ..weights import CHANNELS, O1_LADDER, O2_LADDER
from ..interface import ConformanceReport, TaskInterfaceRequirements
from . import adapters


@dataclass
class ChannelResult:


    channel: str
    items: list[Item]
    interval: Interval
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def weight(self) -> float:
        return CHANNELS[self.channel].weight if self.channel in CHANNELS else 0.0

    @property
    def measured(self) -> bool:
        return self.interval.denominator > 0

    @property
    def low_coverage(self) -> bool:
        return self.measured and self.interval.low_coverage

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "name": CHANNELS[self.channel].name if self.channel in CHANNELS else self.channel,
            "weight": self.weight,
            "interval": self.interval.to_dict(),
            "measured": self.measured,
            "low_coverage": self.low_coverage,
            "detail": self.detail,
            "items": [i.to_dict() for i in self.items],
            "evidence": self.evidence,
        }


def _result(channel: str, items: Sequence[Item], detail: str = "",
            evidence: Mapping[str, Any] | None = None) -> ChannelResult:
    items = list(items)
    from ..verdict import Verdict
    evidence = dict(evidence or {})
    if "exempt_items" not in evidence:
        exempt_items = [i for i in items if i.verdict is Verdict.EXEMPT]
        if exempt_items:
            evidence["exempt_items"] = [
                {"id": i.id, "weight": i.weight, "detail": i.detail}
                for i in exempt_items
            ]
            evidence["exempt_weight"] = round(sum(i.weight for i in exempt_items), 6)
            evidence["exempt_count"] = len(exempt_items)
    return ChannelResult(channel=channel, items=items, interval=score_items(items),
                         detail=detail, evidence=evidence)


def interface_blocked(channel: str, missing: Sequence[str]) -> ChannelResult:


    detail = (
        "reference-only diagnostic capability is unavailable (missing: "
        + ", ".join(missing)
        + "); this channel is unmeasurable rather than a false submission failure"
    )
    return _result(
        channel,
        [unmeasurable(f"{channel}/interface_missing", detail=detail)],
        detail,
        {"reference_capability_missing": list(missing), "attributed_to": "evaluator"},
    )


def _reattribute_blocked(items: Sequence[Item], blocker: Verdict | None) -> list[Item]:


    if blocker is not Verdict.INCONCLUSIVE:
        return list(items)
    out: list[Item] = []
    for it in items:
        if it.verdict is Verdict.SKIPPED and it.detail.startswith("blocked by"):
            out.append(inconclusive(it.id, weight=it.weight,
                                    detail=it.detail + " (harness-side, not scored against the "
                                                       "submission)"))
        else:
            out.append(it)
    return out


_BEND_FLOOR = 0.04


def _action_is_live(rec: Mapping[str, Any], idle_sig: float, bend_floor: float) -> tuple[bool, str]:


    per_frame = float(rec.get("per_frame") or 0.0)
    bend = float(rec.get("bend_vs_idle") or 0.0)
    threshold = float(rec.get("threshold") or bend_floor)
    sig_delta = float(rec.get("sig_delta") or 0.0)
    moved = bend > threshold and per_frame > bend_floor
    state = sig_delta > idle_sig + 1.0
    if moved:
        return True, "motion"
    if state:
        return True, "state"
    return False, "none"


def _o1_boots_from_truth(truth: Any) -> tuple[Verdict, str] | None:


    if truth is None:
        return None
    levels = list(getattr(truth, "levels", ()) or ())
    if not levels:
        return (
            Verdict.INCONCLUSIVE,
            "the truth snapshot contains no levels, so whether gb_levels[0] "
            "boots was never measured",
        )
    first = levels[0]
    declared = getattr(first, "declared_scene", "") or "(undeclared)"
    scene = getattr(first, "scene", "") or ""
    if not getattr(first, "reached", False):
        how = getattr(first, "entry_method", "none") or "none"
        return (
            Verdict.SKIPPED,
            f"{declared} launched but never reached a playable state "
            f"(tried: {how}); a boot that stops short of the level is not "
            "evidence the level runs",
        )
    if scene and declared and scene != declared:
        return (
            Verdict.SKIPPED,
            f"asked for {declared} and the engine came up on {scene}; "
            "Godot's silent main_scene fallback is not a level boot",
        )
    how = getattr(first, "entry_method", "none") or "none"
    how = how if how != "none" else "the level was playable at load, no menu to get past"
    return (Verdict.PASSED, f"{declared} reached a playable state ({how})")


def _o1_responds_from_truth(truth: Any) -> tuple[Verdict, str, dict[str, Any]] | None:


    if truth is None:
        return None
    declared = list(getattr(truth, "declared_actions", ()) or ())
    sweep = list(getattr(truth, "liveness", ()) or ())
    evidence = {
        "source": "truth.liveness",
        "declared_actions": declared,
        "live": [a.action for a in sweep if getattr(a, "live", False)],
        "dead": [a.action for a in sweep if not getattr(a, "live", False)],
        "via": {a.action: getattr(a, "via", "none") for a in sweep},
    }
    if not declared:
        return (
            Verdict.FAILED,
            "the project declares no non-ui input actions at all, so there is "
            "nothing to inject; 15/15 projects bound no L3 action names",
            evidence,
        )
    if not sweep:
        return (
            Verdict.INCONCLUSIVE,
            "no liveness sweep was run for this snapshot, so whether the "
            "controls do anything was never measured. This is our gap, not "
            "the submission's, and it must not read as dead bindings",
            evidence,
        )
    live = evidence["live"]
    if live:
        return (
            Verdict.PASSED,
            f"{len(live)}/{len(sweep)} declared actions move the state "
            f"signature away from the matched idle control ({', '.join(live)})",
            evidence,
        )
    return (
        Verdict.FAILED,
        f"none of {len(sweep)} declared actions changed any component of the "
        "state signature relative to a zero-input control of the same length "
        "-- dead bindings",
        evidence,
    )


def score_o1_runnable(
    cold: adapters.ColdImportResult | None,
    probe: adapters.LevelProbeResult | None,
    capture: adapters.FrameCaptureResult | None = None,
    *,
    mode: str = "H",
    truth: Any = None,
) -> ChannelResult:


    outcomes: dict[str, tuple[Verdict, str]] = {}
    evidence: dict[str, Any] = {"mode": mode}
    blocker: Verdict | None = None


    if cold is None:
        outcomes["cold_import"] = (Verdict.INCONCLUSIVE, "no cold-import reading was taken")
        blocker = Verdict.INCONCLUSIVE
    else:
        evidence["cold_import"] = cold.to_dict()
        if not cold.invocation.outcome.ran:
            outcomes["cold_import"] = (
                Verdict.INCONCLUSIVE,
                f"import did not complete on our side: {cold.invocation.outcome.value}")
            blocker = Verdict.INCONCLUSIVE
        elif not cold.cold:
            outcomes["cold_import"] = (
                Verdict.INCONCLUSIVE,
                "the .godot cache could not be cleared; a hot-cache import is not this rung")
            blocker = Verdict.INCONCLUSIVE
        elif cold.ok:
            outcomes["cold_import"] = (Verdict.PASSED,
                                       f"cold import clean ({len(cold.warnings)} warnings)")
        else:
            outcomes["cold_import"] = (
                Verdict.FAILED,
                f"{len(cold.fatal)} fatal lines on a cold import"
                + (f": {cold.fatal[0][:120]}" if cold.fatal else ""))


    boots_from_truth = _o1_boots_from_truth(truth)
    if boots_from_truth is not None:
        outcomes["boots"] = boots_from_truth
        evidence["boots_source"] = "truth"
        if boots_from_truth[0] is Verdict.INCONCLUSIVE:
            blocker = blocker or Verdict.INCONCLUSIVE
        if truth is not None and getattr(truth, "levels", None):
            first = list(truth.levels)[0]
            evidence["truth_level0"] = {
                "declared_scene": getattr(first, "declared_scene", ""),
                "scene": getattr(first, "scene", ""),
                "reached": bool(getattr(first, "reached", False)),
                "entry_method": getattr(first, "entry_method", ""),
            }
        if probe is not None:
            evidence["level_probe"] = probe.to_dict()
    elif probe is None:
        outcomes["boots"] = (Verdict.INCONCLUSIVE, "no level launch was attempted")
        blocker = blocker or Verdict.INCONCLUSIVE
    else:
        evidence["boots_source"] = "level_probe"
        evidence["level_probe"] = probe.to_dict()
        if not probe.invocation.outcome.ran:
            outcomes["boots"] = (Verdict.INCONCLUSIVE,
                                 f"our launch failed: {probe.invocation.outcome.value}")
            blocker = blocker or Verdict.INCONCLUSIVE
        elif probe.fatal:
            outcomes["boots"] = (
                Verdict.FAILED,
                f"{len(probe.fatal)} fatal log lines while running {probe.level}: "
                + probe.fatal[0][:140])
        elif probe.reached:
            how = (probe.entry_method if probe.entry_method != "none"
                   else "the level was playable at load, no menu to get past")
            outcomes["boots"] = (Verdict.PASSED,
                                 f"{probe.level} reached a playable state ({how})")
        else:
            outcomes["boots"] = (
                Verdict.SKIPPED,
                f"{probe.level} launched but never reached a playable state "
                f"(tried: {probe.entry_method}); a boot that stops short of the level is not "
                "evidence the level runs")


    if mode == "H":
        outcomes["draws_nontrivial"] = (
            Verdict.UNMEASURABLE,
            "H mode produces no pixels: --headless installs a dummy backend where get_texture() "
            "returns non-null with nothing behind it. Re-run in X mode to score this rung.")
    elif capture is None:
        outcomes["draws_nontrivial"] = (Verdict.INCONCLUSIVE, "no frame capture was taken")
    else:
        evidence["capture"] = capture.to_dict()
        outcomes["draws_nontrivial"] = _judge_frame(capture)


    responds_from_truth = _o1_responds_from_truth(truth)
    if responds_from_truth is not None:
        verdict, detail, live_ev = responds_from_truth
        outcomes["responds_to_input"] = (verdict, detail)
        evidence["responds_source"] = "truth"
        evidence["liveness"] = live_ev
        if verdict is Verdict.INCONCLUSIVE:
            blocker = blocker or Verdict.INCONCLUSIVE
    elif probe is None or not probe.invocation.outcome.ran:
        outcomes["responds_to_input"] = (Verdict.INCONCLUSIVE,
                                         "no probe report to read liveness from")
    elif not probe.reached:
        outcomes["responds_to_input"] = (
            Verdict.SKIPPED,
            "gameplay was never reached, so no action could be injected; "
            "'I could not reach gameplay' is not 'the controls are dead'")
    else:
        bend_floor = float(probe.report.get("bend_floor") or _BEND_FLOOR)
        live: list[str] = []
        dead: list[str] = []
        detail_by_action: dict[str, str] = {}
        for action, rec in sorted(probe.per_action.items()):
            ok, via = _action_is_live(rec, probe.idle_sig_delta, bend_floor)
            detail_by_action[action] = via
            (live if ok else dead).append(action)
        evidence["liveness"] = {
            "idle_baseline_per_frame": probe.idle_baseline,
            "idle_sig_delta": probe.idle_sig_delta,
            "bend_floor": bend_floor,
            "live": live, "dead": dead, "via": detail_by_action,
        }
        if not probe.actions_tested:
            outcomes["responds_to_input"] = (
                Verdict.FAILED,
                "the project declares no non-ui input actions at all, so there is nothing to "
                "inject; 15/15 projects bound no L3 action names")
        elif live:
            outcomes["responds_to_input"] = (
                Verdict.PASSED,
                f"{len(live)}/{len(probe.actions_tested)} declared actions move the state "
                f"signature away from the matched idle baseline "
                f"(idle {probe.idle_baseline:.4f} px/frame, sig {probe.idle_sig_delta:.2f})")
        else:
            outcomes["responds_to_input"] = (
                Verdict.FAILED,
                f"none of {len(probe.actions_tested)} declared actions bent the state signature "
                f"away from idle -- dead bindings")

    items = _reattribute_blocked(O1_LADDER.resolve(outcomes), blocker)
    evidence["measured_outcomes"] = {k: (v[0].value, v[1]) for k, v in outcomes.items()}
    detail = "; ".join(f"{k}={v[0].value}" for k, v in outcomes.items())
    return _result("O1", items, detail, evidence)


def _judge_frame(capture: adapters.FrameCaptureResult) -> tuple[Verdict, str]:


    if not capture.invocation.outcome.ran:
        return (Verdict.INCONCLUSIVE,
                f"frame capture failed on our side: {capture.invocation.outcome.value}")
    if not capture.imported_first:


        return (Verdict.INCONCLUSIVE,
                "capture ran without importing the project first; the frames "
                "cannot be attributed to the submission")
    if not capture.has_pixels:
        return (Verdict.FAILED,
                f"captured no frames of {capture.level} in X mode -- the level did not render")
    cr = adapters.load_tool_module(adapters.CHECK_RUNTIME)
    best = capture.best
    if best["colours"] <= cr.BLANK_COLOURS or best["dominant_share"] >= cr.BLANK_DOMINANT_SHARE:
        return (Verdict.FAILED,
                f"blank: {best['colours']} colours, {best['dominant_share'] * 100:.1f}% one colour")
    if best["colours"] <= cr.NEAR_BLANK_COLOURS and best["dominant_share"] >= cr.NEAR_BLANK_SHARE:
        return (Verdict.FAILED,
                f"near-blank: {best['colours']} colours, "
                f"{best['dominant_share'] * 100:.1f}% one colour")
    if best["edge_energy"] < cr.MIN_EDGE_ENERGY:
        return (Verdict.FAILED,
                f"flat: edge energy {best['edge_energy']:.2f}, nothing drawn over the background")
    return (Verdict.PASSED,
            f"{best['colours']} colours, {best['dominant_share'] * 100:.0f}% dominant, "
            f"edge energy {best['edge_energy']:.1f}")


def score_o2_interface(
    report: ConformanceReport,
    requirements: TaskInterfaceRequirements,
) -> ChannelResult:

    outcomes: dict[str, tuple[Verdict, str]] = {}
    evidence: dict[str, Any] = {
        "conformance": report.to_dict(),
        "task_requirements": requirements.to_dict(),
    }

    def outcome(check: str) -> tuple[Verdict, str]:
        row = report.checks.get(check) or {}
        ok = row.get("status") == "pass"
        return (Verdict.PASSED if ok else Verdict.FAILED,
                str(row.get("detail") or f"{check} did not pass conformance"))

    outcomes["groups"] = outcome("groups")
    outcomes["actions"] = outcome("actions")
    outcomes["levels"] = outcome("gb_levels")
    outcomes["endings"] = outcome("endings")

    optional = ["numeric", "device_ids", "anchor_camera", "audio_buses", "level_clear",
                "level_entry"]
    invalid = [name for name in optional
               if (report.checks.get(name) or {}).get("status") != "pass"]
    outcomes["optional_fields_valid"] = (
        (Verdict.PASSED, "all declared optional fields are valid")
        if not invalid else
        (Verdict.FAILED, "invalid optional field(s): " + ", ".join(invalid))
    )

    required_channels = [
        channel for channel, requirement in requirements.channels.items()
        if requirement.required
    ]
    failed_required = [
        channel for channel in required_channels
        if (report.checks.get(f"task_required_{channel}") or {}).get("status") != "pass"
    ]
    outcomes["task_required_observables"] = (
        (Verdict.PASSED,
         "no task-conditional observables are registered" if not required_channels else
         "task-required observables present for " + ", ".join(sorted(required_channels)))
        if not failed_required else
        (Verdict.FAILED,
         "missing task-required observables for " + ", ".join(sorted(failed_required)))
    )


    items = [
        Item(
            id=f"O2/{gate.id}",
            weight=gate.credit,
            verdict=outcomes[gate.id][0],
            credit=1.0 if outcomes[gate.id][0] is Verdict.PASSED else 0.0,
            detail=outcomes[gate.id][1],
        )
        for gate in O2_LADDER.gates
    ]
    evidence["measured_outcomes"] = {key: (value[0].value, value[1])
                                      for key, value in outcomes.items()}
    detail = "; ".join(f"{k}={v[0].value}" for k, v in outcomes.items())
    return _result("O2", items, detail, evidence)


def _sha256_file(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


def score_o6_asset_use(
    tier: str,
    *,
    supplied_assets: Sequence[str] | None = None,
    observed_assets: Sequence[str] | None = None,
    probe_detail: str = "",
    observed_digests: Mapping[str, str] | None = None,
    supplied_digests: Mapping[str, str] | None = None,
    reference_observed: bool = False,
) -> ChannelResult:


    if tier == "D1":
        return _result(
            "O6",
            [unobservable("O6/asset_coverage",
                          detail="D1 supplies no asset pack, so coverage of it is not a property "
                                 "this tier can convey (§4.5); the ceiling handles it")],
            "unobservable at this tier",
            {"tier": tier, "supplied": 0},
        )
    if not supplied_assets and reference_observed:
        return _result(
            "O6", [unobservable("O6/asset_coverage", detail=(
                "the frozen reference observation contains no file-backed input media; "
                "procedural resources are not asset-pack non-use"
            ))], "reference has no observable input-media assets",
            {"tier": tier, "supplied": 0, "basis": "reference_runtime"},
        )
    if not supplied_assets:
        return _result(
            "O6",
            [unmeasurable(
                "O6/asset_coverage",
                detail="this asset-bearing tier has no evaluator-supplied asset manifest; "
                       "the missing assignment is evaluator debt, not submission non-use",
            )],
            "supplied asset manifest missing",
            {"tier": tier, "supplied": 0, "attributed_to": "evaluator"},
        )
    if observed_assets is None:
        return _result(
            "O6",
            [unmeasurable("O6/asset_coverage",
                          detail="no engine-observed asset list; coverage must be read from the "
                                 "running game's loaded resources, not from the filesystem"
                                 + (f" ({probe_detail})" if probe_detail else ""))],
            "no engine reading" + (f": {probe_detail}" if probe_detail else ""),
            {"tier": tier, "supplied": len(supplied_assets),
             "probe_detail": probe_detail, "attributed_to": "evaluator"},
        )
    supplied_by_name: dict[str, str] = {}
    for asset in supplied_assets:


        key = str(asset) if reference_observed else Path(asset).name
        supplied_by_name.setdefault(key, str(asset))
    observed_names = {Path(a).name for a in observed_assets}
    digests = dict(observed_digests or {})
    observed_by_digest: dict[str, str] = {}
    for res_path, digest in digests.items():
        if digest:
            observed_by_digest.setdefault(str(digest), str(res_path))

    supplied_digest: dict[str, str] = {}
    if observed_by_digest:
        for name, source in supplied_by_name.items():
            if name not in observed_names:
                supplied_digest[name] = (supplied_digests or {}).get(source) or _sha256_file(source)
    items: list[Item] = []
    matched: dict[str, str] = {}
    for name in sorted(supplied_by_name):
        if name in observed_names:
            matched[name] = "name"
            items.append(passed(f"O6/uses[{name}]", detail="observed loaded by the engine"))
            continue
        renamed = observed_by_digest.get(supplied_digest.get(name, "") or "\0")
        if renamed:
            matched[name] = renamed
            items.append(passed(
                f"O6/uses[{name}]",
                detail=f"observed loaded by the engine as {renamed} (identical bytes)",
            ))
            continue
        items.append(failed(f"O6/uses[{name}]", detail="supplied but never loaded at runtime"))
    hit = len(matched)
    content_hits = sum(1 for how in matched.values() if how != "name")
    note = f"{hit}/{len(supplied_by_name)} supplied assets observed in the engine"
    if content_hits:
        note += f" ({content_hits} matched by content)"
    return _result(
        "O6",
        items,
        note,
        {
            "tier": tier,
            "supplied": len(supplied_by_name),
            "basis": "reference_runtime" if reference_observed else "supplied_pack",
            "observed": len(observed_names),
            "observed_resource_paths": sorted(str(a) for a in observed_assets),
            "matched": matched,
        },
    )


class ImageDiffBackend(Protocol):


    def load_rgb(self, path: str) -> Any: ...

    def subject_mask(self, image: Any) -> Any:
        pass

    def nonflat_bbox(self, image: Any) -> tuple[int, int, int, int]:
        pass

    def mirror_similarity(self, image: Any, bbox: tuple[int, int, int, int]) -> float:
        pass


class _ImageDiffBackend:


    def __init__(self, module: Any | None = None, flat_tol: int = 12) -> None:
        self.module = module
        self.flat_tol = int(getattr(module, "DEFAULT_FLAT_TOL", flat_tol))

    @property
    def name(self) -> str:
        return ("evalsys.render.imagediff + ocard mirror/mass"
                if self.module is not None
                else "evalsys.ocard.channels._ImageDiffBackend (standalone)")

    def load_rgb(self, path: str):
        if self.module is not None:
            return self.module.load(path)
        import numpy as np
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as im:
            return np.asarray(im.convert("RGB"), dtype=np.int16)

    def subject_mask(self, image):


        import numpy as np

        a = np.asarray(image, dtype=np.int16)
        packed = ((a[..., 0].astype(np.int32) << 16)
                  | (a[..., 1].astype(np.int32) << 8)
                  | a[..., 2].astype(np.int32))
        values, counts = np.unique(packed, return_counts=True)
        top = int(values[int(np.argmax(counts))])
        bg = np.array([(top >> 16) & 0xFF, (top >> 8) & 0xFF, top & 0xFF], dtype=np.int16)
        return np.abs(a - bg).sum(axis=2) > self.flat_tol

    def nonflat_bbox(self, image) -> tuple[int, int, int, int]:
        import numpy as np

        if self.module is not None:
            box = self.module.content_bbox(image)
            if not box.is_empty:
                return (box.left, box.top, box.right, box.bottom)
        mask = self.subject_mask(image)
        if not mask.any():
            h, w = mask.shape
            return (0, 0, w, h)
        ys, xs = np.nonzero(mask)
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    def mirror_similarity(self, image, bbox: tuple[int, int, int, int]) -> float:


        import numpy as np

        x0, y0, x1, y1 = bbox
        crop = np.asarray(image, dtype=np.int16)[y0:y1, x0:x1]
        if crop.size == 0:
            return 0.0
        flipped = np.ascontiguousarray(crop[:, ::-1])
        if self.module is not None:
            res = self.module.diff_fraction(crop, flipped)
            if res.comparable:
                return float(max(0.0, 1.0 - res.frame_fraction))
        diff = np.abs(crop.astype("int32") - flipped.astype("int32")).mean() / 255.0
        return float(max(0.0, 1.0 - diff))


def _imagediff_backend() -> tuple[ImageDiffBackend, str]:
    try:
        from ..render import imagediff
    except Exception:
        backend = _ImageDiffBackend(None)
        return backend, backend.name
    if not all(hasattr(imagediff, fn) for fn in ("load", "content_bbox", "diff_fraction")):
        backend = _ImageDiffBackend(None)
        return backend, backend.name + " (imagediff present but lacks load/content_bbox/"
    backend = _ImageDiffBackend(imagediff)
    return backend, backend.name


NINE_BOX = ("top-left", "top-centre", "top-right",
            "mid-left", "centre", "mid-right",
            "bottom-left", "bottom-centre", "bottom-right")

MASS_TOLERANCE = 0.30


@dataclass
class AnchorSpec:


    frame_path: str
    expected_bearing: str | None = None
    expected_mass_ratio: float | None = None
    mass_tolerance: float = MASS_TOLERANCE
    expect_mirror_symmetry: bool | None = None
    mirror_threshold: float = 0.90

    def __post_init__(self) -> None:
        if self.expected_bearing is not None and self.expected_bearing not in NINE_BOX:
            raise ValueError(f"unknown nine-box cell {self.expected_bearing!r}; "
                             f"expected one of {NINE_BOX}")


def nine_box_cell(cx: float, cy: float, width: int, height: int) -> str:
    col = min(2, max(0, int(cx / max(width, 1) * 3)))
    row = min(2, max(0, int(cy / max(height, 1) * 3)))
    return NINE_BOX[row * 3 + col]


def measure_anchor_composition(frame_path: str | Path) -> dict[str, Any]:


    backend, backend_name = _imagediff_backend()
    import numpy as np

    img = backend.load_rgb(str(frame_path))
    mask = backend.subject_mask(img)
    bbox = backend.nonflat_bbox(img)
    height, width = img.shape[:2]
    subject_px = float(mask.sum())
    if subject_px <= 0:
        raise ValueError("the anchor frame has no non-flat subject region")
    ys, xs = np.nonzero(mask)
    cx, cy = float(xs.mean()), float(ys.mean())
    mirror = backend.mirror_similarity(img, bbox)
    return {
        "backend": backend_name,
        "frame": str(frame_path),
        "resolution": f"{width}x{height}",
        "bearing": nine_box_cell(cx, cy, width, height),
        "centroid": [round(cx, 2), round(cy, 2)],
        "mass_ratio": round(subject_px / float(height * width), 6),
        "nonflat_bbox": list(bbox),
        "mirror_similarity": round(mirror, 6),
        "mirror_symmetry": mirror >= 0.90,
        "downsampled": False,
    }


def score_o9_anchor_composition(spec: AnchorSpec | None) -> ChannelResult:


    if spec is None:
        return _result(
            "O9",
            [unmeasurable("O9/anchor_composition",
                          detail="no anchor frame was designated for this run; O9 needs an "
                                 "evaluator-rendered anchor frame and a registered expectation")],
            "no anchor frame",
        )
    if not Path(spec.frame_path).is_file():
        return _result(
            "O9",
            [inconclusive("O9/anchor_composition",
                          detail=f"anchor frame missing on disk: {spec.frame_path}")],
            "anchor frame missing",
        )

    try:
        evidence = measure_anchor_composition(spec.frame_path)
        bearing = str(evidence["bearing"])
        mass_ratio = float(evidence["mass_ratio"])
        mirror = float(evidence["mirror_similarity"])
    except Exception as exc:
        return _result(
            "O9",
            [inconclusive("O9/anchor_composition",
                            detail=f"composition measurement failed on our side: "
                                   f"{type(exc).__name__}: {exc}")],
            "measurement error",
            {"frame": spec.frame_path},
        )
    items: list[Item] = []

    if spec.expected_bearing is None:
        items.append(unmeasurable(
            "O9/bearing", detail=f"measured {bearing}, but no expected bearing is registered for "
                                 "this anchor; a threshold picked after the measurement is not a "
                                 "test"))
    else:
        ok = bearing == spec.expected_bearing
        items.append((passed if ok else failed)(
            "O9/bearing",
            detail=f"subject bearing {bearing}, expected {spec.expected_bearing}",
            evidence={"measured": bearing, "expected": spec.expected_bearing}))

    if spec.expected_mass_ratio is None:
        items.append(unmeasurable(
            "O9/mass_ratio",
            detail=f"measured {mass_ratio:.4f}, but no expected mass ratio is registered"))
    else:
        lo = spec.expected_mass_ratio * (1.0 - spec.mass_tolerance)
        hi = spec.expected_mass_ratio * (1.0 + spec.mass_tolerance)
        ok = lo <= mass_ratio <= hi
        items.append((passed if ok else failed)(
            "O9/mass_ratio",
            detail=f"subject mass {mass_ratio:.4f}, expected {spec.expected_mass_ratio:.4f} "
                   f"+/-{spec.mass_tolerance * 100:.0f}% -> [{lo:.4f}, {hi:.4f}]",
            evidence={"measured": mass_ratio, "band": [lo, hi]}))

    if spec.expect_mirror_symmetry is None:
        items.append(unmeasurable(
            "O9/mirror_similarity",
            detail=f"measured {mirror:.4f} over the non-flat bounding box, but the anchor does "
                   "not declare whether it should be mirror-symmetric"))
    else:
        symmetric = mirror >= spec.mirror_threshold
        ok = symmetric == spec.expect_mirror_symmetry
        items.append((passed if ok else failed)(
            "O9/mirror_similarity",
            detail=f"mirror similarity {mirror:.4f} over the non-flat bbox "
                   f"(threshold {spec.mirror_threshold}); expected "
                   f"{'symmetric' if spec.expect_mirror_symmetry else 'asymmetric'}",
            evidence={"measured": mirror, "threshold": spec.mirror_threshold}))

    return _result("O9", items,
                   f"bearing={bearing} mass={mass_ratio:.4f} mirror={mirror:.4f}", evidence)


def score_o3_mechanic_fidelity(
    snapshot: Any = None,
    expected: Any = None,
    *,
    task_id: str | None = None,
    route_h_items: Sequence[Item] | None = None,
    progress_items: Sequence[Item] | None = None,
    campaign_witness: Item | None = None,
    player_counts: Sequence[int] | None = None,
) -> ChannelResult:


    if snapshot is None:
        return _no_reading(
            "O3", snapshot, expected,
            "no engine-truth snapshot was taken for this project, so no behavioural "
            "assertion could be evaluated; run `bench truth <project>` or pass one in",
        )
    if not snapshot.read_ok:
        return _no_reading(
            "O3", snapshot, expected,
            "the project could not be read at all: " + snapshot.read_failure,
        )


    from ..assertions.exemptions import evaluate_universal
    from ..assertions.authored import evaluate_authored, registered_task_ids
    from ..assertions.universal import UNIVERSAL
    from ..verdict import Verdict

    items = list(evaluate_universal(snapshot, task_id=task_id, player_counts=player_counts))
    if progress_items is not None:
        items = [item for item in items if not item.id.startswith("U4/")] + list(progress_items)
    elif campaign_witness is not None and campaign_witness.verdict is Verdict.PASSED:


        unavailable = [i for i in items if i.id.startswith("U4/")
                       and i.verdict is Verdict.UNOBSERVABLE]
        if unavailable:
            campaign_witness.weight = sum(i.weight for i in unavailable)
            items = [i for i in items if i not in unavailable] + [campaign_witness]
    h_items = evaluate_authored(snapshot, expected, task_id=task_id)
    if route_h_items:
        h_items.extend(route_h_items)
    items.extend(h_items)
    covered = ", ".join(i for i, _fn in UNIVERSAL)
    exempt_items = [i for i in items if i.verdict is Verdict.EXEMPT]
    h_registered = bool(task_id) and task_id in registered_task_ids()
    detail = (
        f"universal assertions {covered} over {len(snapshot.levels)} levels"
    )
    if h_items:
        detail += f"; H-class {len(h_items)} item(s) for task_id={task_id!r}"
    elif h_registered:
        detail += f"; H-class registered for {task_id!r} but returned no items"
    else:
        detail += (
            "; H-class not registered for this task_id, so this is a reading "
            "of the universal subset only"
        )
    if exempt_items:
        detail += (
            f" {len(exempt_items)} item(s) genre-exempt "
            f"(weight {sum(i.weight for i in exempt_items):.3f}); "
            "see by_verdict.exempt / exempt_items on the card."
        )
    return _result(
        "O3",
        items,
        detail,
        {
            "families": [i for i, _fn in UNIVERSAL],
            "h_class": (
                f"{len(h_items)} item(s)"
                if h_items
                else ("registered empty" if h_registered else "not registered")
            ),
            "h_item_ids": [i.id for i in h_items],
            "task_id": task_id,
            "exempt_items": [
                {"id": i.id, "weight": i.weight, "detail": i.detail}
                for i in exempt_items
            ],
            "snapshot": {
                "project": snapshot.project,
                "levels": len(snapshot.levels),
                "probe_sha256": snapshot.probe_sha256,
                "engine": snapshot.engine,
            },
        },
    )


def _truth_channel(
    channel: str,
    items: Sequence[Item],
    snapshot: Any,
    expected: Any,
    what: str,
) -> ChannelResult:
    return _result(
        channel,
        items,
        f"{what} over {len(snapshot.levels)} levels against expectations frozen "
        f"{expected.generated_at or 'at an unrecorded time'}",
        {
            "snapshot": {
                "project": snapshot.project,
                "levels": len(snapshot.levels),
                "probe_sha256": snapshot.probe_sha256,
                "engine": snapshot.engine,
            },
            "expectations": {
                "project": expected.project,
                "levels": expected.level_count,
                "generated_at": expected.generated_at,
                "commit": expected.commit,
            },
        },
    )


def _no_reading(channel: str, snapshot: Any, expected: Any, needs: str) -> ChannelResult:


    return _result(
        channel,
        [unmeasurable(f"{channel}/no_reading", detail=needs)],
        needs,
        {"has_snapshot": snapshot is not None, "has_expectations": expected is not None},
    )


def _truth_inputs_missing(channel: str, snapshot: Any, expected: Any) -> str | None:


    if snapshot is None:
        return (
            "no engine-truth snapshot was taken for this project, so there is nothing "
            "to compare; run `bench truth <project>` or pass one in"
        )
    if not snapshot.read_ok:
        return "the project could not be read at all: " + snapshot.read_failure
    if expected is None:
        return (
            "no frozen expectations exist for this task, so there is nothing to compare "
            "against; use the complete pinned reference-data release"
        )
    if expected.version != EXPECTATIONS_VERSION:
        return (
            f"the frozen expectations were written at version {expected.version} and "
            f"this evaluator reads version {EXPECTATIONS_VERSION}; comparing across a "
            "version bump would compare two different meanings of the same field"
        )
    return None


def score_o4_level_topology(
    snapshot: Any = None, expected: Any = None, *, task_id: str | None = None
) -> ChannelResult:


    missing = _truth_inputs_missing("O4", snapshot, expected)
    if missing:
        return _no_reading("O4", snapshot, expected, missing)
    from ..assertions.exemptions import apply_goal_probe_exemption_to_e4
    from ..assertions.extracted import level_topology

    items = apply_goal_probe_exemption_to_e4(task_id, level_topology(snapshot, expected))
    from ..assertions.behavior_contracts import reference_failure_edges
    stale_edges = reference_failure_edges(task_id, expected)
    if stale_edges:
        from ..verdict import inconclusive
        items = [inconclusive(
            item.id, weight=item.weight,
            detail="the frozen reference incorrectly records declared failure endings as progression edges; reference re-freeze is required, not a submission failure",
            evidence={"source": "frozen_reference_endings", "stale_edges": stale_edges},
        ) if item.id.startswith("E4/") else item for item in items]
    return _truth_channel(
        "O4", items, snapshot, expected,
        "level count and inter-level digraph",
    )


_LEVEL_NOUNS = (
    "levels?", "zones?", "stages?", "rooms?", "worlds?", "courses?", "tracks?", "maps?",
    "acts?", "chapters?", "waves?", "arenas?", "floors?", "blocks?", "sectors?", "areas?",
    "biomes?", "districts?", "islands?", "dungeons?", "missions?",
)
_NUMBER_WORDS = {
    "one": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_LEVEL_COUNT_RE = re.compile(
    r"\b(?P<n>\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")\s+"
    r"(?:(?:distinct|hand-authored|authored|playable|sequential|separate|unique|"
    r"consecutive|bounded|linear|main|full|complete)\s+)?"
    r"(?P<noun>" + "|".join(_LEVEL_NOUNS) + r")\b",
    re.IGNORECASE,
)


def gdd_declared_level_count(text: str) -> tuple[int | None, str]:


    by_noun: dict[str, list[tuple[int, str]]] = {}
    for match in _LEVEL_COUNT_RE.finditer(text or ""):
        raw = match.group("n").lower()
        count = _NUMBER_WORDS.get(raw)
        if count is None:
            try:
                count = int(raw)
            except ValueError:
                continue
        noun = re.sub(r"s$", "", match.group("noun").lower())
        by_noun.setdefault(noun, []).append((count, match.group(0)))
    if not by_noun:
        return None, ""
    noun, mentions = max(by_noun.items(), key=lambda kv: (len(kv[1]), kv[0]))
    counts = [count for count, _phrase in mentions]
    winner = max(set(counts), key=counts.count)
    phrase = next(phrase for count, phrase in mentions if count == winner)
    return winner, phrase


def _measured_family(items: Sequence[Item]) -> list[Item]:

    items = list(items)
    measured = [it for it in items if it.verdict.in_denominator]
    unit = 1.0 / len(measured) if measured else 1.0
    for it in items:
        it.weight = unit if it.verdict.in_denominator else 1.0
    return items


def score_o4_declared_topology(
    snapshot: Any = None,
    *,
    declared_levels: Sequence[str] = (),
    gdd_text: str = "",
    witness: Mapping[str, Any] | None = None,
) -> ChannelResult:


    from ..assertions.extracted import _family

    if snapshot is None:
        return _no_reading(
            "O4", snapshot, None,
            "no engine-truth snapshot was taken for this project, so whether the declared "
            "levels were built could not be read",
        )
    if not snapshot.read_ok:
        return _no_reading("O4", snapshot, None,
                           "the project could not be read at all: " + snapshot.read_failure)
    declared = [str(level) for level in declared_levels]
    n_declared = len(declared)
    built = [
        lv for lv in getattr(snapshot, "levels", ()) or ()
        if getattr(lv, "reached", False) and getattr(lv, "scene_matches_declaration", False)
    ]
    n_built = len(built)
    consistent = n_declared >= 1 and n_built == n_declared
    e1 = [Item(
        id="E1/manifest_vs_built",
        verdict=Verdict.PASSED if consistent else Verdict.FAILED,
        credit=1.0 if consistent else 0.0,
        detail=(
            f"{n_declared} level(s) declared in gb_levels.json, {n_built} cold-loaded to a "
            "playable state on the declared scene"
            + ("" if consistent else
               "; a declared level that does not come up is a manifest promise the build "
               "does not keep" if n_declared else
               "; the manifest declares no levels")
        ),
        evidence={"declared": declared, "built": [lv.declared_scene for lv in built]},
    )]
    gdd_count, phrase = gdd_declared_level_count(gdd_text)
    if gdd_count is None:
        e1.append(unobservable(
            "E1/gdd_vs_manifest",
            detail="the authored GDD states no explicit level/zone/stage count to hold the "
                   "manifest to; only manifest-vs-built is read",
        ))
    else:
        ok = gdd_count == n_declared
        e1.append(Item(
            id="E1/gdd_vs_manifest",
            verdict=Verdict.PASSED if ok else Verdict.FAILED,
            credit=1.0 if ok else 0.0,
            detail=(
                f"the authored GDD states {gdd_count} ({phrase!r}); gb_levels.json declares "
                f"{n_declared}" + ("" if ok else " -- the design document and the manifest disagree")
            ),
            evidence={"gdd_count": gdd_count, "gdd_phrase": phrase, "manifest_count": n_declared},
        ))
    if witness is None:
        e4 = [unobservable(
            "E4/declared_progression",
            detail="no replay of the submission's own tape was available, so traversal of "
                   "the declared levels was not read",
        )]
    elif not witness.get("reached"):
        e4 = [skipped(
            "E4/declared_progression",
            detail="the submitted tape did not clear the game "
                   f"(stop_reason={witness.get('stop_reason') or 'unknown'}), so whether the "
                   "declared levels are traversed in order could not be observed",
        )]
    else:
        early = bool(witness.get("success_before_all_levels"))
        e4 = [Item(
            id="E4/declared_progression",
            verdict=Verdict.FAILED if early else Verdict.PASSED,
            credit=0.0 if early else 1.0,
            detail=(
                f"the success ending was entered with fewer than the {n_declared} declared "
                "level scenes visited; the manifest promises a progression the game "
                "advances past in place"
                if early else
                f"the submission's own clearing tape visited all {n_declared} declared "
                f"level(s) before the success ending (goal_frame={witness.get('goal_frame')})"
            ),
            evidence={
                "success_before_all_levels": early,
                "goal_frame": witness.get("goal_frame"),
                "stop_reason": witness.get("stop_reason"),
            },
        )]


    items = _measured_family(e1) + _family(e4)
    return _result(
        "O4",
        items,
        f"declared-vs-built consistency (brief mode): {n_declared} declared, {n_built} built, "
        f"gdd states {gdd_count if gdd_count is not None else 'no count'}",
        {
            "mode": "brief_own_structure",
            "declared_levels": declared,
            "built_levels": [lv.declared_scene for lv in built],
            "gdd_level_count": gdd_count,
            "gdd_phrase": phrase,
            "witness_stop_reason": (witness or {}).get("stop_reason"),
            "snapshot": {
                "project": snapshot.project,
                "levels": len(snapshot.levels),
                "probe_sha256": snapshot.probe_sha256,
                "engine": snapshot.engine,
            },
        },
    )


def score_o5_spatial_ordinal(snapshot: Any = None, expected: Any = None) -> ChannelResult:

    missing = _truth_inputs_missing("O5", snapshot, expected)
    if missing:
        return _no_reading("O5", snapshot, expected, missing)
    from ..assertions.extracted import spatial_ordinal

    return _truth_channel(
        "O5", spatial_ordinal(snapshot, expected), snapshot, expected,
        "collectible ordering, normalised goal distance and entity dispersion",
    )


_O7_NEEDS = (
    "a zero-inject L5 route whose authored_solution has passed RT-1 on the "
    "reference; segments (L1-L4) are O3 H, not this channel"
)


def score_o7_authored_clear(
    routes: Sequence[Any] | None = None,
    readings: Sequence[Any] | None = None,
    gold: Sequence[Any] | None = None,
) -> ChannelResult:


    if not routes:
        return _result(
            "O7",
            [unmeasurable("O7/no_route", detail=_O7_NEEDS)],
            _O7_NEEDS,
            {"missing_reading": _O7_NEEDS},
        )
    from ..routes.runner import score_o7_items

    items = score_o7_items(routes, readings or (), gold or ())
    if not items:
        return _result(
            "O7",
            [unmeasurable("O7/no_l5", detail=_O7_NEEDS)],
            _O7_NEEDS,
            {
                "missing_reading": _O7_NEEDS,
                "route_count": len(list(routes)),
                "l5_count": sum(1 for r in routes if getattr(r, "tier", None) == 5),
            },
        )
    return _result(
        "O7",
        items,
        f"L5 authored clear over {len(items)} RT-1-passed route(s)",
        {"route_ids": [i.id for i in items]},
    )


def score_o1_context(ctx: Any) -> ChannelResult:
    return score_o1_runnable(
        ctx.cold_import, ctx.level_probe,
        ctx.capture.frame_capture if ctx.capture else None,
        mode=ctx.mode, truth=ctx.truth,
    )


def score_o2_context(ctx: Any) -> ChannelResult:
    return score_o2_interface(ctx.interface.report, ctx.requirements)


def _assert_truth_interface(ctx: Any, channel: str) -> None:
    if ctx.truth is not None:
        ctx.assert_interface_provenance(ctx.truth, source=f"{channel} truth snapshot")


def score_o3_context(ctx: Any) -> ChannelResult:
    _assert_truth_interface(ctx, "O3")
    route_h_items: list[Item] = []
    routes = list(getattr(ctx.registered_routes, "routes", ()) or ())
    if routes:
        from ..routes.runner import h_items_from_segments
        route_h_items = h_items_from_segments(routes, ctx.route_readings, ctx.route_gold)
    from ..assertions.behavior_contracts import (
        probe_plan, route_progress_items, campaign_progress_items, player_counts_for_mode,
    )
    plan = probe_plan(ctx.task_id)
    task_mode = str(getattr(ctx, "task_mode", "") or "")
    progress_items = None
    if (
        ctx.truth is not None
        and task_mode in {"", "bugfix"}
        and plan.get("goal", {}).get("effect") in {"route_progress", "route_campaign"}
    ):
        if plan["goal"]["effect"] == "route_campaign":
            progress_items = campaign_progress_items(routes, ctx.route_readings, ctx.route_gold)
        else:
            progress_items = route_progress_items(ctx.truth, routes, ctx.route_readings, ctx.route_gold)
    return score_o3_mechanic_fidelity(
        ctx.truth, ctx.expectations, task_id=ctx.task_id,
        route_h_items=route_h_items,
        progress_items=progress_items,
        campaign_witness=(
            campaign_progress_items(routes, ctx.route_readings, ctx.route_gold)[0]
            if routes else None
        ),
        player_counts=player_counts_for_mode(plan, task_mode),
    )


def score_o4_context(ctx: Any) -> ChannelResult:
    _assert_truth_interface(ctx, "O4")
    if not ctx.interface.levels:
        return _result(
            "O4",
            [failed(
                "O4/declared_levels",
                detail="the submission omitted its globally required normalized level order",
            )],
            "submission level order missing",
        )
    if str(getattr(ctx, "task_mode", "") or "") == "brief":


        submission = getattr(ctx, "submission_design", None) or {}
        return score_o4_declared_topology(
            ctx.truth,
            declared_levels=[level.scene for level in ctx.interface.levels],
            gdd_text=str(submission.get("gdd_text") or ""),
            witness=submission.get("witness"),
        )
    return score_o4_level_topology(ctx.truth, ctx.expectations, task_id=ctx.task_id)


def score_o5_context(ctx: Any) -> ChannelResult:
    _assert_truth_interface(ctx, "O5")
    if not ctx.interface.levels:
        return _result(
            "O5",
            [failed(
                "O5/declared_levels",
                detail="the submission omitted globally required level addresses",
            )],
            "submission level addresses missing",
        )
    return score_o5_spatial_ordinal(ctx.truth, ctx.expectations)


def score_o6_context(ctx: Any) -> ChannelResult:
    if str(getattr(ctx, "task_mode", "") or "") == "bugfix":
        return _result(
            "O6",
            [unobservable(
                "O6/asset_coverage",
                detail=(
                    "bugfix mode supplies the complete game rather than a separate asset "
                    "pack; supplied-pack realization is not applicable"
                ),
            )],
            "not applicable in bugfix mode: assets are included in the repair target",
            {"tier": ctx.tier, "task_mode": "bugfix", "supplied": 0},
        )
    assignment = ctx.asset_assignment
    supplied = assignment.supplied_assets if assignment is not None else None
    observation = assignment.observation if assignment is not None else None
    observed = (
        observation.loaded_assets
        if observation is not None and observation.report_produced else None
    )
    return score_o6_asset_use(
        ctx.tier, supplied_assets=supplied, observed_assets=observed,
        supplied_digests=getattr(assignment, "supplied_digests", None),
        reference_observed=bool(getattr(assignment, "reference_observed", False)),
        probe_detail=str(getattr(observation, "detail", "") or "") if observed is None else "",
        observed_digests=(
            dict(getattr(observation, "loaded_asset_digests", {}) or {})
            if observed is not None else None
        ),
    )


def score_o7_context(ctx: Any) -> ChannelResult:
    if (ctx.interface.report.checks.get("endings") or {}).get("status") != "pass":
        return _result(
            "O7",
            [failed(
                "O7/ending_vocabulary",
                detail="the submission's success/failure ending vocabulary is missing or "
                       "ambiguous; leaving the last level cannot count as victory",
            )],
            "submission ending vocabulary invalid",
        )
    for reading in ctx.route_readings:
        ctx.assert_interface_provenance(reading, source="O7 route reading")
    routes = list(getattr(ctx.registered_routes, "routes", ()) or ())
    return score_o7_authored_clear(routes, ctx.route_readings, ctx.route_gold)


def score_o8_context(ctx: Any) -> ChannelResult:
    result = stub_channel("O8")
    result.detail = (
        "excluded from the main card; independent external-agent routes consume "
        "the same levels/actions SubmissionInterface"
    )
    result.evidence.update({
        "excluded": True,
        "interface": ctx.interface.provenance(),
        "dependency": "external_agent",
    })
    return result


def score_o9_context(ctx: Any) -> ChannelResult:
    requirement = ctx.requirements.for_channel("O9")
    if not requirement.required:
        return _result(
            "O9", [unobservable(
                "O9/anchor_composition",
                detail="this task has no frozen anchor composition expectation",
            )], "task has no O9 expectation",
        )
    if ctx.interface.anchor_camera is None:
        return _result(
            "O9", [failed(
                "O9/anchor_camera",
                detail="anchor_camera is a frozen task obligation but the submission omitted it",
            )], "required anchor_camera omitted",
        )
    if ctx.mode == "H":
        return _result(
            "O9", [unmeasurable(
                "O9/anchor_composition",
                detail="H mode has no rasterizer; rerun this required observable in X mode",
            )], "O9 requires X mode",
        )
    expected = getattr(ctx.expectations, "anchor_composition", {}) or {}
    if not expected:
        return _result(
            "O9", [inconclusive(
                "O9/anchor_composition",
                detail="task requires O9 but evaluator supplied no frozen anchor expectation",
            )], "evaluator expectation missing",
        )
    capture = ctx.capture
    if capture is None or not capture.anchor_frame:
        notes = " ".join((capture.manifest.get("notes") or []) if capture else [])
        if "anchor capture failed" in notes:
            return _result(
                "O9", [failed(
                    "O9/anchor_camera_resolves",
                    detail="anchor_camera format is legal but its submitted scene/NodePath "
                           "could not be applied: " + notes,
                )], "submitted anchor address did not resolve",
            )
        return _result(
            "O9", [inconclusive(
                "O9/anchor_composition",
                detail="evaluator capture probe produced no anchor report",
            )], "capture probe missing",
        )
    return score_o9_anchor_composition(AnchorSpec(
        frame_path=capture.anchor_frame,
        expected_bearing=expected.get("bearing"),
        expected_mass_ratio=expected.get("mass_ratio"),
        expect_mirror_symmetry=expected.get("mirror_symmetry"),
    ))


STUB_CHANNELS: dict[str, str] = {
    "O8": "L0-L5 route pass rates from the route runner, with a per-game op cap derived from "
          "measured frame cost; not available here",
}


def stub_channel(channel: str) -> ChannelResult:


    if channel not in STUB_CHANNELS:
        raise KeyError(f"{channel} is not a stub channel; implement it or add it to STUB_CHANNELS")
    if CHANNELS[channel].never_unmeasurable:
        raise ValueError(f"{channel} may never be unmeasurable; it must produce a reading")
    return _result(
        channel,
        [unobservable(f"{channel}/not_implemented", detail=STUB_CHANNELS[channel])],
        f"not implemented: {STUB_CHANNELS[channel]}",
        {"missing_reading": STUB_CHANNELS[channel]},
    )


def stub_channels() -> list[ChannelResult]:
    return [stub_channel(c) for c in sorted(STUB_CHANNELS)]
