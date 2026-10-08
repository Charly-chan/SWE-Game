


from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


DEFAULT_TOL = 8


DEFAULT_FLAT_TOL = 12


DEFAULT_THRESHOLD = 0.0


MIN_DIFFERING_PIXELS = 8


ArrayLike = "np.ndarray"


def load(path: str | os.PathLike[str]) -> np.ndarray:

    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.int16)


def _as_array(img: Any) -> np.ndarray:
    if isinstance(img, np.ndarray):
        return img.astype(np.int16, copy=False)
    if isinstance(img, Image.Image):
        return np.asarray(img.convert("RGB"), dtype=np.int16)
    return load(img)


@dataclass
class BBox:


    top: int
    left: int
    bottom: int
    right: int

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def area(self) -> int:
        return self.height * self.width

    @property
    def is_empty(self) -> bool:
        return self.area == 0

    def union(self, other: "BBox") -> "BBox":
        if self.is_empty:
            return other
        if other.is_empty:
            return self
        return BBox(
            min(self.top, other.top),
            min(self.left, other.left),
            max(self.bottom, other.bottom),
            max(self.right, other.right),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "top": self.top,
            "left": self.left,
            "bottom": self.bottom,
            "right": self.right,
            "width": self.width,
            "height": self.height,
            "area": self.area,
        }


def content_bbox(img: Any, flat_tol: int = DEFAULT_FLAT_TOL) -> BBox:


    a = _as_array(img)


    packed = (
        (a[..., 0].astype(np.int32) << 16)
        | (a[..., 1].astype(np.int32) << 8)
        | a[..., 2].astype(np.int32)
    )
    values, counts = np.unique(packed, return_counts=True)
    top = int(values[int(np.argmax(counts))])
    bg = np.array([(top >> 16) & 0xFF, (top >> 8) & 0xFF, top & 0xFF], dtype=np.int16)
    dist = np.abs(a - bg).sum(axis=2)
    mask = dist > flat_tol
    if not mask.any():
        return BBox(0, 0, 0, 0)
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    return BBox(int(rows[0]), int(cols[0]), int(rows[-1]) + 1, int(cols[-1]) + 1)


@dataclass
class DiffResult:


    comparable: bool
    frame_fraction: float = 0.0
    bbox_fraction: float = 0.0
    differing_pixels: int = 0
    frame_pixels: int = 0
    bbox: BBox | None = None
    size_a: tuple[int, int] = (0, 0)
    size_b: tuple[int, int] = (0, 0)
    tol: int = DEFAULT_TOL
    max_delta: int = 0
    detail: str = ""
    a: str = ""
    b: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparable": self.comparable,
            "frame_fraction": round(self.frame_fraction, 8),
            "bbox_fraction": round(self.bbox_fraction, 8),
            "differing_pixels": self.differing_pixels,
            "frame_pixels": self.frame_pixels,
            "bbox": self.bbox.to_dict() if self.bbox else None,
            "size_a": f"{self.size_a[0]}x{self.size_a[1]}",
            "size_b": f"{self.size_b[0]}x{self.size_b[1]}",
            "tol": self.tol,
            "max_delta": self.max_delta,
            "detail": self.detail,
            "a": self.a,
            "b": self.b,
            "downsampled": False,
        }


def _incomparable(a: Any, b: Any, sa: tuple[int, int], sb: tuple[int, int]) -> DiffResult:
    return DiffResult(
        comparable=False,
        size_a=sa,
        size_b=sb,
        detail=(
            f"incomparable: {sa[0]}x{sa[1]} against {sb[0]}x{sb[1]}. Resizing "
            "either one to match would be a downsample by another name (§5), "
            "and a difference measured across a rescale is not a difference "
            "between the two frames."
        ),
        a=str(a) if not isinstance(a, np.ndarray) else "",
        b=str(b) if not isinstance(b, np.ndarray) else "",
    )


def diff_fraction(a: Any, b: Any, tol: int = DEFAULT_TOL) -> DiffResult:


    arr_a = _as_array(a)
    arr_b = _as_array(b)
    sa = (arr_a.shape[1], arr_a.shape[0])
    sb = (arr_b.shape[1], arr_b.shape[0])
    if arr_a.shape != arr_b.shape:
        return _incomparable(a, b, sa, sb)

    delta = np.abs(arr_a - arr_b).sum(axis=2)
    mask = delta > tol
    n_diff = int(mask.sum())
    n_total = int(mask.size)

    box = content_bbox(arr_a).union(content_bbox(arr_b))
    if box.is_empty:
        bbox_frac = 0.0
    else:
        sub = mask[box.top : box.bottom, box.left : box.right]
        bbox_frac = float(sub.mean())

    return DiffResult(
        comparable=True,
        frame_fraction=n_diff / float(n_total) if n_total else 0.0,
        bbox_fraction=bbox_frac,
        differing_pixels=n_diff,
        frame_pixels=n_total,
        bbox=box,
        size_a=sa,
        size_b=sb,
        tol=tol,
        max_delta=int(delta.max()) if delta.size else 0,
        detail=(
            f"{n_diff} of {n_total} pixels differ by more than {tol} "
            f"({100.0 * n_diff / max(n_total, 1):.4f}% of the frame, "
            f"{100.0 * bbox_frac:.4f}% of the {box.width}x{box.height} content box)"
        ),
        a=str(a) if not isinstance(a, np.ndarray) else "",
        b=str(b) if not isinstance(b, np.ndarray) else "",
    )


