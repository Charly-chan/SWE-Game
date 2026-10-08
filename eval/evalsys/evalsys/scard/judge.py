


from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import ssl
import statistics
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

import numpy as np
from PIL import Image

from ..verdict import Verdict
from ..weights import S_CARD_HUMAN_ONLY, S_CARD_VLM_CHANNELS
from .rubric import (
    DEFAULT_LEVEL,
    LEVEL_CREDITS,
    LEVELS,
    RUBRIC_VERSION,
    RUBRICS,
    judging_rules,
    level_to_credit,
    rubric_markdown_sha256,
)

Image.MAX_IMAGE_PIXELS = None


SUBMISSION_OUTPUT_MARKERS = (
    "bridge",
    "gb_bridge",
    "session",
    "sessions",
    "observations",
    "obs",
    "bot_out",
    "user_data",
    "godot/app_userdata",
)


EVALUATOR_SOURCES = (
    "evalsys.render.capture.B2",
    "evalsys.render.capture.B1",
    "evalsys.ocard.adapters.capture_level_frames",
    "evalsys.taskgen.unity_probe",
    "evalsys.taskgen.reference_video",
    "evalsys.scard.fixture",


    "evalsys.scard.replay",
)


MIN_NATIVE_RESOLUTION = (640, 360)


RETEST_STABLE_RANGE = 0.10


DIFFERENCE_BAND = 0.125


DEFAULT_MAX_FRAMES = 6


DEFAULT_RESPONSES_MODEL = "gpt-5.6-sol"
DEFAULT_RESPONSES_BASE_URL = "https://www.micuapi.ai/v1"
DEFAULT_RESPONSES_KEY_ENV = "MICU_API_KEY"


class ProvenanceError(ValueError):
    pass


class DownscaleError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:


    path: str
    sha256: str
    width: int
    height: int
    source: str
    point_id: str = ""
    level: str = ""
    declared_resolution: str = ""
    downscaled: bool = False
    downscale_detail: str = ""

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"resolution": self.resolution}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def assert_evaluator_sourced(path: str | os.PathLike[str]) -> None:


    resolved = Path(path).expanduser().resolve()
    parts = [p.lower() for p in resolved.parts]
    for part in parts:
        for marker in SUBMISSION_OUTPUT_MARKERS:
            if marker in part:
                raise ProvenanceError(
                    f"{resolved} sits under a path component {part!r} matching "
                    f"submission-output marker {marker!r}. The S-card scores "
                    "frames the evaluator captured (evalsys.render.capture, X "
                    "mode), never frames the submission wrote: the subject does "
                    "not fill in its own report card, and a submission that "
                    "wrote no frames would otherwise escape subjective scoring "
                    "altogether (contract §3)."
                )


def load_frame(
    path: str | os.PathLike[str],
    *,
    source: str = "evalsys.render.capture.B2",
    point_id: str = "",
    level: str = "",
    declared_resolution: tuple[int, int] | str | None = None,
    min_resolution: tuple[int, int] = MIN_NATIVE_RESOLUTION,
    strict: bool = True,
) -> Frame:


    p = Path(path)
    assert_evaluator_sourced(p)
    if source not in EVALUATOR_SOURCES:
        raise ProvenanceError(
            f"frame source {source!r} is not an evaluator capture; accepted "
            f"sources are {list(EVALUATOR_SOURCES)}"
        )
    if not p.is_file():
        raise FileNotFoundError(f"no frame at {p}")
    with Image.open(p) as im:
        width, height = im.size

    declared = ""
    if declared_resolution:
        if isinstance(declared_resolution, str):
            declared = declared_resolution
            try:
                dw, dh = (int(v) for v in declared_resolution.lower().split("x"))
            except ValueError:
                dw = dh = 0
        else:
            dw, dh = declared_resolution
            declared = f"{dw}x{dh}"
    else:
        dw = dh = 0

    problems: list[str] = []
    if dw and dh and (width < dw or height < dh):
        problems.append(
            f"the capture manifest declared {dw}x{dh} and the file delivered is "
            f"{width}x{height}: it was resized after capture"
        )
    if width < min_resolution[0] or height < min_resolution[1]:
        problems.append(
            f"{width}x{height} is below the {min_resolution[0]}x{min_resolution[1]} "
            "floor; HUD text does not survive this size and a judge shown it "
            "cannot report that it was blinded"
        )
    if problems:
        detail = "; ".join(problems) + (
            ". If the context budget is the reason, send FEWER FRAMES "
            "(select_frames), never smaller ones (contract §5)."
        )
        if strict:
            raise DownscaleError(f"{p}: {detail}")
        return Frame(
            path=str(p), sha256=_sha256_file(p), width=width, height=height,
            source=source, point_id=point_id, level=level,
            declared_resolution=declared, downscaled=True, downscale_detail=detail,
        )

    return Frame(
        path=str(p), sha256=_sha256_file(p), width=width, height=height,
        source=source, point_id=point_id, level=level,
        declared_resolution=declared or f"{width}x{height}",
    )


def frames_from_capture_run(
    run: Any, *, level_of: Mapping[str, str] | None = None, strict: bool = True
) -> list[Frame]:


    source = f"evalsys.render.capture.{getattr(run, 'strategy', 'B2')}"
    out: list[Frame] = []
    for shot in getattr(run, "shots", []):
        if not getattr(shot, "ok", False) or not getattr(shot, "path", ""):
            continue
        out.append(
            load_frame(
                shot.path,
                source=source,
                point_id=shot.point_id,
                level=(level_of or {}).get(shot.point_id, getattr(shot, "actual_scene", "")),
                declared_resolution=getattr(shot, "resolution", "") or None,
                strict=strict,
            )
        )
    return out


def assert_deliverable(frames: Sequence[Frame]) -> None:

    if not frames:
        raise ValueError("no frames: there is nothing to judge, and an empty "
                         "frame set must not be scored as a clean one")
    bad = [f for f in frames if f.downscaled]
    if bad:
        raise DownscaleError(
            "downscaled frames in the delivery path: "
            + "; ".join(f"{f.path} ({f.downscale_detail})" for f in bad)
        )
    for f in frames:
        assert_evaluator_sourced(f.path)
        if f.source not in EVALUATOR_SOURCES:
            raise ProvenanceError(f"{f.path}: source {f.source!r} is not an evaluator capture")


