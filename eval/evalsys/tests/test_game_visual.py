
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from evalsys.scard.game_visual import (
    GROUP_WEIGHTS, PROTOCOL, RESPONSE_SCHEMA_VERSION,
    aggregate_game_visual, build_response_contract, build_response_json_schema,
    final_credit, judge_game_visual, visual_report,
    validate_judgment, validate_response_items,
)
from evalsys.taskgen.scorecard import (
    MODE1_REDESIGN_REGISTRY_VERSION, MODE2_REDESIGN_REGISTRY_VERSION,
    MODE3_REDESIGN_REGISTRY_VERSION, MODE2_VLM_REGISTRY_VERSION, score_task_result,
)
from evalsys.taskgen.visual_materials import (
    CORPUS, extract_frame, freeze_visual_rubric, prepare_visual_manifest,
)
from evalsys.verdict import Verdict, inconclusive, passed
from test_redesign_evidence_regressions import _complete_result


def criterion(group="A"):
    return {"id": group + "1", "group": group, "title": "Interface composition",
            "description": "Assess the adopted interface.",
            "full_credit": "Main information has purposeful visual hierarchy.",
            "partial_credit": "Main information is subordinated to decorative headings.",
            "zero_credit": "No organized information.",
            "high_score_requirements": ["Main information has purposeful visual hierarchy."],
            "basis": [{"source_id": "reference_video", "frame_ids": ["R001"]}],
            "caps": [{"id": group + "1-C1", "condition": "Major hierarchy failure.", "max_credit": .5}]}


def event_criterion():
    item = criterion("V")
    item.update(
        id="V3",
        title="事件反馈：碰撞、路口提示、拾取",
        description="评各类事件发生时的即时可见信号是否清楚且相互区分。",
        full_credit="已发生的事件有能对应其语义的反馈。",
        partial_credit="充分连续覆盖中反馈与事件明显脱节。",
        zero_credit="明确事件发生且连续覆盖充分时仍没有任何可见反馈。",
    )
    return item


def judgment(item, q=.8, *, capped=False):
    return {"id": item["id"], "attainment": q, "evidence_frames": ["C001F0001"],
            "strengths": ["Information groups are visible."],
            "deficiencies": ["Main information has weak hierarchy."] if q != 1 else [],
            "deduction_checks": [{"deficiency_index": 1, "field": "full_credit",
                                   "quote": item["full_credit"], "frames": ["C001F0001"],
                                   "why_rule_applies": "Decorative text dominates the objective."}] if q != 1 else [],
            "high_score_evidence": [{"requirement_index": 1, "status": "supported" if q == 1 else "partial",
                                     "frames": ["C001F0001"], "observation": "Visible hierarchy."}],
            "cap_checks": [{"id": item["caps"][0]["id"], "triggered": capped,
                            "frames": ["C001F0001"], "reason": "The major condition is present." if capped else "The remaining gap is limited."}],
            "applied_caps": [item["caps"][0]["id"]] if capped else [],
            "missing_evidence": "", "score_rationale": "Grouping succeeds but emphasis remains incomplete."}


def v2_judgment(item, q=.8, *, capped=False):
    return {
        "item_id": item["id"], "outcome": "measured",
        "applicability": {"status": "applicable", "condition_id": None,
                          "observation": "The criterion applies to the visible interface."},
        "attainment": q, "evidence_frames": ["C001F0001"],
        "strengths": ["Information groups are visible."],
        "deficiencies": ["Main information has weak hierarchy."] if q != 1 else [],
        "deficiency_checks": [{"clause_id": f"{item['id']}.full_credit",
                                "frames": ["C001F0001"],
                                "why_rule_applies": "Decorative text dominates the objective."}] if q != 1 else [],
        "high_score_checks": [{"requirement_id": f"{item['id']}.high.1",
                               "status": "supported" if q == 1 else "partial",
                               "frames": ["C001F0001"], "observation": "Visible hierarchy."}],
        "cap_checks": [{"cap_id": item["caps"][0]["id"], "triggered": capped,
                        "frames": ["C001F0001"],
                        "reason": "The major condition is present." if capped else "The remaining gap is limited."}],
        "applied_caps": [item["caps"][0]["id"]] if capped else [],
        "missing_evidence": "", "evidence_limitations": [],
        "score_rationale": "Grouping succeeds but emphasis remains incomplete.",
    }


def reading(q=.8):
    rubric = {"rubric_version": "test", "requirements": [criterion(g) for g in GROUP_WEIGHTS]}
    rows = {item["id"]: validate_judgment(judgment(item, q), item, {"C001F0001"})
            for item in rubric["requirements"]}
    return aggregate_game_visual(rubric, rows)


@pytest.mark.parametrize("score", [0.0, .2, .5, .8, 1.0])
def test_direct_credit_preserves_rubric_score(score):
    assert final_credit(score, [], []) == score


def test_direct_credit_applies_only_triggered_deficiency_caps():
    item = criterion()
    row = validate_judgment(judgment(item, .9, capped=True), item, {"C001F0001"})
    assert row["attainment"] == .9
    assert row["credit"] == .5
    assert "variants" not in row
    assert final_credit(.4, [item["caps"][0]["id"]], item["caps"]) == .4
    assert final_credit(None, [], item["caps"]) is None


@pytest.mark.parametrize(("q", "capped"), [(.8, False), (.9, True), (1.0, False)])
def test_v2_clause_ids_preserve_v1_scoring_exactly(q, capped):
    item = criterion()
    v1 = validate_judgment(judgment(item, q, capped=capped), item, {"C001F0001"})
    rows, errors = validate_response_items(
        {"schema_version": RESPONSE_SCHEMA_VERSION,
         "items": [v2_judgment(item, q, capped=capped)]},
        [item], {"C001F0001"}, "A",
    )
    assert errors == {}
    assert rows[item["id"]]["attainment"] == v1["attainment"]
    assert rows[item["id"]]["credit"] == v1["credit"]
    assert rows[item["id"]]["applied_caps"] == v1["applied_caps"]
    assert rows[item["id"]]["validation_shadow"]["score_equivalent"] is True


