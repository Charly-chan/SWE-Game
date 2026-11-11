"""Opt-in real Unity/License integration coverage for the Community profile.

The public CI must not enable this module.  A trusted runner supplies an active
``gb mode5 setup`` state plus private positive/auto-win Unity submissions.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from evalsys.taskgen.mode5.community_cli import _evaluator_failure
from evalsys.taskgen.mode5.community_profile import load_default_profile
from evalsys.taskgen.mode5.docker_environment import CommunityDockerError, DockerClient
from evalsys.taskgen.mode5.docker_evaluator import evaluate_in_container, verify_artifact_manifest
from evalsys.taskgen.mode5.docker_license import LicenseProvider


pytestmark = pytest.mark.skipif(
    os.environ.get("GB_RUN_MODE5_DOCKER_INTEGRATION") != "1",
    reason="set GB_RUN_MODE5_DOCKER_INTEGRATION=1 on a trusted licensed runner",
)


def _required_directory(name: str) -> Path:
    value = os.environ.get(name, "")
    if not value:
        pytest.skip(f"{name} is required by the controlled integration runner")
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        pytest.fail(f"{name} is not a directory")
    return path


@pytest.fixture(scope="session")
def active_state():
    root = Path(os.environ.get("GB_MODE5_HOME", Path.home() / ".cache/gamebench/mode5"))
    lock = json.loads((root / "image-lock.json").read_text(encoding="utf-8"))
    preflight = json.loads((root / "last-preflight.json").read_text(encoding="utf-8"))
    config = json.loads((root / "license-provider.json").read_text(encoding="utf-8"))
    if config["provider"] == "file":
        provider = LicenseProvider.file(Path(config["source"]))
    elif config["provider"] == "existing-home":
        provider = LicenseProvider.existing_home(Path(config["source"]))
    elif config["provider"] == "floating":
        provider = LicenseProvider.floating(config["endpoint"])
    else:
        pytest.fail("unsupported integration license provider")
    return root, lock, preflight, provider


@pytest.fixture(scope="session")
def real_inputs(tmp_path_factory):
    # Stage operator fixtures once on the runner filesystem. Do not rely on an
    # external/removable source remaining mounted throughout a long suite.
    package = _required_directory("GB_MODE5_INTEGRATION_PACKAGE")
    positive = _required_directory("GB_MODE5_INTEGRATION_POSITIVE_SUBMISSION")
    staged = tmp_path_factory.mktemp("licensed-inputs")
    shutil.copytree(package, staged / "package")
    shutil.copytree(positive, staged / "positive")
    return staged / "package", staged / "positive"


def _evaluate(package, submission, out, active_state, *, provider=None):
    _, lock, preflight, configured_provider = active_state
    return evaluate_in_container(
        repo_root=Path(__file__).resolve().parents[4], package=package,
        submission=submission, out=out, profile=load_default_profile(),
        image_lock=lock, preflight=preflight,
        license_provider=provider or configured_provider,
        docker=DockerClient(os.environ.get("GB_DOCKER_BIN", "docker")),
    )


def _headline(report):
    return dict((report.get("scorecard") or {}).get("headline") or report.get("headline") or {})


def test_positive_fixture_import_build_run_capture_without_paid_judge(tmp_path, active_state, real_inputs):
    report = _evaluate(*real_inputs, tmp_path / "positive", active_state)
    headline = _headline(report)
    # Build/run evidence and proxy credit do not imply a completed game.
    assert headline["ranking_eligible"] is True
    assert headline["score"] > 0
    card = report["scorecard"]
    assert card["official_total"] is None and card["paper_compatible"] is False
    assert set(card["components"]) == {"mechanics", "playability", "structure", "visual", "stability"}
    # This fixture is intentionally partial, but the release protocol allows
    # the Visual component to earn its full 15-point weight.
    assert 0 <= card["components"]["visual"]["points"] <= 15
    assert card["components"]["stability"]["points"] > 0
    rows = {item["id"]: item for item in report["items"]}
    for ident in ("unity_build", "unity_probe", "unity_evaluator_capture"):
        assert rows[ident]["verdict"] == "passed"
    assert report["fidelity_measurement"]["judge"] == "none"
    assert report["environment"]["environment_class"] == "community-docker"
    verify_artifact_manifest(tmp_path / "positive")


def test_csharp_syntax_error_preserves_static_proxy(tmp_path, active_state, real_inputs):
    package, positive = real_inputs
    submission = tmp_path / "syntax-submission"
    shutil.copytree(positive, submission)
    bad = submission / "Assets" / "Game" / "GBIntentionalSyntaxError.cs"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("public class GBIntentionalSyntaxError { this is invalid C#; }\n", encoding="utf-8")
    report = _evaluate(package, submission, tmp_path / "syntax-evaluation", active_state)
    headline = _headline(report)
    assert headline["score"] > 0
    assert headline["ranking_eligible"] is True
    assert report["engine"]["build"]["status"] == "fail"
    assert report["engine"]["build"]["attribution"] == "submission"
    reading = report["scorecard"]["evidence_score"]
    assert reading["runtime_verified"] is False
    goal = next(row for row in reading["criteria"] if row["id"] == "playability.final_goal")
    assert goal["points"] == 0


def test_invalid_license_is_infrastructure_not_candidate(tmp_path, active_state, real_inputs):
    with tempfile.TemporaryDirectory(prefix="gamebench-invalid-license-") as private:
        invalid = Path(private) / "invalid.ulf"
        invalid.write_text("not-a-unity-entitlement\n", encoding="utf-8")
        invalid.chmod(0o600)
        with pytest.raises((CommunityDockerError, ValueError), match="license") as caught:
            _evaluate(*real_inputs, tmp_path / "invalid-license", active_state,
                      provider=LicenseProvider.file(invalid))
    failure = _evaluator_failure(caught.value)
    assert failure["attribution"] == "evaluator_infrastructure"
    assert failure["reason_code"] == "unity_license_invalid"


def test_broken_hidden_suite_is_unscored_infrastructure(tmp_path, active_state, real_inputs):
    package, submission = real_inputs
    broken = tmp_path / "broken-package"
    shutil.copytree(package, broken)
    hidden = broken / "hidden"
    assert hidden.is_dir()
    shutil.rmtree(hidden)
    with pytest.raises(CommunityDockerError, match="infrastructure"):
        _evaluate(broken, submission, tmp_path / "broken-hidden", active_state)


def test_no_input_auto_win_fails_causal_gate(tmp_path, active_state, real_inputs):
    package, positive = real_inputs
    if os.environ.get("GB_MODE5_INTEGRATION_AUTOWIN_SUBMISSION"):
        submission = _required_directory("GB_MODE5_INTEGRATION_AUTOWIN_SUBMISSION")
    else:
        # A transparent controlled negative, never a model submission. No
        # hidden predicates or controller files are changed to obtain a win.
        submission = tmp_path / "auto-win-submission"
        shutil.copytree(positive, submission)
        script = submission / "Assets/Game/GBControlledAutoWin.cs"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text('''using System.Collections;
using UnityEngine;
using GameBenchmark;
public sealed class GBControlledAutoWin : MonoBehaviour
{
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Install()
    {
        var root = new GameObject("GBControlledAutoWin");
        Object.DontDestroyOnLoad(root);
        root.AddComponent<GBControlledAutoWin>();
    }
    private IEnumerator Start()
    {
        while (true)
        {
            GBOutcome.ReportSuccess();
            yield return new WaitForSeconds(0.1f);
        }
    }
}
''', encoding="utf-8")
    report = _evaluate(package, submission, tmp_path / "auto-win", active_state)
    headline = _headline(report)
    assert report["resolved"] is False
    rows = {row["id"]: row for row in report["items"]}
    assert rows["unity_auto_win_ready"]["verdict"] == "failed"
    assert "unity_auto_win_ready" in report["scorecard"]["strict"]["failed_required_items"]
    goal = next(row for row in report["scorecard"]["evidence_score"]["criteria"]
                if row["id"] == "playability.final_goal")
    assert goal["points"] == 0


def test_sdk_source_change_is_candidate_failure(tmp_path, active_state, real_inputs):
    package, positive = real_inputs
    submission = tmp_path / "sdk-changed-submission"
    shutil.copytree(positive, submission)
    sdk = submission / "Assets/GameBenchmarkSDK/GBObservableState.cs"
    original = sdk.read_text(encoding="utf-8")
    sdk.write_text(original + "\n// controlled unauthorized SDK source change\n", encoding="utf-8")
    report = _evaluate(package, submission, tmp_path / "sdk-changed", active_state)
    item = next(row for row in report["items"] if row["id"] == "unity_sdk_integrity")
    assert item["verdict"] == "failed"
    assert item["attribution"] == "submission"
    assert report["engine"]["build"]["status"] == "skipped"
    assert report["engine"]["build"]["blocked_by"] == "unity_sdk_integrity"
    assert report["engine"]["ran"] is False
    assert report["engine"]["runtime_probe_ran"] is False
    assert "unity_sdk_integrity" in report["scorecard"]["strict"]["failed_required_items"]
    assert _headline(report)["score"] is None
    assert _headline(report)["ranking_eligible"] is False
    assert report["scorecard"]["evidence_score"]["status"] == "integrity_failed"


def test_host_rejects_tampered_artifact(tmp_path, active_state, real_inputs):
    out = tmp_path / "tamper"
    _evaluate(*real_inputs, out, active_state)
    report = out / "report.json"
    report.write_text(report.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(CommunityDockerError, match="(size|digest) mismatch"):
        verify_artifact_manifest(out)