def select_frames(
    frames: Sequence[Frame], max_frames: int = DEFAULT_MAX_FRAMES
) -> tuple[list[Frame], list[Frame]]:


    if len(frames) <= max_frames:
        return list(frames), []
    kept: list[Frame] = []
    seen_levels: set[str] = set()
    for f in frames:
        if f.level and f.level not in seen_levels:
            seen_levels.add(f.level)
            kept.append(f)
        if len(kept) >= max_frames:
            break
    remaining = [f for f in frames if f not in kept]
    room = max_frames - len(kept)
    if room > 0 and remaining:
        idx = np.linspace(0, len(remaining) - 1, room).round().astype(int)
        for i in sorted(set(int(v) for v in idx)):
            kept.append(remaining[i])
    order = {id(f): n for n, f in enumerate(frames)}
    kept.sort(key=lambda f: order[id(f)])
    dropped = [f for f in frames if f not in kept]
    return kept, dropped


@dataclass
class JudgeContext:


    project: str = ""
    tier: str = ""
    modality: str = ""
    levels: tuple[str, ...] = ()
    note: str = ""


    reference_frames: tuple[Frame, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "levels": list(self.levels),
            "reference_frames": [f.to_dict() for f in self.reference_frames],
        }


@dataclass
class ChannelJudgement:


    channel: str
    verdict: Verdict
    credit: float | None
    rationale: str
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: str = "low"


    discriminating: bool = False

    def __post_init__(self) -> None:
        if self.channel not in RUBRICS:
            raise KeyError(f"{self.channel!r} is not an S-card channel")
        if self.verdict is Verdict.PASSED and self.credit is None:
            raise ValueError(f"{self.channel}: a scored channel must carry a credit")
        if self.verdict is not Verdict.PASSED:
            self.credit = None
        if self.credit is not None and not 0.0 <= self.credit <= 1.0:
            raise ValueError(f"{self.channel}: credit {self.credit} out of [0,1]")

    @property
    def band(self) -> str:
        return "" if self.credit is None else RUBRICS[self.channel].band_for(self.credit).label

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "verdict": self.verdict.value,
            "credit": None if self.credit is None else round(self.credit, 6),
            "band": self.band,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "discriminating": self.discriminating,
            "evidence": self.evidence,
        }


@dataclass
class JudgeResult:


    judge_id: str
    judge_kind: str
    model: str
    prompt_sha256: str
    frames: list[Frame]
    channels: dict[str, ChannelJudgement]
    provider_available: bool = True
    provider_error: str = ""
    downscaled: bool = False
    frames_dropped: list[Frame] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    run_index: int = 0
    rubric_version: str = RUBRIC_VERSION
    generated_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )

    def __post_init__(self) -> None:
        if self.judge_kind not in ("local_heuristic", "vlm"):
            raise ValueError(
                f"judge_kind {self.judge_kind!r}: a judge must say which it is. "
                "The corpus already reported local-heuristic output as VLM "
                "results once; the label is the only thing that stopped that "
                "from being caught."
            )
        if self.downscaled or any(f.downscaled for f in self.frames):
            raise DownscaleError(
                f"{self.judge_id}: a downscaled frame reached the judge. "
                "Comparisons and judgements run at native resolution only (§5)."
            )
        for cid in S_CARD_HUMAN_ONLY:
            if cid in self.channels and self.channels[cid].verdict is Verdict.PASSED:
                raise ValueError(
                    f"{cid} is human-scored only and a judge produced a number "
                    "for it. That number would be indistinguishable from a "
                    "measured one in the artifact (§11.3)."
                )

    @property
    def passed_channels(self) -> dict[str, float]:
        return {
            cid: cj.credit
            for cid, cj in self.channels.items()
            if cj.verdict is Verdict.PASSED and cj.credit is not None
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "judge_id": self.judge_id,
            "judge_kind": self.judge_kind,
            "model": self.model,
            "prompt_sha256": self.prompt_sha256,
            "provider_available": self.provider_available,
            "provider_error": self.provider_error,
            "downscaled": self.downscaled,
            "run_index": self.run_index,
            "rubric_version": self.rubric_version,
            "generated_at": self.generated_at,
            "context": self.context,
            "frames": [f.to_dict() for f in self.frames],
            "frames_dropped": [f.to_dict() for f in self.frames_dropped],
            "channels": {cid: cj.to_dict() for cid, cj in self.channels.items()},
        }


class SCardJudge(Protocol):


    judge_id: str
    judge_kind: str

    def judge(self, frames: Sequence[Frame], context: JudgeContext) -> JudgeResult:
        ...


def _inconclusive_channels(reason: str, channels: Sequence[str]) -> dict[str, ChannelJudgement]:
    return {
        cid: ChannelJudgement(
            channel=cid, verdict=Verdict.INCONCLUSIVE, credit=None, rationale=reason
        )
        for cid in channels
    }


def _s4_placeholder() -> dict[str, ChannelJudgement]:

    return {
        "S4": ChannelJudgement(
            channel="S4",
            verdict=Verdict.UNOBSERVABLE,
            credit=None,
            rationale=(
                "feel is not inferable from still frames; S4 is human-scored "
                "only and this judge does not vote on it (§11.3)"
            ),
        )
    }


FLAT_DOMINANT_SHARE = 0.55
FLAT_ENERGY = 1.5


EDGE_BAND = 8


INK_GRADIENT = 24


MIN_EDGE_BLOB = 12


EDGE_PENALTY_SCALE = 4.0

LOCAL_ALGORITHM_SPEC = (
    "local_heuristic v1 | "
    f"S1: 4x4 grid, 5-bit RGB quantisation, flat iff dominant_share>={FLAT_DOMINANT_SHARE} "
    f"and gradient_energy<={FLAT_ENERGY}, credit=1-flat_fraction | "
    f"S2: luma gradient>{INK_GRADIENT} within a {EDGE_BAND}px border band, "
    f"4-connected components >= {MIN_EDGE_BLOB}px touching the outermost row/col, "
    f"credit=1/(1+clipped/{EDGE_PENALTY_SCALE}) | "
    "S3: 6x6x6 RGB histogram per level, credit=1-L1/2 between level means | "
    "S4: not scored by any judge"
)


def _rgb(frame: Frame) -> np.ndarray:
    with Image.open(frame.path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.int16)


def _luma(a: np.ndarray) -> np.ndarray:
    return (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]).astype(np.float32)


def _gradient_energy(l: np.ndarray) -> float:
    if l.shape[0] < 2 or l.shape[1] < 2:
        return 0.0
    dy = np.abs(np.diff(l, axis=0)).mean()
    dx = np.abs(np.diff(l, axis=1)).mean()
    return float((dy + dx) / 2.0)