def test_v2_machine_schema_enumerates_only_frozen_ids():
    item = criterion()
    item["description"] += " With no HUD this item is not_applicable."
    requirement = build_response_contract({"requirements": [item]})["requirements"][0]
    schema = build_response_json_schema([requirement])
    properties = schema["properties"]["items"]["items"]["oneOf"][0]["properties"]
    assert properties["item_id"] == {"const": "A1"}
    assert properties["applicability"]["properties"]["condition_id"]["enum"] == [
        None, "A1.description",
    ]
    assert properties["deficiency_checks"]["items"]["properties"]["clause_id"]["enum"] == [
        "A1.full_credit", "A1.partial_credit", "A1.high.1",
    ]
    assert properties["cap_checks"]["items"]["properties"]["cap_id"]["enum"] == ["A1-C1"]


def test_v2_unknown_clause_id_cannot_create_a_deduction():
    item = criterion()
    row = v2_judgment(item)
    row["deficiency_checks"][0]["clause_id"] = "A1.description"
    normalized, errors = validate_response_items(
        {"schema_version": RESPONSE_SCHEMA_VERSION, "items": [row]},
        [item], {"C001F0001"}, "A",
    )
    assert normalized == {}
    assert errors == {"A1": "Unknown deficiency clause_id"}


def test_v2_explicit_outcome_must_match_validated_fields():
    item = criterion()
    row = v2_judgment(item)
    row["outcome"] = "unknown"
    normalized, errors = validate_response_items(
        {"schema_version": RESPONSE_SCHEMA_VERSION, "items": [row]},
        [item], {"C001F0001"}, "A",
    )
    assert normalized == {}
    assert "explicit measured/not_applicable outcome" in errors["A1"]


def test_tiny_rts_resource_loop_gets_partial_credit_without_filmed_supply_limit():
    item = next(r for r in json.loads((CORPUS / "tiny_rts/rubric.json").read_text())["requirements"]
                if r["id"] == "M2")
    row = v2_judgment(item, .75)
    row.update(strengths=["Gathering increases resources; spending creates a new unit nearby."],
               deficiencies=[], deficiency_checks=[],
               missing_evidence="The adopted supply limit is never reached in the recording.",
               score_rationale="The collection and spending loop is demonstrated; supply-limit blocking is unshown.")
    row["high_score_checks"] = [
        {"requirement_id": f"M2.high.{i}", "status": "supported" if i < 3 else "unknown",
         "frames": ["C001F0001"] if i < 3 else [],
         "observation": "Visible resource and unit consequence." if i < 3 else "No supply-limit event shown."}
        for i in (1, 2, 3)
    ]
    rows, errors = validate_response_items(
        {"schema_version": RESPONSE_SCHEMA_VERSION, "items": [row]},
        [item], {"C001F0001"}, "M",
    )
    assert errors == {}
    assert rows["M2"]["credit"] == .75
    assert rows["M2"]["status"] == "measured"
    assert rows["M2"]["deficiencies"] == []
    row["attainment"] = 1
    _, errors = validate_response_items(
        {"schema_version": RESPONSE_SCHEMA_VERSION, "items": [row]},
        [item], {"C001F0001"}, "M",
    )
    assert "Full attainment lacks full-credit evidence" in errors["M2"]


def test_entirely_unshown_achievement_is_zero_and_stays_in_group_average():
    item = criterion("V")
    row = v2_judgment(item, 0)
    row.update(evidence_frames=[], strengths=[], deficiencies=[], deficiency_checks=[],
               missing_evidence="No frame shows the required interface state.",
               score_rationale="None of the item's required achievement was demonstrated.")
    row["high_score_checks"][0].update(status="unknown", frames=[], observation="Required state not shown.")
    row["cap_checks"][0].update(frames=[], reason="Unshown state does not establish a hierarchy failure.")
    response = {"schema_version": RESPONSE_SCHEMA_VERSION, "items": [row]}
    rows, errors = validate_response_items(response, [item], {"C001F0001"}, "V")
    assert errors == {}
    assert rows["V1"]["credit"] == 0
    rubric = {"requirements": [criterion(g) for g in GROUP_WEIGHTS]}
    other = {i["id"]: validate_judgment(judgment(i, 1), i, {"C001F0001"})
             for i in rubric["requirements"] if i["group"] != "V"}
    result = aggregate_game_visual(rubric, {**other, **rows})
    assert result.verdict is Verdict.PASSED
    assert result.credit == pytest.approx(.73)
    row["attainment"] = None
    _, errors = validate_response_items(response, [item], {"C001F0001"}, "V")
    assert "Applicable items need numeric attainment" in errors["V1"]
    row.update(attainment=.1, evidence_frames=["C001F0001"])
    _, errors = validate_response_items(response, [item], {"C001F0001"}, "V")
    assert "Positive attainment needs demonstrated strengths" in errors["V1"]


def test_canopy_completion_can_be_partial_when_arrival_is_unshown():
    item = next(r for r in json.loads((CORPUS / "canopy_dash/rubric.json").read_text())["requirements"]
                if r["id"] == "M5")
    row = v2_judgment(item, .18)
    row.update(strengths=["The success screen and R-key replay entry are visible."],
               deficiencies=[], deficiency_checks=[],
               missing_evidence="The endpoint arrival required by full_credit and its subsequent two seconds are unshown.",
               score_rationale="The completion display earns credit, but the required arrival-to-result flow is unshown.")
    row["high_score_checks"] = [
        {"requirement_id": f"M5.high.{i}", "status": "supported" if i < 3 else "not_applicable",
         "frames": ["C001F0001"],
         "observation": "Success and replay entry are visible." if i < 3 else "The screen adopts no result statistics."}
        for i in (1, 2, 3)
    ]
    rows, errors = validate_response_items(
        {"schema_version": RESPONSE_SCHEMA_VERSION, "items": [row]},
        [item], {"C001F0001"}, "M",
    )
    assert errors == {}
    assert rows["M5"]["credit"] == .18


