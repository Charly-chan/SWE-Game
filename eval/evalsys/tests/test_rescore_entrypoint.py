
import json
import os
from pathlib import Path
import subprocess
import sys

from evalsys.taskgen.scorecard import DEFAULT_REGISTRY_BY_MODE

ROOT = Path(__file__).resolve().parents[3]


def test_rescore_defaults_to_each_reports_mode_and_retains_missing_visuals(tmp_path):
    paths = []
    originals = {}
    for mode in DEFAULT_REGISTRY_BY_MODE:
        folder = tmp_path / mode
        folder.mkdir()
        report = folder / "report.json"
        report.write_text(json.dumps({"mode": mode, "game_id": "fixture", "items": [],
                                      "engine": {}, "resolved": False}))
        paths.append(str(report))
        originals[report] = report.read_bytes()
    out = tmp_path / "scores"
    result = subprocess.run(
        [sys.executable, str(ROOT / "eval/tools/rescore.py"), *paths, "--out-dir", str(out)],
        env=os.environ | {"PYTHONPATH": str(ROOT / "eval/evalsys")},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for mode, version in DEFAULT_REGISTRY_BY_MODE.items():
        card = json.loads((out / f"{mode}.scorecard.json").read_text())["scorecard"]
        assert card["registry_version"] == version
        if mode in {"brief", "gdd", "skeleton"}:
            assert card["weighted_total"]["score"] is None
            assert card["ranking_eligible"] is False
    assert all(path.read_bytes() == original for path, original in originals.items())


def test_rescore_historical_registry_remains_an_explicit_choice(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"mode": "brief", "game_id": "fixture", "items": [],
                                  "engine": {}, "resolved": False}))
    result = subprocess.run(
        [sys.executable, str(ROOT / "eval/tools/rescore.py"), str(report),
         "--registry", "2026-09-11.evidence1"],
        env=os.environ | {"PYTHONPATH": str(ROOT / "eval/evalsys")},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2026-09-11.evidence1" in result.stdout