@dataclass
class Distinguishable:


    distinguishable: bool
    comparable: bool
    metric: str
    value: float
    threshold: float
    null_control: float | None
    diff: DiffResult
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "distinguishable": self.distinguishable,
            "comparable": self.comparable,
            "metric": self.metric,
            "value": round(self.value, 8),
            "threshold": round(self.threshold, 8),
            "null_control": (
                None if self.null_control is None else round(self.null_control, 8)
            ),
            "detail": self.detail,
            "diff": self.diff.to_dict(),
        }


def distinguishable(
    a: Any,
    b: Any,
    threshold: float = DEFAULT_THRESHOLD,
    *,
    tol: int = DEFAULT_TOL,
    metric: str = "bbox",
    null_control: float | None = None,
    min_pixels: int = MIN_DIFFERING_PIXELS,
) -> Distinguishable:


    d = diff_fraction(a, b, tol=tol)
    if not d.comparable:
        return Distinguishable(
            False, False, metric, 0.0, threshold, null_control, d, d.detail
        )
    value = d.bbox_fraction if metric == "bbox" else d.frame_fraction
    bar = max(threshold, null_control or 0.0)
    enough_pixels = d.differing_pixels >= min_pixels
    verdict = enough_pixels and value > bar

    why = (
        f"{d.differing_pixels} pixels differ (floor {min_pixels}); {metric} "
        f"fraction {value:.8f} against a bar of {bar:.8f}"
    )
    if not enough_pixels:
        why += " -- below the pixel floor, treated as the same frame"
    if null_control is None:
        why += (
            "; NO NULL CONTROL WAS SUPPLIED, so this cannot separate 'two "
            "different states' from 'a noisy metric'"
        )
    else:
        why += f"; same-class-twice null control {null_control:.8f}"
    return Distinguishable(verdict, True, metric, value, threshold, null_control, d, why)


@dataclass
class NullControl:


    n_pairs: int
    metric: str
    max_delta: float
    median_delta: float
    values: list[float] = field(default_factory=list)
    incomparable: int = 0
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.n_pairs > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_pairs": self.n_pairs,
            "metric": self.metric,
            "max_delta": round(self.max_delta, 8),
            "median_delta": round(self.median_delta, 8),
            "values": [round(v, 8) for v in self.values],
            "incomparable": self.incomparable,
            "usable": self.usable,
            "detail": self.detail,
        }


def null_control_delta(
    same_class_frames: Sequence[Any],
    *,
    tol: int = DEFAULT_TOL,
    metric: str = "bbox",
) -> NullControl:


    frames = list(same_class_frames)
    values: list[float] = []
    bad = 0
    for i in range(len(frames)):
        for j in range(i + 1, len(frames)):
            d = diff_fraction(frames[i], frames[j], tol=tol)
            if not d.comparable:
                bad += 1
                continue
            values.append(d.bbox_fraction if metric == "bbox" else d.frame_fraction)
    if not values:
        return NullControl(
            0,
            metric,
            0.0,
            0.0,
            [],
            bad,
            "no comparable same-class pair: there is no baseline, and any "
            "difference reported against these frames is unvalidated",
        )
    ordered = sorted(values)
    med = ordered[len(ordered) // 2]
    return NullControl(
        len(values),
        metric,
        max(values),
        med,
        values,
        bad,
        f"{len(values)} same-class pairs: median {med:.6f}, worst {max(values):.6f} "
        f"({metric} fraction). Differences at or below the worst value are noise.",
    )


def fingerprint(img: Any, size: int = 32) -> np.ndarray:


    a = _as_array(img).astype(np.float32).mean(axis=2)
    h, w = a.shape
    ys = np.linspace(0, h, size + 1).astype(int)
    xs = np.linspace(0, w, size + 1).astype(int)
    out = np.zeros((size, size), dtype=np.float32)
    for i in range(size):
        for j in range(size):
            block = a[ys[i] : max(ys[i] + 1, ys[i + 1]), xs[j] : max(xs[j] + 1, xs[j + 1])]
            out[i, j] = block.mean() if block.size else 0.0
    return out


def shortlist_pairs(
    paths: Sequence[str | os.PathLike[str]], *, cutoff: float = 6.0, size: int = 32
) -> list[tuple[str, str]]:


    sigs = {str(p): fingerprint(load(p), size) for p in paths}
    keys = sorted(sigs)
    out: list[tuple[str, str]] = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if float(np.abs(sigs[keys[i]] - sigs[keys[j]]).mean()) <= cutoff:
                out.append((keys[i], keys[j]))
    return out