def test_v2_cannot_select_a_non_registered_na_clause():
    item = criterion()
    row = v2_judgment(item)
    row.update(outcome="not_applicable", attainment=None, strengths=[], deficiencies=[],
               deficiency_checks=[], score_rationale="")
    row["applicability"] = {
        "status": "not_applicable", "condition_id": "A1.full_credit",
        "observation": "The candidate has no HUD.",
    }
    row["high_score_checks"][0]["status"] = "not_applicable"
    normalized, errors = validate_response_items(
        {"schema_version": RESPONSE_SCHEMA_VERSION, "items": [row]},
        [item], {"C001F0001"}, "A",
    )
    assert normalized == {}
    assert errors == {"A1": "Unknown applicability condition_id"}


def test_v2_still_rejects_missing_candidate_frame_evidence():
    item = criterion()
    row = v2_judgment(item)
    row["evidence_frames"] = []
    normalized, errors = validate_response_items(
        {"schema_version": RESPONSE_SCHEMA_VERSION, "items": [row]},
        [item], {"C001F0001"}, "A",
    )
    assert normalized == {}
    assert errors == {"A1": "Measured attainment needs evidence and a rationale"}


def test_v3_counter_delta_alone_cannot_receive_full_credit():
    item = event_criterion()
    row = judgment(item, 1)
    row["strengths"] = ["The persistent pickup counter changes across the run."]
    row["score_rationale"] = "The counter rises, but no pickup event frame is present."
    with pytest.raises(ValueError, match="counter delta alone is insufficient"):
        validate_judgment(row, item, {"C001F0001"})


def test_v3_full_credit_accepts_auditable_event_feedback_linkage():
    item = event_criterion()
    row = judgment(item, 1)
    row["event_witnesses"] = [{
        "event_type": "pickup",
        "event_frames": ["C001F0001"],
        "feedback_frames": ["C001F0002"],
        "linkage": "The visible pickup disappears as the counter increments in the next sampled frame.",
    }]
    validated = validate_judgment(row, item, {"C001F0001", "C001F0002"})
    assert validated["credit"] == 1


def test_v3_partial_numeric_score_remains_valid_without_event_witness():
    item = event_criterion()
    row = judgment(item, .25)
    validated = validate_judgment(row, item, {"C001F0001"})
    assert validated["status"] == "measured"
    assert validated["credit"] == .25


def test_v2_not_applicable_id_preserves_v1_policy_decision():
    item = criterion()
    item["description"] = (
        "If the candidate adopts no HUD, assess world-space information instead; "
        "the HUD-specific item is not_applicable."
    )
    v1 = {
        "id": "A1", "applicability": "not_applicable", "attainment": None,
        "applicability_quote": item["description"],
        "applicability_reason": "The candidate uses only world-space information.",
        "evidence_frames": ["C001F0001"], "strengths": [], "deficiencies": [],
        "deduction_checks": [],
        "high_score_evidence": [{"requirement_index": 1, "status": "not_applicable",
                                 "frames": [], "observation": "The whole item is conditional.",
                                 "applicability_quote": item["high_score_requirements"][0]}],
        "cap_checks": [{"id": "A1-C1", "triggered": False, "frames": [],
                        "reason": "No applicable hierarchy failure."}],
        "applied_caps": [], "missing_evidence": "", "score_rationale": "",
    }
    expected = validate_judgment(v1, item, {"C001F0001"})
    v2 = {
        "item_id": "A1", "outcome": "not_applicable",
        "applicability": {"status": "not_applicable", "condition_id": "A1.description",
                          "observation": "The candidate uses only world-space information."},
        "attainment": None, "evidence_frames": ["C001F0001"],
        "strengths": [], "deficiencies": [], "deficiency_checks": [],
        "high_score_checks": [{"requirement_id": "A1.high.1", "status": "not_applicable",
                               "frames": [], "observation": "The whole item is conditional."}],
        "cap_checks": [{"cap_id": "A1-C1", "triggered": False, "frames": [],
                        "reason": "No applicable hierarchy failure."}],
        "applied_caps": [], "missing_evidence": "", "score_rationale": "",
    }
    rows, errors = validate_response_items(
        {"schema_version": RESPONSE_SCHEMA_VERSION, "items": [v2]},
        [item], {"C001F0001"}, "A",
    )
    assert errors == {}
    assert rows["A1"]["status"] == expected["status"] == "not_applicable"
    assert rows["A1"]["credit"] is expected["credit"] is None


@pytest.mark.parametrize("bad", [True, -1, 1.1, float("nan"), float("inf"), "0.8"])
def test_invalid_provider_numbers_do_not_become_measurements(bad):
    with pytest.raises(ValueError):
        final_credit(bad, [], [])


def test_caps_and_source_scope_need_real_candidate_evidence():
    item = criterion()
    row = judgment(item, capped=True)
    row["cap_checks"][0]["triggered"] = False
    with pytest.raises(ValueError, match="disagree"):
        validate_judgment(row, item, {"C001F0001"})
    row = judgment(item)
    row["evidence_frames"] = ["R001"]
    with pytest.raises(ValueError, match="candidate"):
        validate_judgment(row, item, {"C001F0001"})
    row = judgment(item)
    row["deduction_checks"][0]["field"] = "description"
    with pytest.raises(ValueError, match="GT description"):
        validate_judgment(row, item, {"C001F0001"})