def _dominant_share(a: np.ndarray) -> tuple[float, tuple[int, int, int]]:
    q = ((a[..., 0] >> 3).astype(np.int32) << 10) | \
        ((a[..., 1] >> 3).astype(np.int32) << 5) | (a[..., 2] >> 3).astype(np.int32)
    vals, counts = np.unique(q, return_counts=True)
    i = int(np.argmax(counts))
    v = int(vals[i])
    colour = (((v >> 10) & 31) << 3, ((v >> 5) & 31) << 3, (v & 31) << 3)
    return float(counts[i] / q.size), colour


def region_report(frame: Frame, grid: int = 4) -> dict[str, Any]:


    a = _rgb(frame)
    h, w = a.shape[:2]
    ys = np.linspace(0, h, grid + 1).astype(int)
    xs = np.linspace(0, w, grid + 1).astype(int)
    cells: list[dict[str, Any]] = []
    for i in range(grid):
        for j in range(grid):
            block = a[ys[i]:ys[i + 1], xs[j]:xs[j + 1]]
            if block.size == 0:
                continue
            share, colour = _dominant_share(block)
            energy = _gradient_energy(_luma(block))
            cells.append({
                "row": i, "col": j,
                "dominant_share": round(share, 6),
                "dominant_rgb": list(colour),
                "gradient_energy": round(energy, 4),
                "flat": bool(share >= FLAT_DOMINANT_SHARE and energy <= FLAT_ENERGY),
            })
    flat = sum(1 for c in cells if c["flat"])
    return {
        "frame": frame.path,
        "sha256": frame.sha256,
        "resolution": frame.resolution,
        "grid": grid,
        "cells": cells,
        "flat_cells": flat,
        "total_cells": len(cells),
        "flat_fraction": round(flat / len(cells), 6) if cells else 0.0,
    }


