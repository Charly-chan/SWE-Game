
import pytest

from evalsys.taskgen.scorecard import (
    MODE2_VLM_REGISTRY_VERSION, MODE3_VLM_REGISTRY_VERSION, score_task_result,
)
from evalsys.verdict import failed, inconclusive, passed
from test_progressive_redesign_scorecard import _axis
from test_redesign_evidence_regressions import _complete_result
from test_taskgen_mode34b import _fixture


def result_for(mode, numeric_slots):
    result = _complete_result() if mode == 'gdd' else _fixture('skeleton_noop_canopy_dash')
    context = getattr(result, 'mode1_context', None) or {}
    context.setdefault('rubric', {})['required_numeric_slots'] = numeric_slots
    result.mode1_context = context
    names = ('ops_present', 'ops_valid', 'ops_not_idle')
    result.items = [item for item in result.items if item.id not in names]
    result.items.extend(passed(name) for name in names)
    return result


def score(result, mode):
    return score_task_result(result, MODE2_VLM_REGISTRY_VERSION if mode == 'gdd' else MODE3_VLM_REGISTRY_VERSION)


@pytest.mark.parametrize('mode', ['gdd', 'skeleton'])
@pytest.mark.parametrize('failed_item', ['ops_present', 'ops_valid', 'ops_not_idle'])
def test_candidate_input_failure_is_zero_not_a_missing_numeric_probe(mode, failed_item):
    result = result_for(mode, ['score'])
    result.items = [failed(failed_item, detail='candidate did not supply usable input')
                    if item.id == failed_item else item for item in result.items]
    card = score(result, mode)
    numeric = _axis(card, 'numeric_contract')
    assert numeric['credit'] == 0.0
    assert numeric['status'] == 'candidate_failure'
    assert not any(row.get('axis') == 'numeric_contract' for row in card['evaluator_failures'])
    assert any(row.get('axis') == 'numeric_contract' for row in card['candidate_failures'])


@pytest.mark.parametrize('mode', ['gdd', 'skeleton'])
def test_valid_inputs_with_missing_numeric_telemetry_remain_an_evaluator_gap(mode):
    card = score(result_for(mode, ['score']), mode)
    assert _axis(card, 'numeric_contract')['status'] == 'unverified_zero'
    assert any(row.get('axis') == 'numeric_contract' for row in card['evaluator_failures'])


@pytest.mark.parametrize('mode', ['gdd', 'skeleton'])
def test_inconclusive_input_probe_is_not_blame_assigned_to_candidate(mode):
    result = result_for(mode, ['score'])
    result.items = [inconclusive('ops_valid', detail='engine unavailable')
                    if item.id == 'ops_valid' else item for item in result.items]
    card = score(result, mode)
    assert _axis(card, 'numeric_contract')['status'] == 'unverified_zero'


@pytest.mark.parametrize('mode', ['gdd', 'skeleton'])
def test_no_numeric_requirement_stays_inapplicable_even_without_ops(mode):
    result = result_for(mode, [])
    result.items = [failed('ops_present') if item.id == 'ops_present' else item for item in result.items]
    card = score(result, mode)
    assert _axis(card, 'numeric_contract')['status'] == 'empty_denominator'