def test_continuous_group_weights_and_unknown_item_preserve_denominators():
    rubric = {"requirements": [criterion(g) for g in GROUP_WEIGHTS]}
    rows = {i["id"]: validate_judgment(judgment(i, .8 if i["group"] == "M" else 1), i, {"C001F0001"})
            for i in rubric["requirements"]}
    item = aggregate_game_visual(rubric, rows)
    assert item.credit == pytest.approx(.9 + .1 * .8)
    assert item.evidence["scoring"]["method"] == "direct_continuous"
    assert "variants" not in item.evidence
    assert all("variants" not in group for group in item.evidence["groups"].values())
    del rows["V1"]
    missing = aggregate_game_visual(rubric, rows)
    assert missing.verdict is Verdict.INCONCLUSIVE
    assert missing.evidence["unmeasured"] == ["V1"]
    assert missing.evidence["groups"]["V"]["credit"] is None


def test_retained_readings_keep_direct_credit_without_obsolete_diagnostics():
    rubric = {"requirements": [criterion("A")]}
    row = validate_judgment(judgment(rubric["requirements"][0], .8), rubric["requirements"][0], {"C001F0001"})
    row["variants"] = {"direct": .8, "squared": .64, "cubic": .512}
    result = aggregate_game_visual(rubric, {"A1": row}, evidence={
        "score_curve": {"exponent": 1}, "variants": row["variants"],
    })
    assert result.credit == .8
    assert result.evidence["criteria"]["A1"]["credit"] == .8
    assert "variants" not in result.evidence["criteria"]["A1"]
    assert "variants" not in result.evidence and "score_curve" not in result.evidence
    assert "variants" in row  # Retained input evidence is not rewritten.
    assert "direct rubric judgment" in "\n".join(visual_report(result))


def test_not_applicable_needs_an_actual_conditional_clause_and_is_not_unknown():
    item = criterion()
    item["description"] = "If the candidate adopts a timer, assess its visual hierarchy."
    row = judgment(item)
    row.update(applicability="not_applicable", attainment=None,
               applicability_quote="If the candidate adopts a timer", applicability_reason="No timer was adopted.",
               deficiencies=[], deduction_checks=[], score_rationale="")
    row["high_score_evidence"][0]["status"] = "not_applicable"
    assert validate_judgment(row, item, {"C001F0001"})["status"] == "not_applicable"
    row["applicability_quote"] = "A timer is always optional."
    with pytest.raises(ValueError, match="conditional"):
        validate_judgment(row, item, {"C001F0001"})


def test_actual_visual_score_replaces_five_points_and_old_registry_stays_identical():
    result = _complete_result()
    old = score_task_result(result, MODE2_REDESIGN_REGISTRY_VERSION)
    result.items.append(reading(.8))
    new = score_task_result(result)
    assert new["registry_version"] == MODE2_VLM_REGISTRY_VERSION
    assert new["weighted_total"]["score"] == pytest.approx(round(old["weighted_total"]["score"] - 5 + 15 * .8, 3))
    assert new["visual_total"]["score"] == pytest.approx(15 * .8)
    assert new["visual_total"]["credit"] == pytest.approx(.8)
    assert new["weighted_total"]["headline_ceiling"] == 100
    assert new["strict"] == old["strict"]
    assert new["ranking_eligible"]
    assert not any(row["id"] == "visual_placeholder" for row in new["axes"])
    assert old == score_task_result(result, MODE2_REDESIGN_REGISTRY_VERSION)


@pytest.mark.parametrize("item", [None, inconclusive("task_visual", detail="provider unavailable"),
                                passed("task_visual", credit=1, evidence={"rubric_version": "generic legacy"})])
def test_missing_or_legacy_visual_withholds_composite_and_preserves_objective(item):
    result = _complete_result()
    old = score_task_result(result, MODE2_REDESIGN_REGISTRY_VERSION)
    if item:
        result.items.append(item)
    new = score_task_result(result)
    assert new["weighted_total"]["score"] is None
    assert new["visual_total"]["score"] is None
    assert new["objective_total"]["score"] == old["weighted_total"]["score"] - 5
    assert new["assessment_status"] == "objective_only"
    assert not new["ranking_eligible"]
    axis = next(row for row in new["axes"] if row["id"] == "task_visual")
    assert axis["credit"] is None and axis["earned_points"] is None


@pytest.mark.parametrize("mode,registry", [("brief", MODE1_REDESIGN_REGISTRY_VERSION),
                                          ("gdd", MODE2_REDESIGN_REGISTRY_VERSION),
                                          ("skeleton", MODE3_REDESIGN_REGISTRY_VERSION)])
def test_new_visual_registries_preserve_each_modes_objective_axes(mode, registry):
    from test_mode1_redesign_scorecard import _result as brief_result
    from test_taskgen_mode34b import _fixture

    result = brief_result(qwen_like=False) if mode == "brief" else (
        _complete_result() if mode == "gdd" else _fixture("skeleton_noop_canopy_dash"))
    old = score_task_result(result, registry)
    result.items.append(reading(.7))
    new = score_task_result(result)
    assert new["visual_total"]["score"] == pytest.approx(15 * .7)
    assert [row for row in new["axes"] if row["id"] != "task_visual"] == [
        row for row in old["axes"] if row["id"] != "visual_placeholder"]
    assert new["strict"] == old["strict"]


def test_visual_zero_is_measured_and_missing_objective_still_blocks_ranking():
    result = _complete_result()
    result.items.append(reading(0))
    zero = score_task_result(result)
    assert zero["ranking_eligible"] and zero["visual_total"]["score"] == 0
    result.mode1_context["route_readings"].pop()
    gap = score_task_result(result)
    assert gap["weighted_total"]["score"] is None
    assert gap["visual_total"]["score"] == 0
    assert not gap["ranking_eligible"]


def test_mode_specific_visual_registry_rejects_wrong_mode_before_judging():
    from evalsys.taskgen.scorecard import registry_policy
    with pytest.raises(ValueError, match="only for mode gdd"):
        registry_policy(MODE2_VLM_REGISTRY_VERSION, mode="bugfix")


