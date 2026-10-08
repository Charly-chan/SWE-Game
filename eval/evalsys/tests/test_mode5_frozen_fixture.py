

from types import SimpleNamespace

import pytest

from evalsys.taskgen.evaluate import _attribute_mode5_items
from evalsys.taskgen.scorecard import (
    MODE5_MDVA_REGISTRY_VERSION, default_registry_for_mode, score_task_result,
)
from evalsys.taskgen.mode5.pipeline import phase_order
from evalsys.verdict import Attribution, failed, inconclusive, passed


GATES = (
    "task_gdd_contract", "verifier_profile_complete", "unity_layout",
    "unity_interface", "unity_sdk_integrity", "port_contract_alignment",
    "no_eval_smuggling", "no_bundled_godot_runtime", "build_recipe",
    "unity_anti_grant_static", "unity_build", "unity_probe", "unity_input_dispatch",
    "ops_present", "ops_valid", "ops_not_idle", "unity_auto_win_ready",
    "null_no_win", "unity_counterfactual",
)
CAPABILITIES = (
    "unity_mechanic_trace", "causal_witness", "unity_runtime_stability",
    "unity_hidden_behavior", "unity_structure_fidelity", "unity_vlm",
)


def fixture(*overrides):
    rows = {ident: passed(ident) for ident in (*GATES, *CAPABILITIES)}
    rows.update({row.id: row for row in overrides})
    return SimpleNamespace(
        package=SimpleNamespace(manifest={"mode": "port", "game_id": "frozen-fixture"}),
        items=list(rows.values()), resolved=True,
    )


def test_frozen_registry_full_score_is_100():
    assert default_registry_for_mode("port") == MODE5_MDVA_REGISTRY_VERSION
    assert phase_order() == ("static", "runtime", "objective", "visual", "attribution")
    card = score_task_result(fixture(), MODE5_MDVA_REGISTRY_VERSION)
    assert card["registry_version"] == MODE5_MDVA_REGISTRY_VERSION
    assert card["weighted_total"]["score"] == 100.0
    assert card["ranking_eligible"] is True


def test_candidate_build_failure_is_a_decided_zero():
    items = _attribute_mode5_items([
        failed("unity_build", attribution=Attribution.SUBMISSION),
        *(inconclusive(ident, detail="Unity build did not produce a runnable player")
          for ident in ("unity_probe", "unity_mechanic_trace", "unity_runtime_stability",
                        "unity_hidden_behavior", "unity_structure_fidelity", "unity_vlm")),
    ])
    by_id = {row.id: row for row in items}
    assert by_id["unity_mechanic_trace"].credit == 0.0
    assert by_id["unity_mechanic_trace"].attribution is Attribution.SUBMISSION


def test_missing_vlm_is_inconclusive_and_withholds_only_headline():
    card = score_task_result(
        fixture(inconclusive("unity_vlm", detail="VLM provider unavailable",
                             evidence={"owner": "harness", "retryable": True})),
        MODE5_MDVA_REGISTRY_VERSION,
    )
    assert card["objective_total"]["score"] == 70.0
    assert card["weighted_total"]["score"] is None
    assert card["outcome_status"] == "evaluation_incomplete"


def test_frozen_registry_keeps_harness_gap_as_null_not_zero():
    environment = {
        "profile_id": "local-wsl-dev", "environment_class": "local-wsl-dev",
        "score_eligible": False, "certified": False,
        "certification_status": "pending",
        "detail": "certified Unity VM unavailable",
    }
    card = score_task_result(
        fixture(inconclusive("unity_build", detail="certified Unity VM unavailable",
                             evidence={"attribution": "infrastructure",
                                       "environment": environment})),
        MODE5_MDVA_REGISTRY_VERSION,
    )
    assert card["weighted_total"]["score"] is None
    assert card["outcome_status"] == "infrastructure_inconclusive"


def test_host_finalizer_updates_top_level_score_alongside_the_headline(tmp_path):
    import json
    import tarfile
    from unittest.mock import patch
    from evalsys.taskgen.mode5.finalize import finalize_mode5

    result = fixture(failed("unity_build", attribution=Attribution.SUBMISSION))
    source = tmp_path / "offline.json"
    source.write_text(json.dumps({
        "mode": "port", "items": [item.to_dict() for item in result.items],
        "score": {"score": None}, "headline": {"score": None},
    }))
    artifacts = tmp_path / "artifacts.tar.gz"
    with tarfile.open(artifacts, "w:gz"):
        pass
    with patch("evalsys.taskgen.mode5.finalize.TaskPackage.read", return_value=result.package):
        final = finalize_mode5(source, tmp_path / "package", artifacts, tmp_path / "final")
    assert final["scorecard"]["weighted_total"]["score"] == 0.0
    assert final["score"] == final["headline"]
    assert final["score"]["score"] == 0.0
    assert final["score_scope"] == "mode5_model_capability_only"


@pytest.mark.parametrize("cause", ["infrastructure_build", "auto_win", "capture_gap", "lost_capture"])
def test_finalizer_does_not_turn_independent_missing_media_into_candidate_zero(tmp_path, cause):
    import json
    import tarfile
    from unittest.mock import patch
    from evalsys.taskgen.mode5.finalize import finalize_mode5
    cases = {
        "infrastructure_build": [failed("unity_build", attribution=Attribution.HARNESS)],
        "auto_win": [failed("unity_auto_win_ready", attribution=Attribution.SUBMISSION)],
        "capture_gap": [failed("unity_build", attribution=Attribution.SUBMISSION),
                        inconclusive("unity_evaluator_capture", detail="recorder unavailable",
                                     attribution=Attribution.HARNESS)],
        "lost_capture": [failed("unity_probe", attribution=Attribution.SUBMISSION),
                         passed("unity_evaluator_capture")],
    }
    result = fixture(*cases[cause])
    source = tmp_path / "offline.json"
    source.write_text(json.dumps({"mode": "port", "items": [i.to_dict() for i in result.items]}))
    artifacts = tmp_path / "artifacts.tar.gz"
    with tarfile.open(artifacts, "w:gz"):
        pass
    with patch("evalsys.taskgen.mode5.finalize.TaskPackage.read", return_value=result.package):
        with pytest.raises(RuntimeError, match="evaluator_failed"):
            finalize_mode5(source, tmp_path / "package", artifacts, tmp_path / "final")
    assert not (tmp_path / "final" / "report.json").exists()