def edge_text_report(frame: Frame, band: int = EDGE_BAND) -> dict[str, Any]:


    a = _rgb(frame)
    l = _luma(a)
    h, w = l.shape
    grad = np.zeros((h, w), dtype=np.float32)
    grad[:-1, :] += np.abs(np.diff(l, axis=0))
    grad[:, :-1] += np.abs(np.diff(l, axis=1))
    mask = grad > INK_GRADIENT

    in_band = np.zeros((h, w), dtype=bool)
    b = max(1, min(band, h // 2, w // 2))
    in_band[:b, :] = True
    in_band[-b:, :] = True
    in_band[:, :b] = True
    in_band[:, -b:] = True
    m = mask & in_band

    visited = np.zeros((h, w), dtype=bool)
    comps: list[dict[str, Any]] = []
    ys, xs = np.nonzero(m)
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        if visited[y0, x0]:
            continue
        stack = [(y0, x0)]
        visited[y0, x0] = True
        pix: list[tuple[int, int]] = []
        touches = False
        while stack:
            y, x = stack.pop()
            pix.append((y, x))
            if y == 0 or x == 0 or y == h - 1 or x == w - 1:
                touches = True
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and m[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((ny, nx))
        if touches and len(pix) >= MIN_EDGE_BLOB:
            rows = [p[0] for p in pix]
            cols = [p[1] for p in pix]
            comps.append({
                "pixels": len(pix),
                "bbox": [min(rows), min(cols), max(rows) + 1, max(cols) + 1],
            })
    return {
        "frame": frame.path,
        "sha256": frame.sha256,
        "band_px": b,
        "edge_ink_fraction": round(float(m.sum()) / float(max(1, in_band.sum())), 6),
        "clipped_components": len(comps),
        "components": comps[:20],
    }


def palette_histogram(frame: Frame, bins: int = 6) -> np.ndarray:
    a = _rgb(frame)
    idx = (
        (a[..., 0] * bins // 256) * bins * bins
        + (a[..., 1] * bins // 256) * bins
        + (a[..., 2] * bins // 256)
    ).ravel()
    hist = np.bincount(idx, minlength=bins ** 3).astype(np.float64)
    total = hist.sum()
    return hist / total if total else hist


@dataclass
class LocalHeuristicJudge:


    judge_id: str = "local_heuristic/v1"
    judge_kind: str = "local_heuristic"
    model: str = "none (pixel statistics)"
    grid: int = 4

    @property
    def prompt_sha256(self) -> str:

        return hashlib.sha256(LOCAL_ALGORITHM_SPEC.encode("utf-8")).hexdigest()

    def judge(self, frames: Sequence[Frame], context: JudgeContext) -> JudgeResult:
        assert_deliverable(frames)
        kept = list(frames)

        regions = [region_report(f, self.grid) for f in kept]
        flat_fraction = float(np.mean([r["flat_fraction"] for r in regions]))
        s1 = ChannelJudgement(
            channel="S1",
            verdict=Verdict.PASSED,
            credit=max(0.0, 1.0 - flat_fraction),
            rationale=(
                f"{sum(r['flat_cells'] for r in regions)} of "
                f"{sum(r['total_cells'] for r in regions)} grid cells across "
                f"{len(kept)} frames are flat (dominant colour share >= "
                f"{FLAT_DOMINANT_SHARE} and gradient energy <= {FLAT_ENERGY}). "
                "This measures flatness, not texturing: a sky, a fog plane or a "
                "deliberate colour field reads identically to an untextured "
                "wall, so a low score here is a reason to look, not a finding."
            ),
            evidence={"per_frame": regions, "mean_flat_fraction": round(flat_fraction, 6)},
            confidence="low",
        )

        edges = [edge_text_report(f) for f in kept]
        clipped = float(np.mean([e["clipped_components"] for e in edges]))
        s2 = ChannelJudgement(
            channel="S2",
            verdict=Verdict.PASSED,
            credit=1.0 / (1.0 + clipped / EDGE_PENALTY_SCALE),
            rationale=(
                f"mean {clipped:.2f} connected high-contrast components per "
                f"frame reach the outermost pixel row or column within a "
                f"{EDGE_BAND}px band. Ink on the frame edge is what clipped "
                "text leaves behind; a full-bleed textured background leaves "
                "the same trace, and this heuristic cannot tell them apart. "
                "Read the count, not only the credit: an absolute count of 0 "
                "is the only unambiguous reading here."
            ),
            evidence={"per_frame": edges, "mean_clipped_components": round(clipped, 4)},
            confidence="low",
        )

        by_level: dict[str, list[np.ndarray]] = {}
        for f in kept:
            by_level.setdefault(f.level or "unlabelled", []).append(palette_histogram(f))
        if len(by_level) < 2:
            s3 = ChannelJudgement(
                channel="S3",
                verdict=Verdict.INCONCLUSIVE,
                credit=None,
                rationale=(
                    f"frames from {len(by_level)} level(s) were supplied "
                    f"({sorted(by_level)}); a cross-level consistency reading "
                    "needs two. The evaluator chose the capture points, so this "
                    "gap is ours, not the submission's (§5.5)."
                ),
                evidence={"levels": sorted(by_level)},
            )
        else:
            means = {k: np.mean(v, axis=0) for k, v in by_level.items()}
            keys = sorted(means)
            dists = []
            for i in range(len(keys)):
                for j in range(i + 1, len(keys)):
                    dists.append(float(np.abs(means[keys[i]] - means[keys[j]]).sum() / 2.0))
            worst = max(dists)
            s3 = ChannelJudgement(
                channel="S3",
                verdict=Verdict.PASSED,
                credit=max(0.0, 1.0 - worst),
                rationale=(
                    f"worst pairwise palette L1/2 distance between levels "
                    f"{keys} is {worst:.4f} over a 6x6x6 RGB histogram. This is "
                    "a colour statistic, not a judgement about art direction: a "
                    "deliberate night level scores badly and two unrelated games "
                    "sharing a palette score well."
                ),
                evidence={
                    "levels": keys,
                    "pairwise_l1_half": [round(d, 6) for d in dists],
                    "worst": round(worst, 6),
                },
                confidence="low",
            )

        channels = {"S1": s1, "S2": s2, "S3": s3} | _s4_placeholder()
        return JudgeResult(
            judge_id=self.judge_id,
            judge_kind="local_heuristic",
            model=self.model,
            prompt_sha256=self.prompt_sha256,
            frames=kept,
            channels=channels,
            provider_available=True,
            context=context.to_dict() | {"algorithm": LOCAL_ALGORITHM_SPEC},
        )


def _anthropic_api_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/messages"):
        return base
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def default_anthropic_requester(payload: dict, api_key: str, api_url: str) -> dict:

    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"S-card VLM request failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"S-card VLM request failed: {exc}") from exc


def default_claude_code_requester(payload: dict, api_key: str, api_url: str) -> dict:


    executable = (
        os.environ.get("GAMEBENCH_CLAUDE_CODE_BIN")
        or shutil.which("claude")
    )
    if not executable:
        raise RuntimeError("Claude Code VLM transport requires `claude` on PATH")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Claude Code VLM transport requires one Messages payload")
    content = messages[0].get("content") if isinstance(messages[0], Mapping) else None
    if not isinstance(content, list):
        raise ValueError("Claude Code VLM transport requires content blocks")

    with tempfile.TemporaryDirectory(prefix="gamebench-vlm-frames-") as frame_tmp, \
            tempfile.TemporaryDirectory(prefix="gamebench-vlm-claude-") as config_tmp:
        frame_root = Path(frame_tmp)
        text_blocks: list[str] = []
        frame_names: list[str] = []
        for block in content:
            if not isinstance(block, Mapping):
                continue
            if block.get("type") == "text":
                text_blocks.append(str(block.get("text") or ""))
                continue
            if block.get("type") != "image":
                continue
            source = block.get("source")
            if not isinstance(source, Mapping) or source.get("type") != "base64":
                raise ValueError("Claude Code VLM transport accepts base64 image blocks only")
            media_type = str(source.get("media_type") or "")
            if media_type != "image/png":
                raise ValueError(f"Claude Code VLM transport requires PNG, got {media_type!r}")
            name = f"frame_{len(frame_names):02d}.png"
            (frame_root / name).write_bytes(base64.b64decode(str(source.get("data") or "")))
            frame_names.append(name)
        if not frame_names:
            raise ValueError("Claude Code VLM transport received no images")

        prompt = (
            "Use the Read tool to inspect every evaluator-owned PNG listed below, in order. "
            "They are the only visual evidence. Then follow the JSON-only rubric request.\n\n"
            + "\n".join(f"FRAME {index}: {name}" for index, name in enumerate(frame_names))
            + "\n\n"
            + "\n".join(text_blocks)
        )
        env = dict(os.environ)
        env["ANTHROPIC_API_KEY"] = api_key
        base = api_url.rstrip("/")
        if base.endswith("/v1/messages"):
            base = base[:-12]
        elif base.endswith("/messages"):
            base = base[:-9]
        env["ANTHROPIC_BASE_URL"] = base
        env["CLAUDE_CONFIG_DIR"] = config_tmp
        proc = subprocess.run(
            [
                executable,
                "--print",
                "--bare",
                "--model",
                str(payload.get("model") or "opus"),
                "--effort",
                "medium",
                "--permission-mode",
                "acceptEdits",
                "--allowedTools",
                "Read",
                "--no-session-persistence",
                "--output-format",
                "json",
            ],
            cwd=frame_root,
            env=env,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout)[-2000:].replace(api_key, "<redacted>")
            raise RuntimeError(
                f"Claude Code VLM transport exited {proc.returncode}: {detail}"
            )
        envelope = json.loads(proc.stdout)
        result = envelope.get("result")
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError("Claude Code VLM transport returned no result text")
        return {
            "content": [{"type": "text", "text": result}],
            "model": str(payload.get("model") or "opus"),
            "transport": "claude_code",
        }


def _responses_api_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/responses"):
        return base
    if base.endswith("/v1"):
        return f"{base}/responses"
    return f"{base}/v1/responses"


def _verified_https_context() -> ssl.SSLContext:

    configured_cafile = os.environ.get("SSL_CERT_FILE")
    if configured_cafile:
        return ssl.create_default_context(cafile=configured_cafile)
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def default_openai_responses_requester(
    payload: dict, api_key: str, api_url: str
) -> dict:

    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
            "user-agent": "gamebench-evalsys/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=180, context=_verified_https_context()
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"S-card Responses request failed: HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"S-card Responses request failed: {exc}") from exc


def _extract_response_text(response: Mapping[str, Any]) -> str:
    content = response.get("content")
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    if content:
        return str(content)
    if response.get("choices"):
        return str(response["choices"][0].get("message", {}).get("content", ""))
    return ""


def extract_responses_text(response: Mapping[str, Any]) -> str:

    if response.get("output_text"):
        return str(response["output_text"])
    texts: list[str] = []
    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, Mapping):
                    continue
                if part.get("type") in {"output_text", "text"} and part.get("text"):
                    texts.append(str(part["text"]))
    return "\n".join(texts)


def _extract_json_object(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("judge response contained no JSON object")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("judge response JSON must be an object")
    return data


def _image_block(frame: Frame) -> dict[str, Any]:

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(Path(frame.path).read_bytes()).decode("ascii"),
        },
    }


def build_prompt(frames: Sequence[Frame], context: JudgeContext) -> dict[str, Any]:


    reference = list(context.reference_frames)
    n = len(frames)
    prompt: dict[str, Any] = {
        "task": (
            "Score three visual criteria of a generated game from frames "
            "captured by the evaluator, against the anchored levels below. "
            "Judge only what is visible. Answer a level (0-4) per channel; do "
            "not compute a credit."
        ),
        "rubric_version": RUBRIC_VERSION,
        "rubric_markdown_sha256": rubric_markdown_sha256(),
        "game": {"project": context.project, "tier": context.tier,
                 "levels": list(context.levels), "note": context.note},
        "level_scale": {
            "levels": {str(level): credit for level, credit in LEVEL_CREDITS.items()},
            "default_level": DEFAULT_LEVEL,
            "meaning": {
                "4": "as good as the reference this task was cut from would look on this axis",
                "3": "one visible shortfall against the reference; a viewer notices it only when looking",
                "2": "the default: mixed, or the frames do not decide it either way",
                "1": "the failure is routine across the sampled frames",
                "0": "the property is absent, or the frames show it broke",
            },
        },
        "judging_rules": judging_rules(),
        "channels": {
            cid: {
                "name": RUBRICS[cid].name,
                "question": RUBRICS[cid].question,
                "levels": [
                    {"level": b.level, "label": b.label, "anchor": b.descriptor}
                    for b in RUBRICS[cid].bands
                ],
            }
            for cid in S_CARD_VLM_CHANNELS
        },
        "frames": [
            {"index": i, "point_id": f.point_id, "level": f.level,
             "resolution": f.resolution, "sha256": f.sha256}
            for i, f in enumerate(frames)
        ],
        "images": (
            f"images 1-{n} are the submission frames, in the order listed under "
            "`frames` (image k is frame index k-1)"
        ),
        "schema": {
            "S1": {"level": "integer 0-4, or null when the frames cannot show the channel",
                   "frames_cited": "list of frame indices that decided the level",
                   "rationale": "string naming what you looked at in the cited frames",
                   "confident": "boolean"},
            "S2": "same shape",
            "S3": "same shape",
        },
        "rules": [
            "Return a single JSON object and nothing else.",
            "Do not score S4. Feel is not inferable from still frames.",
        ],
    }
    if reference:
        prompt["reference_frames"] = [
            {"index": i, "point_id": f.point_id, "level": f.level,
             "resolution": f.resolution, "sha256": f.sha256}
            for i, f in enumerate(reference)
        ]
        prompt["images"] += (
            f"; images {n + 1}-{n + len(reference)} are frames of the REFERENCE "
            "game this task was cut from, in the order listed under "
            "`reference_frames`. They set the ceiling for level 4 and are not "
            "scored."
        )
    return prompt


def _parse_channel(cid: str, raw: Any) -> ChannelJudgement:


    if not isinstance(raw, Mapping):
        return ChannelJudgement(
            cid, Verdict.INCONCLUSIVE, None,
            f"the response carried no object for {cid}; a missing channel is unread, not clean",
            evidence={"raw": raw},
        )
    rationale = str(raw.get("rationale") or "")
    level = raw.get("level")
    credit = raw.get("credit")
    if level is None and credit is None:
        return ChannelJudgement(
            cid, Verdict.INCONCLUSIVE, None,
            rationale or "the judge declined to score this channel",
            evidence={"raw": dict(raw)},
        )
    answered_by = "level"
    if level is not None:
        try:
            level_int = int(level)
        except (TypeError, ValueError):
            level_int = -1
        if level_int not in LEVELS:
            return ChannelJudgement(
                cid, Verdict.INCONCLUSIVE, None,
                f"level {level!r} is not one of {list(LEVELS)}", evidence={"raw": dict(raw)},
            )
        value = level_to_credit(level_int)
    else:
        try:
            value = min(1.0, max(0.0, float(credit)))
        except (TypeError, ValueError):
            return ChannelJudgement(
                cid, Verdict.INCONCLUSIVE, None,
                f"credit {credit!r} is not a number", evidence={"raw": dict(raw)},
            )
        answered_by = "credit"
        level_int = RUBRICS[cid].band_for(value).level
    cited_raw = raw.get("frames_cited")
    cited: list[int] = []
    if isinstance(cited_raw, (list, tuple)):
        for item in cited_raw:
            try:
                cited.append(int(item))
            except (TypeError, ValueError):
                continue
    confidence = "medium" if (raw.get("confident") and cited) else "low"
    return ChannelJudgement(
        cid, Verdict.PASSED, value, rationale,
        evidence={"raw": dict(raw), "level": level_int, "frames_cited": cited,
                  "answered_by": answered_by},
        confidence=confidence,
    )


@dataclass
class AnthropicSCardJudge:


    api_key: str = ""
    base_url: str = "https://vip.auto-code.net/v1"
    model: str = "opus-4-7"
    requester: Callable[[dict, str, str], dict] = default_anthropic_requester
    max_tokens: int = 2048
    max_frames: int = DEFAULT_MAX_FRAMES
    judge_id: str = "anthropic_scard/v1"
    judge_kind: str = "vlm"
    transport: str = "messages"

    @classmethod
    def from_env(
        cls,
        requester: Callable[[dict, str, str], dict] | None = None,
        **overrides: Any,
    ) -> "AnthropicSCardJudge":


        selected_key_env = (os.environ.get("GAMEBENCH_VLM_KEY_ENV") or "").strip()
        selected_key = os.environ.get(selected_key_env, "") if selected_key_env else ""
        transport = (os.environ.get("GAMEBENCH_VLM_TRANSPORT") or "messages").strip().lower()
        if requester is None:
            requester = (
                default_claude_code_requester
                if transport == "claude_code"
                else default_anthropic_requester
            )
        return cls(
            api_key=(selected_key
                     or os.environ.get("ANTHROPIC_API_KEY")
                     or os.environ.get("CLAUDE_API_KEY")
                     or os.environ.get("AUTO_CODE_API_KEY") or ""),
            base_url=(os.environ.get("GAMEBENCH_VLM_BASE_URL")
                      or os.environ.get("ANTHROPIC_BASE_URL")
                      or os.environ.get("CLAUDE_BASE_URL")
                      or "https://vip.auto-code.net/v1"),
            model=(os.environ.get("GAMEBENCH_VLM_MODEL")
                   or os.environ.get("ANTHROPIC_MODEL")
                   or os.environ.get("CLAUDE_MODEL") or "opus-4-7"),
            requester=requester,
            transport=transport,
            **overrides,
        )

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def api_url(self) -> str:
        return _anthropic_api_url(self.base_url)

    def _unavailable(
        self, frames: Sequence[Frame], dropped: Sequence[Frame],
        context: JudgeContext, prompt_sha: str, error: str,
    ) -> JudgeResult:
        return JudgeResult(
            judge_id=self.judge_id,
            judge_kind="vlm",
            model=self.model,
            prompt_sha256=prompt_sha,
            frames=list(frames),
            frames_dropped=list(dropped),
            channels=_inconclusive_channels(error, S_CARD_VLM_CHANNELS) | _s4_placeholder(),
            provider_available=False,
            provider_error=error,
            context=context.to_dict(),
        )

    def judge(self, frames: Sequence[Frame], context: JudgeContext) -> JudgeResult:
        assert_deliverable(frames)
        kept, dropped = select_frames(frames, self.max_frames)
        prompt = build_prompt(kept, context)
        prompt_text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
        prompt_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        if not self.available:
            return self._unavailable(
                kept, dropped, context, prompt_sha,
                "no API key in the environment (ANTHROPIC_API_KEY / "
                "CLAUDE_API_KEY / AUTO_CODE_API_KEY): the judge never ran, so "
                "these channels are inconclusive, not passed",
            )
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{
                "role": "user",
                "content": [{"type": "text", "text": prompt_text}]
                + [_image_block(frame) for frame in kept]
                + [_image_block(frame) for frame in context.reference_frames],
            }],
        }
        try:
            response = self.requester(payload, self.api_key, self.api_url)
            data = _extract_json_object(_extract_response_text(response))
        except Exception as exc:
            return self._unavailable(
                kept, dropped, context, prompt_sha, f"provider error: {exc}"
            )
        channels = {cid: _parse_channel(cid, data.get(cid)) for cid in S_CARD_VLM_CHANNELS}
        return JudgeResult(
            judge_id=self.judge_id,
            judge_kind="vlm",
            model=self.model,
            prompt_sha256=prompt_sha,
            frames=kept,
            frames_dropped=dropped,
            channels=channels | _s4_placeholder(),
            provider_available=True,
            context=context.to_dict() | {
                "wire_api": "anthropic_messages",
                "transport": self.transport,
            },
        )


@dataclass
class OpenAIResponsesSCardJudge:


    api_key: str = ""
    base_url: str = DEFAULT_RESPONSES_BASE_URL
    model: str = DEFAULT_RESPONSES_MODEL
    requester: Callable[[dict, str, str], dict] = default_openai_responses_requester
    max_output_tokens: int = 2048
    max_frames: int = DEFAULT_MAX_FRAMES
    judge_id: str = "openai_responses_scard/v1"
    judge_kind: str = "vlm"


    max_attempts: int = 2


    json_object_format: bool = True

    @classmethod
    def from_env(
        cls,
        requester: Callable[[dict, str, str], dict] = default_openai_responses_requester,
        **overrides: Any,
    ) -> "OpenAIResponsesSCardJudge":
        selected_key_env = (os.environ.get("GAMEBENCH_VLM_KEY_ENV") or "").strip()
        selected_key = os.environ.get(selected_key_env, "") if selected_key_env else ""
        return cls(
            api_key=(
                selected_key
                or os.environ.get(DEFAULT_RESPONSES_KEY_ENV)
                or os.environ.get("OPENAI_API_KEY")
                or os.environ.get("AUTO_CODE_API_KEY")
                or ""
            ),
            base_url=(
                os.environ.get("GAMEBENCH_VLM_BASE_URL")
                or os.environ.get("OPENAI_BASE_URL")
                or os.environ.get("AUTO_CODE_BASE_URL")
                or DEFAULT_RESPONSES_BASE_URL
            ),
            model=(
                os.environ.get("GAMEBENCH_VLM_MODEL")
                or os.environ.get("OPENAI_VLM_MODEL")
                or DEFAULT_RESPONSES_MODEL
            ),
            requester=requester,
            **overrides,
        )

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def api_url(self) -> str:
        return _responses_api_url(self.base_url)

    def _unavailable(
        self,
        frames: Sequence[Frame],
        dropped: Sequence[Frame],
        context: JudgeContext,
        prompt_sha: str,
        error: str,
    ) -> JudgeResult:
        return JudgeResult(
            judge_id=self.judge_id,
            judge_kind="vlm",
            model=self.model,
            prompt_sha256=prompt_sha,
            frames=list(frames),
            frames_dropped=list(dropped),
            channels=_inconclusive_channels(error, S_CARD_VLM_CHANNELS) | _s4_placeholder(),
            provider_available=False,
            provider_error=error,
            context=context.to_dict() | {"wire_api": "responses"},
        )

    def judge(self, frames: Sequence[Frame], context: JudgeContext) -> JudgeResult:
        assert_deliverable(frames)
        kept, dropped = select_frames(frames, self.max_frames)
        prompt = build_prompt(kept, context)
        prompt_text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
        prompt_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        if not self.available:
            selected_key_env = (
                os.environ.get("GAMEBENCH_VLM_KEY_ENV")
                or f"{DEFAULT_RESPONSES_KEY_ENV} / OPENAI_API_KEY / AUTO_CODE_API_KEY"
            )
            return self._unavailable(
                kept,
                dropped,
                context,
                prompt_sha,
                f"no API key in {selected_key_env}; the "
                "Responses VLM never ran",
            )
        content: list[dict[str, Any]] = [
            {"type": "input_text", "text": prompt_text}
        ]
        for frame in [*kept, *context.reference_frames]:
            encoded = base64.b64encode(Path(frame.path).read_bytes()).decode("ascii")
            content.append({
                "type": "input_image",
                "image_url": f"data:image/png;base64,{encoded}",
                "detail": "high",
            })
        payload: dict[str, Any] = {
            "model": self.model,
            "store": False,
            "max_output_tokens": self.max_output_tokens,
            "input": [{"role": "user", "content": content}],
        }
        effort = (os.environ.get("GAMEBENCH_VLM_EFFORT") or "").strip().lower()
        if effort in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            payload["reasoning"] = {"effort": effort}
        if self.json_object_format:
            payload["text"] = {"format": {"type": "json_object"}}
        started = time.monotonic()
        errors: list[str] = []
        response: Any = None
        data: dict | None = None
        for _attempt in range(max(1, self.max_attempts)):
            try:
                response = self.requester(payload, self.api_key, self.api_url)
                data = _extract_json_object(extract_responses_text(response))
                break
            except Exception as exc:
                errors.append(str(exc))
        if data is None:
            return self._unavailable(
                kept,
                dropped,
                context,
                prompt_sha,
                f"provider error after {len(errors)} attempt(s): " + " || ".join(errors),
            )
        latency = round(time.monotonic() - started, 3)
        channels = {cid: _parse_channel(cid, data.get(cid)) for cid in S_CARD_VLM_CHANNELS}
        return JudgeResult(
            judge_id=self.judge_id,
            judge_kind="vlm",
            model=self.model,
            prompt_sha256=prompt_sha,
            frames=kept,
            frames_dropped=dropped,
            channels=channels | _s4_placeholder(),
            provider_available=True,
            context=context.to_dict() | {
                "wire_api": "responses",
                "endpoint": self.api_url,
                "served_model": str(response.get("model") or "") if isinstance(response, Mapping) else "",
                "latency_seconds": latency,
                "attempts": len(errors) + 1,
                "first_error": errors[0] if errors else "",
                "usage": _usage_summary(response),
            },
        )


def _usage_summary(response: Any) -> dict[str, int]:


    if not isinstance(response, Mapping):
        return {}
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        return {}
    out: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        try:
            out[key] = int(usage.get(key) or 0)
        except (TypeError, ValueError):
            continue
    details = usage.get("output_tokens_details")
    if isinstance(details, Mapping):
        try:
            out["reasoning_tokens"] = int(details.get("reasoning_tokens") or 0)
        except (TypeError, ValueError):
            pass
    cached = usage.get("input_tokens_details")
    if isinstance(cached, Mapping):
        try:
            out["cached_tokens"] = int(cached.get("cached_tokens") or 0)
        except (TypeError, ValueError):
            pass
    return out


def vlm_scard_judge_from_env() -> SCardJudge:

    provider = (os.environ.get("GAMEBENCH_VLM_PROVIDER") or "responses").strip().lower()
    if provider in {"responses", "openai", "openai_responses"}:
        return OpenAIResponsesSCardJudge.from_env()
    if provider in {"anthropic", "messages", "anthropic_messages"}:
        return AnthropicSCardJudge.from_env()
    raise ValueError(
        "GAMEBENCH_VLM_PROVIDER must be responses or anthropic, got "
        + repr(provider)
    )


@dataclass
class ChannelSpread:


    channel: str
    n_runs: int
    n_scored: int
    values: list[float]
    mean: float | None
    minimum: float | None
    maximum: float | None
    range: float | None
    stdev: float | None
    distinct_bands: list[str]
    verdicts: dict[str, int]

    @property
    def stable(self) -> bool:


        return (
            self.n_scored >= 2
            and self.range is not None
            and self.range <= RETEST_STABLE_RANGE
            and len(self.distinct_bands) == 1
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "n_runs": self.n_runs,
            "n_scored": self.n_scored,
            "values": [round(v, 6) for v in self.values],
            "mean": None if self.mean is None else round(self.mean, 6),
            "min": None if self.minimum is None else round(self.minimum, 6),
            "max": None if self.maximum is None else round(self.maximum, 6),
            "range": None if self.range is None else round(self.range, 6),
            "stdev": None if self.stdev is None else round(self.stdev, 6),
            "distinct_bands": self.distinct_bands,
            "verdicts": self.verdicts,
            "stable": self.stable,
            "stable_range_bar": RETEST_STABLE_RANGE,
        }


@dataclass
class RetestReport:


    judge_id: str
    judge_kind: str
    n: int
    runs: list[JudgeResult]
    per_channel: dict[str, ChannelSpread]

    @property
    def unstable_channels(self) -> list[str]:
        return sorted(c for c, s in self.per_channel.items()
                      if s.n_scored >= 2 and not s.stable)

    @property
    def deterministic(self) -> bool:
        return all(
            s.range == 0.0 for s in self.per_channel.values() if s.range is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "judge_id": self.judge_id,
            "judge_kind": self.judge_kind,
            "n": self.n,
            "deterministic": self.deterministic,
            "unstable_channels": self.unstable_channels,
            "per_channel": {c: s.to_dict() for c, s in self.per_channel.items()},
            "reading_order": [
                "the spread is the finding; the mean is context for it",
                "a judge that produces different rankings from identical input "
                "contributes noise that looks like signal, which is worse than "
                "contributing nothing",
            ],
            "runs": [r.to_dict() for r in self.runs],
        }


def channel_spreads(runs: Sequence[JudgeResult]) -> dict[str, ChannelSpread]:

    per_channel: dict[str, ChannelSpread] = {}
    for cid in RUBRICS:
        vals: list[float] = []
        verdicts: dict[str, int] = {}
        bands: list[str] = []
        for r in runs:
            cj = r.channels.get(cid)
            if cj is None:
                verdicts["absent"] = verdicts.get("absent", 0) + 1
                continue
            verdicts[cj.verdict.value] = verdicts.get(cj.verdict.value, 0) + 1
            if cj.verdict is Verdict.PASSED and cj.credit is not None:
                vals.append(cj.credit)
                if cj.band not in bands:
                    bands.append(cj.band)
        per_channel[cid] = ChannelSpread(
            channel=cid,
            n_runs=len(runs),
            n_scored=len(vals),
            values=vals,
            mean=(sum(vals) / len(vals)) if vals else None,
            minimum=min(vals) if vals else None,
            maximum=max(vals) if vals else None,
            range=(max(vals) - min(vals)) if vals else None,
            stdev=statistics.pstdev(vals) if len(vals) >= 2 else (0.0 if vals else None),
            distinct_bands=bands,
            verdicts=verdicts,
        )
    return per_channel


def judge_n_times(
    judge: SCardJudge, frames: Sequence[Frame], context: JudgeContext, n: int = 5
) -> RetestReport:


    if n < 1:
        raise ValueError("n must be at least 1")
    runs: list[JudgeResult] = []
    for i in range(n):
        r = judge.judge(frames, context)
        r.run_index = i
        runs.append(r)

    return RetestReport(
        judge_id=getattr(judge, "judge_id", type(judge).__name__),
        judge_kind=getattr(judge, "judge_kind", "unknown"),
        n=n,
        runs=runs,
        per_channel=channel_spreads(runs),
    )


@dataclass
class PairJudgement:


    judge_id: str
    judge_kind: str
    good: JudgeResult
    variant: JudgeResult
    deltas: dict[str, float | None]
    difference_band: float
    expect: str
    label: str = ""

    @property
    def differing_channels(self) -> list[str]:
        return sorted(c for c, d in self.deltas.items()
                      if d is not None and abs(d) >= self.difference_band)

    @property
    def any_difference(self) -> bool:
        return bool(self.differing_channels)

    @property
    def meets_expectation(self) -> bool:
        if self.expect == "no_difference":
            return not self.any_difference
        if self.expect == "difference":
            return self.any_difference
        return True

    @property
    def comparable(self) -> bool:

        return any(d is not None for d in self.deltas.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "judge_id": self.judge_id,
            "judge_kind": self.judge_kind,
            "label": self.label,
            "expect": self.expect,
            "difference_band": self.difference_band,
            "deltas": {c: (None if d is None else round(d, 6))
                       for c, d in self.deltas.items()},
            "differing_channels": self.differing_channels,
            "any_difference": self.any_difference,
            "comparable": self.comparable,
            "meets_expectation": self.meets_expectation,
            "good": self.good.to_dict(),
            "variant": self.variant.to_dict(),
        }


def judge_pair(
    judge: SCardJudge,
    good_frames: Sequence[Frame],
    variant_frames: Sequence[Frame],
    context: JudgeContext,
    *,
    expect: str = "difference",
    label: str = "",
    difference_band: float = DIFFERENCE_BAND,
) -> PairJudgement:


    if expect not in ("difference", "no_difference", "unspecified"):
        raise ValueError(f"expect must be difference/no_difference/unspecified, got {expect!r}")
    a = judge.judge(good_frames, context)
    b = judge.judge(variant_frames, context)
    deltas: dict[str, float | None] = {}
    for cid in RUBRICS:
        ca, cb = a.channels.get(cid), b.channels.get(cid)
        if (ca and cb and ca.verdict is Verdict.PASSED and cb.verdict is Verdict.PASSED
                and ca.credit is not None and cb.credit is not None):
            deltas[cid] = cb.credit - ca.credit
        else:
            deltas[cid] = None
    return PairJudgement(
        judge_id=getattr(judge, "judge_id", type(judge).__name__),
        judge_kind=getattr(judge, "judge_kind", "unknown"),
        good=a, variant=b, deltas=deltas,
        difference_band=difference_band, expect=expect, label=label,
    )


@dataclass
class CalibrationOutcome:
    fixture_id: str
    channel: str
    kind: str
    passed: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CalibrationReport:


    judge_id: str
    judge_kind: str
    model: str
    outcomes: list[CalibrationOutcome] = field(default_factory=list)

    def channel_passed(self, channel: str) -> bool:


        relevant = [o for o in self.outcomes
                    if o.channel in (channel, "*")]
        if not any(o.kind == "defect" and o.channel == channel for o in relevant):
            return False
        if not any(o.kind == "null_control" for o in relevant):
            return False
        return all(o.passed for o in relevant)

    @property
    def calibrated_channels(self) -> list[str]:
        return sorted(c for c in RUBRICS if self.channel_passed(c))

    def to_dict(self) -> dict[str, Any]:
        return {
            "judge_id": self.judge_id,
            "judge_kind": self.judge_kind,
            "model": self.model,
            "calibrated_channels": self.calibrated_channels,
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


def outcome_for_pair(
    fixture_id: str,
    channel: str,
    kind: str,
    pj: PairJudgement,
    *,
    min_bands: int = 2,
) -> CalibrationOutcome:


    if kind == "null_control":
        ok = pj.comparable and not pj.any_difference
        detail = (
            "no channel moved on the null control"
            if ok else
            f"the null control moved {pj.differing_channels}: this judge "
            "reports differences between two captures of the same thing, so "
            "its findings on real pairs are unattributable"
        )
        if not pj.comparable:
            detail = "the judge produced no comparable reading on either side"
    else:
        delta = pj.deltas.get(channel)
        needed = min_bands * 0.25
        ok = delta is not None and delta <= -needed + 1e-9
        detail = (
            f"{channel} moved {delta if delta is None else round(delta, 4)} "
            f"against a required drop of {needed}"
        )
        if delta is None:
            detail += "; the judge produced no comparable reading on this channel"
    return CalibrationOutcome(
        fixture_id=fixture_id, channel=channel, kind=kind,
        passed=bool(ok), detail=detail, evidence=pj.to_dict(),
    )


def calibrate(
    judge: SCardJudge,
    pairs: Mapping[str, tuple[Sequence[Frame], Sequence[Frame]]],
    context: JudgeContext,
    *,
    min_bands: int = 2,
) -> CalibrationReport:


    from .rubric import CALIBRATION_FIXTURES

    outcomes: list[CalibrationOutcome] = []
    for fid, (good, variant) in pairs.items():
        fixture = CALIBRATION_FIXTURES.get(fid)
        if fixture is None:
            raise KeyError(f"{fid!r} is not a registered calibration fixture")
        expect = "no_difference" if fixture.kind == "null_control" else "difference"
        pj = judge_pair(judge, good, variant, context, expect=expect, label=fid)
        outcomes.append(
            outcome_for_pair(fid, fixture.channel, fixture.kind, pj, min_bands=min_bands)
        )
    return CalibrationReport(
        judge_id=getattr(judge, "judge_id", type(judge).__name__),
        judge_kind=getattr(judge, "judge_kind", "unknown"),
        model=getattr(judge, "model", ""),
        outcomes=outcomes,
    )


def write_judge_report(
    result: JudgeResult | RetestReport | PairJudgement | CalibrationReport,
    out_dir: str | os.PathLike[str],
    *,
    name: str = "",
) -> Path:


    kind = getattr(result, "judge_kind", "unknown")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = name or f"scard_{kind}_{type(result).__name__.lower()}"
    path = out / f"{stem}.json"
    path.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


__all__ = [
    "AnthropicSCardJudge",
    "CalibrationOutcome",
    "CalibrationReport",
    "ChannelJudgement",
    "ChannelSpread",
    "DEFAULT_RESPONSES_BASE_URL",
    "DEFAULT_RESPONSES_KEY_ENV",
    "DEFAULT_RESPONSES_MODEL",
    "DIFFERENCE_BAND",
    "DownscaleError",
    "EVALUATOR_SOURCES",
    "Frame",
    "JudgeContext",
    "JudgeResult",
    "LocalHeuristicJudge",
    "OpenAIResponsesSCardJudge",
    "MIN_NATIVE_RESOLUTION",
    "PairJudgement",
    "ProvenanceError",
    "RETEST_STABLE_RANGE",
    "RetestReport",
    "SCardJudge",
    "SUBMISSION_OUTPUT_MARKERS",
    "assert_deliverable",
    "assert_evaluator_sourced",
    "build_prompt",
    "calibrate",
    "default_openai_responses_requester",
    "extract_responses_text",
    "channel_spreads",
    "edge_text_report",
    "frames_from_capture_run",
    "judge_n_times",
    "judge_pair",
    "load_frame",
    "outcome_for_pair",
    "palette_histogram",
    "region_report",
    "select_frames",
    "write_judge_report",
    "vlm_scard_judge_from_env",
]