def test_frozen_corpus_contains_all_games_and_resolvable_reference_ids():
    paths = list(CORPUS.glob("*/rubric.json"))
    assert len(paths) == 41
    count = 0
    for path in paths:
        rubric = json.loads(path.read_text())
        policy = rubric["scoring_policy"]
        assert policy["method"] == "direct_continuous"
        assert policy["version"] == rubric["rubric_version"]
        assert policy["range"] == [0, 1]
        assert policy["version"] == "2026-10-08.demonstrated-quality-v4"
        assert [row["credit_range"] for row in policy["direct_score_references"]] == [
            [0.01, 0.05], [0.05, 0.20], [0.20, 0.40], [0.45, 0.65], [0.70, 0.90],
        ]
        assert set(policy["group_guidance"]) == set(GROUP_WEIGHTS)
        assert "score_curve" not in rubric
        source = json.loads(path.with_name("source.json").read_text())
        ids = {row["id"] for row in source["reference_video"]["frames"] + source["provided_assets"]["frames"]}
        for item in rubric["requirements"]:
            count += 1
            assert item["group"] in GROUP_WEIGHTS
            assert item["high_score_requirements"]
            assert all(0 <= cap["max_credit"] <= .15 for cap in item["caps"])
            for basis in item["basis"]:
                assert set(basis.get("frame_ids", [])) <= ids
        for frame in source["provided_assets"]["frames"]:
            if frame.get("bundled_path"):
                assert (path.parent / frame["bundled_path"]).is_file()
    assert count == 658


def test_rubric_is_frozen_only_under_evaluator_hidden_directory(tmp_path):
    assert freeze_visual_rubric(tmp_path / "hidden", "shadow_walker")
    assert (tmp_path / "hidden/vlm/rubric.json").is_file()
    contract = json.loads((tmp_path / "hidden/vlm/response_contract.json").read_text())
    assert contract["schema_version"] == RESPONSE_SCHEMA_VERSION
    assert contract["requirements"][0]["response_ids"]["deduction_clauses"]
    rubric = json.loads((tmp_path / "hidden/vlm/rubric.json").read_text())
    assert contract["scoring_policy"] == rubric["scoring_policy"]
    assert not (tmp_path / "visible").exists()
    pkg = SimpleNamespace(manifest={"mode": "brief", "reference_video": "off"})
    with pytest.raises(ValueError, match="ablation"):
        prepare_visual_manifest(pkg, {}, tmp_path / "evidence")


@pytest.mark.parametrize("mode", ["brief", "port"])
def test_frozen_quality_policy_reaches_the_judge_and_report(mode, tmp_path):
    rubric = json.loads((CORPUS / "shadow_walker/rubric.json").read_text())
    policy = rubric["scoring_policy"]
    item = criterion()
    rubric["requirements"] = [item]
    manifest = {"game_id": "shadow_walker", "mode": mode,
                "game_rubric": rubric, "response_contract": build_response_contract(rubric),
                "demonstrations": [{"id": "one"}]}

    class Judge:
        def score(self, prompt, images, folder):
            request = json.loads("{\n" + prompt.rsplit("\n\n{\n", 1)[1])
            assert request["scoring_policy"] == policy
            assert request["requirements"][0]["full_credit"] == item["full_credit"]
            return {"items": [judgment(item, .8)]}, {"requested_model": "test"}

    with patch("evalsys.taskgen.visual_materials.prepare_candidate_frames", return_value=[{"id": "C001F0001"}]), \
         patch("evalsys.taskgen.visual_materials.evidence_images", return_value=({}, [])):
        result = judge_game_visual(manifest, tmp_path, judge=Judge())
    assert result.verdict is Verdict.PASSED
    assert result.credit == .8
    assert result.evidence["scoring"]["policy_version"] == policy["version"]
    assert "variants" not in result.evidence


def test_judge_sees_all_segments_and_isolates_provider_failure_per_group(tmp_path):
    item = criterion()
    manifest = {"game_id": "fixture", "mode": "brief", "game_rubric": {"requirements": [item, criterion("M")]},
                "demonstrations": [{"id": "one"}, {"id": "two"}]}
    candidates = [{"id": "C001F0001", "clip": 1}, {"id": "C002F0001", "clip": 2}]
    calls = []

    class Judge:
        def score(self, prompt, images, folder):
            calls.append(prompt)
            assert "reference" in prompt.lower()
            assert "C001F0001" in prompt and "C002F0001" in prompt
            raise RuntimeError("provider unavailable")

    with patch("evalsys.taskgen.visual_materials.prepare_candidate_frames", return_value=candidates), \
         patch("evalsys.taskgen.visual_materials.evidence_images", return_value=({"candidate_frames": candidates}, [])):
        result = judge_game_visual(manifest, tmp_path, judge=Judge())
    assert len(calls) == 2
    assert result.verdict is Verdict.INCONCLUSIVE
    assert "provider unavailable" in result.detail
    assert set(result.evidence["unmeasured"]) == {"A1", "M1"}
    assert {group: row["status"] for group, row in result.evidence["group_results"].items()} == {
        "A": "inconclusive", "M": "inconclusive",
    }


def test_reference_frame_extraction_clamps_timestamp_to_last_decodable_frame(tmp_path):
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg unavailable")
    movie = tmp_path / "reference.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=32x32:rate=5:duration=1", "-c:v", "libx264", str(movie),
    ], check=True)
    target = tmp_path / "frame.jpg"
    actual = extract_frame(movie, 1.0, target)
    assert target.is_file()
    assert 0 <= actual < 1.0


