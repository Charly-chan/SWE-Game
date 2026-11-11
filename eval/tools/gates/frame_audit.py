#!/usr/bin/env python3


from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import sys

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit("frame_audit.py needs numpy and Pillow")

FRAME_DIRS = ("compare", "verify", "recording", "shots")


def collect(roots: list[str], explicit: list[str]) -> list[str]:
    out: list[str] = []
    for d in explicit:
        for base, _dirs, files in os.walk(d):
            out += [os.path.join(base, f) for f in files if f.lower().endswith(".png")]
    for r in roots:
        for sub in FRAME_DIRS:
            p = os.path.join(r, sub)
            if not os.path.isdir(p):
                continue
            for base, _dirs, files in os.walk(p):
                out += [os.path.join(base, f) for f in files if f.lower().endswith(".png")]
    return sorted(set(out))


def load(path: str):
    im = Image.open(path).convert("RGB")
    return np.asarray(im, dtype=np.int16)


def audit(paths: list[str], near_pct: float, tol: int) -> dict:
    if not paths:
        return {"frames": 0, "identical": [], "near": [], "effective": 0}

    pixels, digests, sizes = {}, {}, {}
    for p in paths:
        digests[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
        a = load(p)
        pixels[p] = a
        sizes[p] = a.shape[:2]


    by_content: dict[str, list[str]] = {}
    for p in paths:
        key = hashlib.sha256(pixels[p].astype("uint8").tobytes()).hexdigest()
        by_content.setdefault(key, []).append(p)
    identical = [sorted(v) for v in by_content.values() if len(v) > 1]


    reps = [sorted(v)[0] for v in by_content.values()]
    near = []
    for a, b in itertools.combinations(sorted(reps), 2):
        if sizes[a] != sizes[b]:
            continue
        d = np.abs(pixels[a] - pixels[b]).sum(axis=2)
        pct = 100.0 * float((d > tol).mean())
        if pct < near_pct:
            near.append({"a": a, "b": b, "pct": round(pct, 3)})
    near.sort(key=lambda x: x["pct"])

    return {
        "frames": len(paths),
        "effective": len(by_content),
        "identical": identical,
        "near": near,
        "digests": digests,
        "sizes": {p: f"{sizes[p][1]}x{sizes[p][0]}" for p in paths},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="*", help="project directories")
    ap.add_argument("--dir", action="append", default=[], help="scan this directory exactly")
    ap.add_argument("--near", type=float, default=2.0,
                    help="flag pairs differing in fewer than this %% of pixels (default 2.0)")
    ap.add_argument("--tol", type=int, default=8,
                    help="summed-RGB delta above which a pixel counts as different (default 8)")
    ap.add_argument("--json", help="write the full report here")
    args = ap.parse_args()

    paths = collect(args.roots, args.dir)
    rep = audit(paths, args.near, args.tol)

    print(f"frames scanned      : {rep['frames']}")
    print(f"distinct by content : {rep['effective']}")

    bad = 0
    if rep["identical"]:
        bad = 1
        print(f"\nIDENTICAL CONTENT, DIFFERENT NAME  ({len(rep['identical'])} group(s)) -- defect")
        for g in rep["identical"]:
            print(f"  {len(g)} files share one image:")
            for p in g:
                print(f"      {p}")
    else:
        print("\nIDENTICAL CONTENT, DIFFERENT NAME  : none")

    if rep["near"]:
        print(f"\nNEAR-DUPLICATES under {args.near}%  ({len(rep['near'])} pair(s)) -- justify each")
        for n in rep["near"]:
            print(f"  {n['pct']:6.3f}%  {os.path.basename(n['a']):34s} vs {os.path.basename(n['b'])}")
    else:
        print(f"\nNEAR-DUPLICATES under {args.near}%  : none")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rep, fh, indent=2)
        print(f"\nwrote {args.json}")
    return bad


if __name__ == "__main__":
    sys.exit(main())
