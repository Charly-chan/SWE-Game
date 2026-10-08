#!/usr/bin/env python3


from __future__ import annotations

import argparse
import json
import os
import sys

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit("flat_share.py needs numpy and Pillow")


def measure(path: str, crop: tuple[int, int, int, int], bits: int) -> dict:
    im = Image.open(path).convert("RGB")
    a = np.asarray(im, dtype=np.uint8)
    h, w = a.shape[:2]
    top, bottom, left, right = crop
    a = a[top:h - bottom if bottom else h, left:w - right if right else w]
    if a.size == 0:
        return {"path": path, "error": "crop removed the whole frame"}
    if bits < 8:
        shift = 8 - bits
        a = (a >> shift) << shift
    flat = a.reshape(-1, 3)
    packed = (flat[:, 0].astype(np.uint32) << 16) | \
             (flat[:, 1].astype(np.uint32) << 8) | flat[:, 2].astype(np.uint32)
    vals, counts = np.unique(packed, return_counts=True)
    order = np.argsort(counts)[::-1]
    total = int(counts.sum())
    top_n = []
    for i in order[:6]:
        v = int(vals[i])
        top_n.append({
            "hex": "#%06X" % v,
            "pct": round(100.0 * int(counts[i]) / total, 2),
        })
    return {
        "path": path,
        "size": f"{w}x{h}",
        "measured": f"{a.shape[1]}x{a.shape[0]}",
        "distinct": int(vals.size),
        "top1": top_n[0]["pct"] if top_n else 0.0,
        "top2": round(sum(t["pct"] for t in top_n[:2]), 2),
        "top5": round(sum(t["pct"] for t in top_n[:5]), 2),
        "colors": top_n,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frames", nargs="*")
    ap.add_argument("--dir", action="append", default=[])
    ap.add_argument("--top", type=int, default=0, help="pixels of HUD to skip at the top")
    ap.add_argument("--bottom", type=int, default=0)
    ap.add_argument("--left", type=int, default=0)
    ap.add_argument("--right", type=int, default=0)
    ap.add_argument("--bits", type=int, default=8, help="keep this many bits per channel")
    ap.add_argument("--json")
    args = ap.parse_args()

    paths = list(args.frames)
    for d in args.dir:
        for base, _dirs, files in os.walk(d):
            paths += [os.path.join(base, f) for f in sorted(files)
                      if f.lower().endswith(".png")]
    paths = sorted(set(paths))
    if not paths:
        return ap.error("no frames")

    crop = (args.top, args.bottom, args.left, args.right)
    rows = [measure(p, crop, args.bits) for p in paths]
    ok = [r for r in rows if "error" not in r]

    print(f"{'frame':<34} {'distinct':>8} {'top1%':>7} {'top2%':>7} {'top5%':>7}  dominant")
    for r in rows:
        if "error" in r:
            print(f"{os.path.basename(r['path']):<34} {r['error']}")
            continue
        dom = " ".join(f"{c['hex']}:{c['pct']:.1f}" for c in r["colors"][:2])
        print(f"{os.path.basename(r['path']):<34} {r['distinct']:>8} "
              f"{r['top1']:>7.1f} {r['top2']:>7.1f} {r['top5']:>7.1f}  {dom}")
    if ok:
        print(f"\n{len(ok)} frames — mean top1 {sum(r['top1'] for r in ok)/len(ok):.1f}%, "
              f"mean top2 {sum(r['top2'] for r in ok)/len(ok):.1f}%, "
              f"mean distinct {sum(r['distinct'] for r in ok)/len(ok):.0f}, "
              f"worst top1 {max(r['top1'] for r in ok):.1f}% "
              f"({os.path.basename(max(ok, key=lambda r: r['top1'])['path'])})")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rows, fh, indent=2)
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