def test_judge_permits_one_contract_repair_without_inventing_scores(tmp_path):
    item = criterion()
    policy = {"version": "test-direct-policy", "method": "direct_continuous"}
    manifest = {"game_id": "fixture", "mode": "brief", "game_rubric": {"requirements": [item], "scoring_policy": policy},
                "demonstrations": [{"id": "one"}]}
    calls = []

    class Judge:
        def score(self, prompt, images, folder):
            calls.append(prompt)
            assert '"scoring_policy": ' + json.dumps(policy, indent=2).replace("\n", "\n  ") in prompt
            row = judgment(item, .8)
            if len(calls) == 1:
                row["deficiencies"] = []
                row["deduction_checks"] = []
            else:
                assert "Sub-full attainment needs an observed deficiency or a specific unshown requirement" in prompt
                assert "Do not invent a defect" in prompt
            return {"items": [row]}, {"requested_model": "test"}

    with patch("evalsys.taskgen.visual_materials.prepare_candidate_frames", return_value=[{"id": "C001F0001"}]), \
         patch("evalsys.taskgen.visual_materials.evidence_images", return_value=({}, [])):
        result = judge_game_visual(manifest, tmp_path, judge=Judge())
    assert len(calls) == 2
    assert result.verdict is Verdict.PASSED
    assert result.credit == pytest.approx(.8)


def test_judge_retries_one_transient_transport_failure(tmp_path):
    item = criterion()
    manifest = {"game_id": "fixture", "mode": "brief",
                "game_rubric": {"requirements": [item]},
                "demonstrations": [{"id": "one"}]}
    calls = []

    class Judge:
        def score(self, _prompt, _images, folder):
            calls.append(Path(folder))
            if len(calls) == 1:
                raise RuntimeError("upstream service returned an invalid gateway response")
            return {"items": [judgment(item, .8)]}, {"requested_model": "test"}

    with patch("evalsys.taskgen.visual_materials.prepare_candidate_frames",
               return_value=[{"id": "C001F0001"}]), \
         patch("evalsys.taskgen.visual_materials.evidence_images", return_value=({}, [])):
        result = judge_game_visual(manifest, tmp_path, judge=Judge())
    assert result.verdict is Verdict.PASSED
    assert result.credit == pytest.approx(.8)
    assert len(calls) == 2
    assert calls[1].name == "transport-retry"
    assert result.evidence["requests"][0]["transport_retries"] == 1


def test_judge_resumes_complete_groups_from_normalized_checkpoint(tmp_path):
    item = criterion()
    manifest = {"game_id": "fixture", "mode": "brief", "game_rubric": {"requirements": [item]},
                "demonstrations": [{"id": "one"}]}
    retained = validate_judgment(judgment(item, .8), item, {"C001F0001"})
    retained.update(title=item["title"], group="A", owner="candidate", failure_code=None)
    folder = tmp_path / "A"
    folder.mkdir(parents=True)
    (folder / "normalized.json").write_text(json.dumps({item["id"]: retained}))

    class Judge:
        def score(self, *_args):
            raise AssertionError("a complete group checkpoint must not call the provider")

    with patch("evalsys.taskgen.visual_materials.prepare_candidate_frames",
               return_value=[{"id": "C001F0001"}]):
        result = judge_game_visual(manifest, tmp_path, judge=Judge())
    assert result.verdict is Verdict.PASSED
    assert result.credit == pytest.approx(.8)
    assert result.evidence["group_results"]["A"]["resumed_from_checkpoint"] is True


def test_judge_resumes_valid_items_and_requests_only_pending_item(tmp_path):
    first = criterion()
    second = {**criterion(), "id": "A2", "title": "Second criterion"}
    manifest = {"game_id": "fixture", "mode": "brief",
                "game_rubric": {"requirements": [first, second]},
                "demonstrations": [{"id": "one"}]}
    retained = validate_judgment(judgment(first, .8), first, {"C001F0001"})
    retained.update(title=first["title"], group="A", owner="candidate", failure_code=None)
    folder = tmp_path / "A"
    folder.mkdir(parents=True)
    (folder / "normalized.json").write_text(json.dumps({first["id"]: retained}))

    class Judge:
        def score(self, prompt, _images, _folder):
            assert '"id": "A2"' in prompt and '"id": "A1"' not in prompt
            return {"items": [judgment(second, .6)]}, {"requested_model": "test"}

    with patch("evalsys.taskgen.visual_materials.prepare_candidate_frames",
               return_value=[{"id": "C001F0001"}]), \
         patch("evalsys.taskgen.visual_materials.evidence_images", return_value=({}, [])):
        result = judge_game_visual(manifest, tmp_path, judge=Judge())
    assert result.verdict is Verdict.PASSED
    assert result.credit == pytest.approx(.7)
    assert result.evidence["group_results"]["A"]["resumed_item_ids"] == ["A1"]


def test_missing_candidate_ops_is_a_complete_visual_zero_not_evaluator_inconclusive(tmp_path):
    item = criterion()
    manifest = {
        "game_id": "fixture", "mode": "brief", "game_rubric": {"requirements": [item]},
        "demonstrations": [{"id": "whole-run", "film": {
            "ok": False, "mp4": "", "error": "the submission has no ops tape to film",
        }}],
    }
    result = judge_game_visual(manifest, tmp_path, judge=object())
    assert result.verdict is Verdict.PASSED
    assert result.credit == 0
    assert result.evidence["recording_health"]["owner"] == "candidate"
    assert result.evidence["recording_health"]["failure_code"] == "candidate_ops_missing"
    assert result.evidence["groups"]["A"]["status"] == "complete"


def test_real_sampler_includes_both_ends_of_long_recording(tmp_path):
    import shutil
    import subprocess
    from evalsys.taskgen.visual_materials import prepare_candidate_frames

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg unavailable")
    movie = tmp_path / "recording.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=64x64:rate=5:duration=65", "-c:v", "libx264", str(movie)], check=True)
    frames = prepare_candidate_frames([{"id": "whole-run", "film": {"mp4": str(movie)}}], tmp_path / "sample")
    assert frames[0]["time_s"] == 0
    assert frames[-1]["time_s"] > 64
    assert 100 < len([f for f in frames if f["sampling_kind"] != "dense_motion"]) <= 120
    assert len([f for f in frames if f["sampling_kind"] == "dense_motion"]) <= 180
    assert {f.get("excerpt") for f in frames if f["sampling_kind"] == "dense_motion"} == {1, 2, 3}
    assert all(Path(f["path"]).is_file() for f in frames)


