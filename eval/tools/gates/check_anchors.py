#!/usr/bin/env python3


from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_runtime as cr

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


DRIFT_PCT = 2.0


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", REPO, *args],
                          capture_output=True, text=True).stdout


def modified_anchors() -> list[str]:
    out = []
    for line in git("status", "--porcelain").splitlines():
        path = line[3:].strip().strip('"')
        low = path.lower()
        if "/compare/" not in low or not low.endswith(".png"):
            continue
        base = os.path.basename(low)
        if "anchor" in base or base.startswith("00_"):
            out.append(path)
    return sorted(out)


def drift(path: str) -> tuple[str, float] | None:

    blob = subprocess.run(["git", "-C", REPO, "show", f"HEAD:{path}"], capture_output=True)
    if blob.returncode != 0:
        return None


    tmp = os.path.join(tempfile.gettempdir(), f"_anchor_head_{os.getpid()}.png")
    with open(tmp, "wb") as fh:
        fh.write(blob.stdout)
    try:
        old = cr.measure_frame(tmp)
        new = cr.measure_frame(os.path.join(REPO, path))
    except Exception as exc:
        return (f"unmeasurable ({type(exc).__name__})", float("inf"))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    worst_key, worst = "", 0.0
    for key, o in old.items():
        pct = abs(o - new[key]) / max(abs(o), 1e-9) * 100.0
        if pct > worst:
            worst_key, worst = key, pct
    return (worst_key, worst)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--restore", action="store_true",
                    help="git checkout the anchors whose composition did not actually change")
    args = ap.parse_args()

    hits = modified_anchors()
    if not hits:
        print("anchors: clean — no frozen frame has been overwritten")
        return 0

    incidental, real = [], []
    for path in hits:
        d = drift(path)
        if d is None:
            print(f"  NEW      {path}  (not in HEAD — commit it to freeze it)")
            real.append(path)
            continue
        key, pct = d
        if pct <= DRIFT_PCT:
            print(f"  REWRIT   {path}  same picture, worst {key} {pct:.2f}%")
            incidental.append(path)
        else:
            print(f"  CHANGED  {path}  {key} moved {pct:.2f}% — a real re-lock, must be documented")
            real.append(path)

    if args.restore and incidental:
        subprocess.run(["git", "-C", REPO, "checkout", "--", *incidental], check=False)
        print(f"restored {len(incidental)} anchor(s) that were rewritten without changing")

    print(f"\n{len(incidental)} rewritten identically, {len(real)} genuinely different")
    if real:
        print("A genuinely different anchor is only legitimate as a deliberate, documented re-lock.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
