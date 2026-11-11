#!/usr/bin/env python3

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evalsys"))
from evalsys.taskgen.visual_rescore import rescore_visuals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="new output directory; source evidence is not overwritten")
    parser.add_argument("--visual-inputs", type=Path, help="saved visual_inputs.json (records video paths and rubric)")
    parser.add_argument("--record-missing", action="store_true", help="record the retained Godot submission when no film exists; never rerun an agent")
    parser.add_argument("--package", type=Path, help="retained task package when its path has moved")
    parser.add_argument("--submission", type=Path, help="retained submission when its path has moved")
    parser.add_argument("--game-rubric", action="store_true",
                        help="use the Mode-5 game-rubric VLM protocol")
    parser.add_argument("--registry", help="visual scoring registry; preserves a saved modeN-vlm1 registry by default")
    args = parser.parse_args()
    try:
        data = rescore_visuals(args.report, args.out, visual_inputs=args.visual_inputs,
                              package=args.package, submission=args.submission,
                              record_missing=args.record_missing,
                              game_rubric=args.game_rubric, registry_version=args.registry)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        parser.exit(2, f"error: {exc}\n")
    card = data["scorecard"]
    print(f"objective_total={card.get('objective_total', {}).get('score')}")
    print(f"weighted_total={card['weighted_total']['score']}")
    print(f"assessment_status={card.get('assessment_status')}")
    print(f"report={args.out / 'report.json'}")
    return 0 if card["weighted_total"]["score"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