def test_default_evaluation_actually_invokes_the_new_vlm_producer(tmp_path):
    from evalsys.taskgen.evaluate import evaluate_task
    from evalsys.taskgen.scorecard import MODE1_VLM_REGISTRY_VERSION
    from evalsys.taskgen.package import TaskPackage
    from _interface_fixture import write_conformant_project

    root = tmp_path / "package"
    (root / "visible").mkdir(parents=True)
    (root / "hidden").mkdir()
    (root / "visible/PROMPT.md").write_text("Build the specified fixture.")
    (root / "hidden/oracle.json").write_text("{}")
    package = TaskPackage(root, {"schema_version": 1, "mode": "brief", "game_id": "fixture"})
    package.write_manifest()
    sub = write_conformant_project(tmp_path / "sub")
    (sub / "GDD.md").write_text("# Fixture game\nMove the player to the goal; reset after failure.")
    (sub / "ops.json").write_text(json.dumps({"ops": [{"op": "hold", "action": "gb_right", "frames": 30}]}))
    manifest = {"demonstrations": [{"id": "whole-run"}]}
    with patch("evalsys.taskgen.evaluate._run_engine", return_value={"ran": True}), \
         patch("evalsys.taskgen.evaluate.godot_available", return_value=Path("/fake")), \
         patch("evalsys.taskgen.demonstrations.capture_task_visuals", return_value=manifest) as capture, \
         patch("evalsys.taskgen.visual_materials.prepare_visual_manifest", return_value=manifest) as prepare, \
         patch("evalsys.scard.game_visual.judge_game_visual", return_value=reading(.8)) as judge:
        result = evaluate_task(package.root, sub, engine="on", visual_judge="vlm",
                               score_against_source=False, out=tmp_path / "result")
    assert result.registry_version == MODE1_VLM_REGISTRY_VERSION
    assert capture.call_count == prepare.call_count == judge.call_count == 1
    assert next(i for i in result.items if i.id == "task_visual").evidence["protocol"] == PROTOCOL


def test_saved_game_rubric_rescore_retains_objective_artifact_locations(tmp_path):
    from evalsys.taskgen.visual_rescore import rescore_visuals
    from test_visual_rescore import saved_report

    source, film = saved_report(tmp_path)
    data = json.loads(source.read_text())
    data.update(package=str(tmp_path / "original-package"), witness_protocol="gamebench.feature-demos.v1")
    source.write_text(json.dumps(data))
    inputs = tmp_path / "demonstrations/visual_inputs.json"
    manifest = json.loads(inputs.read_text())
    manifest.update(game_rubric={"requirements": []}, game_id="fixture", mode="gdd")
    inputs.write_text(json.dumps(manifest))
    original = source.read_bytes()
    captured = []

    def score(result, registry):
        captured.append(result)
        return {"objective_total": {}, "weighted_total": {"score": None}, "assessment_status": "evaluation_incomplete"}

    with patch("evalsys.scard.game_visual.judge_game_visual", return_value=reading(.8)), \
         patch("evalsys.taskgen.visual_rescore.score_task_result", side_effect=score), \
         patch("evalsys.taskgen.visual_rescore.capture_task_visuals", side_effect=AssertionError("No engine rerun")):
        rescore_visuals(source, tmp_path / "new")
    assert source.read_bytes() == original
    assert captured[0].report_path == source
    assert captured[0].package.root == tmp_path / "original-package"
    assert captured[0].stored_report["witness_protocol"] == "gamebench.feature-demos.v1"


@pytest.mark.parametrize("explicit_registry", [False, True])
def test_new_registry_visual_rescore_prepares_gt_when_saved_manifest_is_legacy(tmp_path, explicit_registry):
    from evalsys.taskgen.visual_rescore import rescore_visuals
    from evalsys.taskgen.scorecard import MODE2_VLM_REGISTRY_VERSION
    from test_visual_rescore import saved_report

    source, _film = saved_report(tmp_path)
    data = json.loads(source.read_text())
    data.update(package=str(tmp_path / "package"))
    if not explicit_registry:
        data["scorecard"] = {"registry_version": MODE2_VLM_REGISTRY_VERSION}
    source.write_text(json.dumps(data))
    original = source.read_bytes()
    prepared = json.loads((tmp_path / "demonstrations/visual_inputs.json").read_text())
    prepared.update(game_rubric={"requirements": []}, game_id="fixture", mode="gdd")
    with patch("evalsys.taskgen.visual_rescore.TaskPackage.read"), \
         patch("evalsys.taskgen.visual_materials.prepare_visual_manifest", return_value=prepared) as prepare, \
         patch("evalsys.scard.game_visual.judge_game_visual", return_value=reading(.8)) as judge, \
         patch("evalsys.taskgen.visual_rescore.judge_saved_visuals", side_effect=AssertionError("legacy judge must not run")), \
         patch("evalsys.taskgen.visual_rescore.score_task_result", return_value={
             "objective_total": {}, "weighted_total": {"score": None},
             "assessment_status": "evaluation_incomplete",
         }) as score:
        result = rescore_visuals(source, tmp_path / "new", judge=object(), **(
            {"registry_version": MODE2_VLM_REGISTRY_VERSION} if explicit_registry else {}))
    assert prepare.call_count == judge.call_count == 1
    assert score.call_args.args[1] == MODE2_VLM_REGISTRY_VERSION
    assert result["visual_rescore"]["game_rubric"] is True
    assert source.read_bytes() == original


