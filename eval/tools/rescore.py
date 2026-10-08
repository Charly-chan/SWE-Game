#!/usr/bin/env python3


from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from evalsys.taskgen.evaluate import REPORT_SCHEMA
from evalsys.taskgen.scorecard import REGISTRY_VERSIONS, score_task_result
from evalsys.verdict import Attribution, Item
from evalsys.report.terminology import display_terms


def rebuild(report: dict, report_path: Path | None = None) -> SimpleNamespace:
    items = []
    allowed = set(Item.__dataclass_fields__)
    for raw_item in report.get("items") or []:
        raw = {key: value for key, value in raw_item.items() if key in allowed}
        if raw.get("attribution"):
            raw["attribution"] = Attribution(raw["attribution"])
        items.append(Item(**raw))
    return SimpleNamespace(
        package=SimpleNamespace(
            manifest={
                "mode": report.get("mode"),
                "game_id": report.get("game_id"),
            },
            root=Path(str(report.get("package"))) if report.get("package") else None,
        ),
        items=items,
        resolved=report.get("resolved"),
        stored_report=report,
        report_path=report_path,
    )


def upgrade_score_contract(report: dict, scorecard: dict) -> None:


    report["report_schema"] = REPORT_SCHEMA
    if report.get("mode") != "port":
        return
    old_score = report.get("score")
    behavior = report.setdefault("behavior", {})
    if "score" not in behavior and isinstance(old_score, dict) and "lo" in old_score:
        behavior["score"] = old_score
    behavior["score_scope"] = "behavior_only_diagnostic"
    weighted = scorecard.get("weighted_total") or {}
    headline = {
        "status": scorecard.get("outcome_status") or "evaluation_incomplete",
        "score": weighted.get("score"),
        "scale": weighted.get("scale") or "0-100",
        "ranking_eligible": bool(scorecard.get("ranking_eligible")),
        "score_scope": "mode5_model_capability_only",
    }
    report["headline"] = headline
    report["score"] = dict(headline)
    report["score_scope"] = "mode5_model_capability_only"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", help="report.json file(s)")
    parser.add_argument(
        "--write",
        action="store_true",
        help="replace scorecard in place after saving report.json.pre-rescore",
    )
    parser.add_argument(
        "--registry", "--registry-version",
        dest="registry_version",
        default=None,
        choices=REGISTRY_VERSIONS,
        help="scorecard registry (default: the current registry for each report's task mode)",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="write the fresh scorecard as <out-dir>/<report-parent-name>.scorecard.json "
             "instead of touching the report (rescoring cells under another registry row)",
    )
    args = parser.parse_args()

    for name in args.reports:
        path = Path(name).resolve()
        report = json.loads(path.read_text(encoding="utf-8"))
        old = report.get("scorecard") or {}
        fresh = score_task_result(
            rebuild(report, path), registry_version=args.registry_version
        )
        old_total = old.get("weighted_total") or {}
        new_total = fresh.get("weighted_total") or {}
        print(
            f"{path}: weighted_total "
            f"{old_total.get('score')} ({old.get('registry_version', 'missing')}, "
            f"{old_total.get('status', 'missing')}) -> "
            f"{new_total.get('score')} ({fresh['registry_version']}, "
            f"{new_total.get('status', 'missing')}); "
            f"ranking_eligible "
            f"{old.get('ranking_eligible')} -> {fresh.get('ranking_eligible')}"
        )
        composition = new_total.get("composition")
        if fresh.get("resolution_status"):
            print(
                f"  resolution: {fresh['resolution_status']} "
                f"F2P={fresh.get('f2p_rate')} P2P={fresh.get('p2p_rate')} "
                f"resolved={fresh.get('resolved')}"
            )
        elif composition:
            print(
                f"  composition: repair_credit={composition.get('repair_credit')} "
                f"gates={composition.get('gates')} "
                f"regression_mean={composition.get('regression_mean')}"
                + (f" failed_gates={composition['failed_gates']}"
                   if composition.get("failed_gates") else "")
            )
        for item in new_total.get("unmeasured") or []:
            print(
                f"  unmeasured: {item.get('source')} <- "
                f"{display_terms(str(item.get('step') or ''))[:200]}"
            )
        if args.out_dir:
            out_dir = Path(args.out_dir).resolve()
            out_dir.mkdir(parents=True, exist_ok=True)


            label = (
                path.parent.parent.name
                if path.name == "report.json" and path.parent.name.startswith("evaluation")
                else path.parent.name
            )
            dest = out_dir / f"{label}.scorecard.json"
            dest.write_text(
                json.dumps({"report": str(path), "scorecard": fresh}, indent=2, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            print(f"  wrote {dest}")
        if args.write:
            backup = path.with_name(path.name + ".pre-rescore")
            shutil.copy2(path, backup)
            report["scorecard"] = fresh
            upgrade_score_contract(report, fresh)
            path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"  wrote {path} (backup {backup})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
