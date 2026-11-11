from __future__ import annotations
from dataclasses import replace
from ..scorecard import _mode5_item_attribution, _mode5_evidence_attribution
from ...verdict import Attribution, Item, Verdict
def _attribute_mode5_items(items: list[Item]) -> list[Item]:


    attributed: list[Item] = []


    for item in _cascade_mode5_root_failure(items):
        if item.attribution is not None:
            attributed.append(item)
            continue
        raw = _mode5_item_attribution(item)
        if raw == Attribution.HARNESS.value:
            attributed.append(replace(item, attribution=Attribution.HARNESS))
        elif raw == Attribution.SUBMISSION.value:
            attributed.append(replace(item, attribution=Attribution.SUBMISSION))
        elif raw == Attribution.UNATTRIBUTABLE.value:
            attributed.append(replace(item, attribution=Attribution.UNATTRIBUTABLE))
        elif raw is not None:
            attributed.append(replace(item, attribution=Attribution(raw)))
        else:
            attributed.append(item)
    return attributed


def _cascade_mode5_root_failure(items: list[Item]) -> list[Item]:
    """Use the same dependency attribution for live runs and saved reports."""
    roots = {item.id: item for item in items}
    build = roots.get("unity_build")
    probe = roots.get("unity_probe")
    root = build if build and build.verdict in {Verdict.FAILED, Verdict.SKIPPED} and _mode5_item_attribution(build) == "submission" else None
    if root is None and probe and probe.verdict is Verdict.FAILED and _mode5_item_attribution(probe) == "submission":
        root = probe
    if root is None:
        return list(items)
    dependent = {"unity_probe", "unity_mechanic_trace", "unity_runtime_stability", "unity_auto_win_ready",
                 "unity_input_dispatch", "unity_hidden_behavior", "unity_source_behavior", "unity_counterfactual",
                 "legacy_reference_trace", "causal_witness", "null_no_win", "unity_evaluator_capture"}
    if root.id == "unity_probe":
        dependent = {"unity_mechanic_trace", "causal_witness"}
    result = []
    for item in items:
        owner = item.attribution.value if item.attribution else _mode5_evidence_attribution(item.evidence)
        if (item.id in dependent and item.verdict in {Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE}
                and owner not in {"harness", "unattributable"}
                and not any(word in item.detail.lower() for word in ("no runnable", "not requested", "disabled by evaluator"))):
            item = replace(item, verdict=Verdict.FAILED, credit=0.0, attribution=Attribution.SUBMISSION,
                           evidence={**item.evidence, "dependency_root": root.id})
        result.append(item)
    return result