def test_responses_rubric_honors_the_configured_openai_endpoint(monkeypatch):
    from evalsys.scard.rubric_judge import OpenAIResponsesRubricJudge
    monkeypatch.delenv("GAMEBENCH_VLM_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    assert OpenAIResponsesRubricJudge().base_url == "https://example.invalid/v1"


@pytest.mark.skipif(__import__("os").name == "nt", reason="fixture is a POSIX executable script")
def test_claude_transport_preserves_real_model_and_rejects_zero_exit_api_error(tmp_path, monkeypatch):
    from evalsys.scard.rubric_judge import ClaudeCodeRubricJudge
    import sys

    cli = tmp_path / "fake-claude"
    cli.write_text("#!" + sys.executable + "\n" +
                   "import json,sys\nsys.stdin.read()\n" +
                   "print(json.dumps({'type':'assistant','message':{'model':'claude-fable-5-1','content':[]}}))\n" +
                   "print(json.dumps({'type':'result','is_error':True,'result':'quota exhausted'}))\n")
    cli.chmod(0o755)
    monkeypatch.setenv("GAMEBENCH_CLAUDE_CODE_BIN", str(cli))
    monkeypatch.setenv("GAMEBENCH_VLM_MODEL", "claude-fable-5-1")
    monkeypatch.setenv("GAMEBENCH_VLM_KEY_ENV", "TEST_RUBRIC_KEY")
    monkeypatch.setenv("TEST_RUBRIC_KEY", "test-only")
    with pytest.raises(RuntimeError, match="quota exhausted"):
        ClaudeCodeRubricJudge().score("judge", [], tmp_path / "call")
    saved = json.loads((tmp_path / "call/request.json").read_text(encoding="utf-8"))
    assert saved["returned_models"] == ["claude-fable-5-1"]
    assert saved["exit_code"] == 0
    assert (tmp_path / "call/result.json").is_file()


def test_mdva_responses_transport_honours_model_effort_and_json_contract(tmp_path, monkeypatch):
    from evalsys.scard.rubric_judge import OpenAIResponsesRubricJudge, rubric_judge_from_env

    image = tmp_path / "frame.png"
    image.write_bytes(__import__("base64").b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    ))
    calls = []

    def requester(payload, key, endpoint):
        calls.append((payload, key, endpoint))
        return {
            "model": "gpt-5.6-sol",
            "output": [{"type": "message", "content": [{
                "type": "output_text", "text": '{"items": []}',
            }]}],
        }

    monkeypatch.setenv("GAMEBENCH_VLM_PROVIDER", "responses")
    monkeypatch.setenv("GAMEBENCH_VLM_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("GAMEBENCH_VLM_EFFORT", "high")
    monkeypatch.setenv("GAMEBENCH_VLM_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("GAMEBENCH_VLM_KEY_ENV", "TEST_RESPONSES_KEY")
    monkeypatch.setenv("TEST_RESPONSES_KEY", "test-only")
    monkeypatch.setattr("evalsys.scard.judge.default_openai_responses_requester", requester)

    judge = rubric_judge_from_env()
    assert isinstance(judge, OpenAIResponsesRubricJudge)
    response, record = judge.score(
        "return JSON", [{"path": str(image), "label": "candidate"}], tmp_path / "responses",
    )

    assert response == {"items": []}
    payload, key, endpoint = calls[0]
    assert key == "test-only"
    assert endpoint == "https://example.invalid/v1/responses"
    assert payload["model"] == "gpt-5.6-sol"
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["text"] == {"format": {"type": "json_object"}}
    assert record["transport"] == "openai_responses"
    assert (tmp_path / "responses/request.json").is_file()
    assert (tmp_path / "responses/result.json").is_file()


@pytest.mark.parametrize("mode,video,case_id,uses_vlm", [
    ("brief", True, "", True), ("gdd", True, "", True),
    ("skeleton", True, "", True), ("port", True, "", True),
    ("brief", False, "", False), ("bugfix", True, "case-1", False),
])
def test_fixed_task_install_freezes_current_visual_policy(mode, video, case_id, uses_vlm, tmp_path):
    from evalsys.taskgen.generate import generate_task
    from evalsys.taskgen.package import write_json

    old_rubric = {"game_id": "shadow_walker", "rubric_version": "frozen-release"}
    def install(game_id, variant, dest):
        (dest / "visible").mkdir(parents=True)
        (dest / "hidden").mkdir()
        for name in ("HANDOFF.md", "PROMPT.md", "visible/PROMPT.md"):
            (dest / name).write_text("Fixed agent task\n")
        (dest / "hidden/objective.json").write_text("fixed objective checks\n")
        write_json(dest / "manifest.json", {"schema_version": 1, "game_id": game_id,
                                             "mode": mode, "reference_video": "on" if video else "off"})
        write_json(dest / "hidden/vlm/rubric.json", old_rubric)
        write_json(dest / "hidden/vlm/response_contract.json", {"rubric_version": "frozen-release"})

    with patch("evalsys.taskgen.generate.load_manifest", return_value={
        "games": {"shadow_walker": {"cases": ["case-1"]}},
    }), patch("evalsys.taskgen.generate.install_task", side_effect=install):
        pkg = generate_task("shadow_walker", mode=mode, out=tmp_path / "task",
                            reference_video=video, case_id=case_id)
    rubric = json.loads((pkg.hidden / "vlm/rubric.json").read_text())
    contract = json.loads((pkg.hidden / "vlm/response_contract.json").read_text())
    if uses_vlm:
        assert rubric["rubric_version"] == "2026-10-08.demonstrated-quality-v4"
        assert contract["scoring_policy"] == rubric["scoring_policy"]
        assert "score_curve" not in rubric
    else:
        assert rubric == old_rubric
        assert contract == {"rubric_version": "frozen-release"}
    assert (pkg.visible / "PROMPT.md").read_text() == "Fixed agent task\n"
    assert (pkg.hidden / "objective.json").read_text() == "fixed objective checks\n"
