#!/usr/bin/env python3


from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:
    sys.stderr.write(f"need numpy and Pillow: {exc}\n")
    sys.exit(2)

Image.MAX_IMAGE_PIXELS = None

_EVALSYS_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "evalsys"))
if _EVALSYS_ROOT not in sys.path:
    sys.path.insert(0, _EVALSYS_ROOT)
from evalsys.engine import hostenv


WATERMARK_RGB = (207, 130, 23)


SKIP_DIRS = {
    ".godot", ".git", ".import", "__pycache__", "node_modules",
    "inputs", "staging", "_archives", "raw", "_raw", "downloads",
    "compare", "verify", "recording", "shots", "build", "export", ".cache",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"}


MIN_COLOURS_TO_JUDGE = 40

MAX_COLOURS_TO_JUDGE = 400

MIN_LAPLACIAN = 5.0
MAX_LAPLACIAN = 25.0

SUSPECT_COLOUR_MAX = 700
SUSPECT_LAPLACIAN = 30.0


BLOCK_COLOUR_ALARM = 40


def analyse(path: str) -> dict:

    with Image.open(path) as im:
        im = im.convert("RGBA")
        width, height = im.size
        arr = np.asarray(im)

    flat = arr.reshape(-1, 4)
    uniq, counts = np.unique(flat, axis=0, return_counts=True)
    n_colours = int(len(uniq))

    opaque = arr[..., 3] == 255
    watermark_px = int(
        (
            (arr[..., 0] == WATERMARK_RGB[0])
            & (arr[..., 1] == WATERMARK_RGB[1])
            & (arr[..., 2] == WATERMARK_RGB[2])
            & opaque
        ).sum()
    )


    grey = arr[..., :3].astype(np.float32).mean(-1)
    if grey.shape[0] >= 3 and grey.shape[1] >= 3:
        lap = (
            4 * grey[1:-1, 1:-1]
            - grey[:-2, 1:-1] - grey[2:, 1:-1]
            - grey[1:-1, :-2] - grey[1:-1, 2:]
        )
        interior = arr[1:-1, 1:-1, 3] == 255
        laplacian = float(np.median(np.abs(lap[interior]))) if interior.sum() >= 20 else -1.0
    else:
        laplacian = -1.0


    worst_block = 0
    worst_block_at = None
    if max(width, height) <= 4096:
        step = 16
        for by in range(0, height - step + 1, step):
            for bx in range(0, width - step + 1, step):
                block = arr[by:by + step, bx:bx + step]
                if (block[..., 3] == 255).sum() < step * step * 0.5:
                    continue
                bc = int(len(np.unique(block.reshape(-1, 4), axis=0)))
                if bc > worst_block:
                    worst_block, worst_block_at = bc, (bx, by)

    return {
        "path": path,
        "width": width,
        "height": height,
        "colours": n_colours,
        "watermark_px": watermark_px,
        "laplacian": laplacian,
        "worst_block_colours": worst_block,
        "worst_block_at": worst_block_at,
    }


def classify(rec: dict, threshold: int) -> tuple[str, str]:

    if rec["watermark_px"] > 0:
        return "LOCKED", f"{rec['watermark_px']} px of exact Premium-Version orange"

    colours, lap = rec["colours"], rec["laplacian"]

    if colours <= threshold or lap < 0:
        return "clean", ""


    # documents the file as Kenney Physics Assets elementMetal030.png, CC0 1.0, with the full
    # digest. The same CC0 file under two names, accused twice.


    if colours <= MAX_COLOURS_TO_JUDGE and MIN_LAPLACIAN <= lap <= MAX_LAPLACIAN:
        return "LOCKED", (
            f"{colours} colours with median |Laplacian| {lap:.1f} "
            "— moderate palette plus high-frequency noise, the noise-filter signature"
        )
    if colours <= SUSPECT_COLOUR_MAX and MIN_LAPLACIAN <= lap <= SUSPECT_LAPLACIAN:
        return "SUSPECT", (
            f"{colours} colours with median |Laplacian| {lap:.1f} "
            "— just outside the locked band, render and look"
        )


    if colours <= SUSPECT_COLOUR_MAX and rec["worst_block_colours"] >= BLOCK_COLOUR_ALARM:
        at = rec["worst_block_at"]
        return "SUSPECT", (
            f"16x16 block at {at} holds {rec['worst_block_colours']} colours "
            "— dense patch inside otherwise clean art, render and look"
        )
    return "clean", ""


def walk_images(roots: list[str], include_staging: bool):
    for root in roots:
        if not os.path.exists(root):
            sys.stderr.write(f"warning: no such path: {root}\n")
            continue
        if os.path.isfile(root):
            yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if not include_staging:
                dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
            else:
                dirnames[:] = [
                    d for d in dirnames
                    if d.lower() not in {".godot", ".git", ".import", "__pycache__"}
                ]
            for name in sorted(filenames):
                if os.path.splitext(name)[1].lower() in IMAGE_EXT:
                    yield os.path.join(dirpath, name)


def render_for_review(path: str, outdir: str, scale_target: int = 320) -> str | None:

    try:
        os.makedirs(outdir, exist_ok=True)
        with Image.open(path) as im:
            im = im.convert("RGBA")
            scale = max(1, min(8, scale_target // max(im.width, 1)))
            big = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
            mat = Image.new("RGBA", big.size, (25, 25, 35, 255))
            mat.alpha_composite(big)
            safe = path.strip("/").replace("/", "__")
            out = os.path.join(outdir, safe)
            mat.convert("RGB").save(out)
        return out
    except Exception:
        return None


def main() -> int:
    hostenv.configure_stdio()
    ap = argparse.ArgumentParser(
        description="Find premium-locked / watermarked art in shipped game directories.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="A hit is a lead, not a verdict. Render it and look at it before acting.",
    )
    ap.add_argument("roots", nargs="+", help="directories (or files) to scan")
    ap.add_argument("--jsonl", metavar="FILE", help="write one JSON object per image")
    ap.add_argument("--render", metavar="DIR", help="dump renders of non-clean hits here")
    ap.add_argument("--threshold", type=int, default=MIN_COLOURS_TO_JUDGE,
                    help=f"ignore images with this many colours or fewer (default {MIN_COLOURS_TO_JUDGE})")
    ap.add_argument("--all", action="store_true", help="include clean files in --jsonl output")
    ap.add_argument("--include-staging", action="store_true",
                    help="also scan inputs/, staging, raw packs and QA output")
    ap.add_argument("--quiet", action="store_true", help="suppress the human-readable summary")
    args = ap.parse_args()

    hits: list[dict] = []
    skipped: list[dict] = []
    per_dir: dict[str, list[int]] = {}
    total = 0
    sink = open(args.jsonl, "w", encoding="utf-8") if args.jsonl else None

    try:
        for path in walk_images(args.roots, args.include_staging):
            total += 1
            try:
                rec = analyse(path)
            except Exception as exc:


                skipped.append({"path": path, "error": f"{type(exc).__name__}: {exc}"})
                continue

            tier, reason = classify(rec, args.threshold)
            rec["tier"] = tier
            rec["reason"] = reason


            rec["basis"] = "watermark" if rec["watermark_px"] > 0 else "signature"
            if rec["colours"] > args.threshold:
                per_dir.setdefault(os.path.dirname(path), []).append(rec["laplacian"])

            if tier != "clean":
                if args.render:
                    rec["render"] = render_for_review(path, args.render)
                hits.append(rec)
            if sink and (args.all or tier != "clean"):
                sink.write(json.dumps(rec) + "\n")
    finally:
        if sink:
            sink.close()

    if not args.quiet:
        order = {"LOCKED": 0, "SUSPECT": 1}
        for rec in sorted(hits, key=lambda r: (order[r["tier"]], -r["colours"])):
            print(f"{rec['tier']:8s} {rec['colours']:5d}col lap={rec['laplacian']:5.1f}  {rec['path']}")
            print(f"         {rec['reason']}")

        counts = {t: sum(1 for h in hits if h["tier"] == t) for t in ("LOCKED", "SUSPECT")}
        print()
        print(f"scanned {total} images, {len(skipped)} unreadable")
        print(f"  LOCKED  {counts['LOCKED']:5d}  watermark colour or dither signature — do not ship")
        print(f"  SUSPECT {counts['SUSPECT']:5d}  borderline dither distance — RENDER AND LOOK")


        banded = sorted(
            (
                (statistics.median(v), d, len(v))
                for d, v in per_dir.items()
                if len(v) >= 4 and MIN_LAPLACIAN <= statistics.median(v) <= MAX_LAPLACIAN
            )
        )[:10]
        if banded:
            print("\ndirectories whose median sits inside the noise band:")
            for med, d, n in banded:
                print(f"  median |Laplacian| {med:7.1f} over {n:5d} files  {d}")

        if skipped:
            print(f"\nskipped {len(skipped)} unreadable files (likely mid-write):")
            for s in skipped[:20]:
                print(f"  {s['path']}  {s['error']}")
            if len(skipped) > 20:
                print(f"  ... and {len(skipped) - 20} more")

    return 1 if any(h["tier"] == "LOCKED" for h in hits) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
