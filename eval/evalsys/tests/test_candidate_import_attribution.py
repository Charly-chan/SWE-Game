
import pytest

from evalsys.scard.game_visual import PROTOCOL
from evalsys.taskgen.scorecard import score_task_result
from evalsys.verdict import inconclusive, passed
from test_mode1_redesign_scorecard import _result as brief_result
from test_progressive_redesign_scorecard import _axis
from test_redesign_evidence_regressions import _complete_result
from test_taskgen_mode34b import _fixture


def result_for(mode, cold_status='failed', measured=True):
    result = (brief_result(qwen_like=False) if mode == 'brief' else
              _complete_result() if mode == 'gdd' else _fixture('skeleton_noop_canopy_dash'))
    context = getattr(result, 'mode1_context', None) or {}
    context.pop('candidate_snapshot', None)
    context['candidate_truth'] = {}
    context['route_readings'] = []
    result.mode1_context = context
    reproduction = next(item for item in result.items if item.id == 'reproduction')
    channels = reproduction.evidence['card']['channels']
    channels[:] = [row for row in channels if row['channel'] not in {'O1', 'O6'}]
    channels.extend([
        {'channel': 'O1', 'measured': measured, 'lo': 0, 'hi': 0,
         'coverage': 1, 'denominator': 1, 'by_verdict': {cold_status: 1},
         'note': f'cold_import={cold_status}; boots=inconclusive'},
        {'channel': 'O6', 'measured': False, 'lo': 0, 'hi': 1,
         'coverage': 0, 'denominator': 0, 'by_verdict': {'unmeasurable': 1},
         'note': 'asset scratch import failed'},
    ])
    result.items = [item for item in result.items if item.id != 'task_visual']
    result.items.append(passed('task_visual', credit=0, evidence={'protocol': PROTOCOL}))
    return result


@pytest.mark.parametrize('mode', ['brief', 'gdd', 'skeleton'])
def test_confirmed_candidate_import_failure_keeps_runtime_weight(mode):
    card = score_task_result(result_for(mode))
    for name in ('asset_realization', 'task_checkpoints', 'hidden_scenarios'):
        axis = _axis(card, name)
        assert axis['credit'] == 0
        assert axis['status'] == 'candidate_failure'
        assert not any(row.get('axis') == name for row in card['evaluator_failures'])
        assert any(row.get('axis') == name and row['failure_code'] == 'candidate_cold_import_failed'
                   for row in card['candidate_failures'])


@pytest.mark.parametrize('mode', ['brief', 'gdd', 'skeleton'])
@pytest.mark.parametrize('cold_status,measured', [('passed', True), ('inconclusive', False), ('failed', False)])
def test_missing_probe_without_decided_candidate_import_failure_stays_incomplete(mode, cold_status, measured):
    card = score_task_result(result_for(mode, cold_status, measured))
    assert not card['ranking_eligible']
    assert _axis(card, 'asset_realization')['status'] == 'not_instrumented_zero'
    assert any(row.get('axis') == 'asset_realization' for row in card['evaluator_failures'])


def test_import_failure_does_not_hide_independent_static_or_visual_evaluator_gaps():
    result = result_for('brief')
    replaced = {'authored_gdd_quality', 'task_visual'}
    result.items = [inconclusive(item.id, detail='independent evaluator request failed')
                    if item.id in replaced else item for item in result.items]
    card = score_task_result(result)
    assert not card['ranking_eligible']
    assert _axis(card, 'gdd_quality')['status'] == 'not_instrumented_zero'
    assert _axis(card, 'task_visual')['credit'] is None


@pytest.mark.parametrize('mode', ['brief', 'gdd', 'skeleton'])
def test_missing_declared_levels_explains_asset_probe_gap_only(mode):
    result = result_for(mode, cold_status='passed')
    channels = next(item for item in result.items if item.id == 'reproduction').evidence['card']['channels']
    channels[:] = [row for row in channels if row['channel'] not in {'O2', 'O6'}]
    channels.extend([
        {'channel': 'O2', 'measured': True, 'lo': 0, 'hi': 0,
         'note': 'groups=failed; actions=failed; levels=failed; endings=failed'},
        {'channel': 'O6', 'measured': False, 'lo': 0, 'hi': 1,
         'note': 'no engine reading: normalized interface has no level address'},
    ])
    before = score_task_result(result_for(mode, cold_status='passed'))
    card = score_task_result(result)
    asset = _axis(card, 'asset_realization')
    assert asset['credit'] == 0
    assert asset['status'] == 'candidate_failure'
    assert asset['evidence']['failure_code'] == 'candidate_missing_level_manifest'
    for name in ('task_checkpoints', 'hidden_scenarios'):
        assert _axis(card, name)['status'] == _axis(before, name)['status']


def test_invalid_level_contract_does_not_explain_generic_asset_probe_failure():
    result = result_for('brief', cold_status='passed')
    channels = next(item for item in result.items if item.id == 'reproduction').evidence['card']['channels']
    channels[:] = [row for row in channels if row['channel'] != 'O2']
    channels.append({'channel': 'O2', 'measured': True, 'lo': 0, 'hi': 0, 'note': 'levels=failed'})
    card = score_task_result(result)
    assert _axis(card, 'asset_realization')['status'] == 'not_instrumented_zero'


def invalid_level_result(mode):

    result = result_for(mode, cold_status='passed')
    channels = next(item for item in result.items if item.id == 'reproduction').evidence['card']['channels']
    channels[:] = [row for row in channels if row['channel'] != 'O2']
    channels.append({'channel': 'O2', 'measured': True, 'lo': 0, 'hi': 0,
                     'note': 'groups=passed; actions=failed; levels=failed; endings=failed'})
    result.mode1_context['candidate_truth'] = {'levels': [{
        'declared_scene': "{'scene': 'res://canopy_dash.tscn', 'name': 'Level 1'}",
        'stop_reason': 'no_record', 'reached': False,
    }]}
    return result


@pytest.mark.parametrize('mode', ['brief', 'gdd', 'skeleton'])
def test_all_invalid_declared_addresses_are_candidate_runtime_failures(mode):
    card = score_task_result(invalid_level_result(mode))
    for name in ('asset_realization', 'task_checkpoints', 'hidden_scenarios'):
        axis = _axis(card, name)
        assert axis['credit'] == 0
        assert axis['status'] == 'candidate_failure'
        assert axis['evidence']['failure_code'] == 'candidate_invalid_level_manifest'
        assert not any(row.get('axis') == name for row in card['evaluator_failures'])


@pytest.mark.parametrize('other_level', [
    {'declared_scene': 'res://level.tscn', 'stop_reason': 'no_record', 'reached': False},
    {'declared_scene': '', 'stop_reason': 'no_record', 'reached': False},
    {'declared_scene': 'invalid', 'stop_reason': 'timeout', 'reached': False},
    {'declared_scene': 'invalid', 'stop_reason': '', 'reached': True},
])
def test_invalid_address_does_not_explain_another_missing_or_reached_level(other_level):
    result = invalid_level_result('gdd')
    result.mode1_context['candidate_truth']['levels'].append(other_level)
    card = score_task_result(result)
    assert _axis(card, 'asset_realization')['status'] == 'not_instrumented_zero'
    assert not card['ranking_eligible']


@pytest.mark.parametrize('measured,note', [(False, 'levels=failed'), (True, 'levels=passed')])
def test_invalid_address_requires_confirmed_interface_failure(measured, note):
    result = invalid_level_result('gdd')
    channels = next(item for item in result.items if item.id == 'reproduction').evidence['card']['channels']
    row = next(row for row in channels if row['channel'] == 'O2')
    row.update(measured=measured, note=note)
    card = score_task_result(result)
    assert _axis(card, 'asset_realization')['status'] == 'not_instrumented_zero'


def test_invalid_addresses_keep_independent_static_and_visual_gaps():
    result = invalid_level_result('brief')
    result.items = [inconclusive(item.id, detail='independent evaluator request failed')
                    if item.id in {'authored_gdd_quality', 'task_visual'} else item
                    for item in result.items]
    card = score_task_result(result)
    assert _axis(card, 'asset_realization')['status'] == 'candidate_failure'
    assert _axis(card, 'gdd_quality')['status'] == 'not_instrumented_zero'
    assert _axis(card, 'task_visual')['credit'] is None
    assert not card['ranking_eligible']


def asset_first_scene_failure(mode):
    result = result_for(mode, cold_status='passed')
    result.mode1_context['candidate_truth'] = {'levels': [
        {'declared_scene': 'res://level_01.tscn', 'stop_reason': 'scene_load_failed', 'reached': False},
        {'declared_scene': 'res://level_02.tscn', 'stop_reason': 'no_goal', 'reached': True},
    ]}
    channels = next(item for item in result.items if item.id == 'reproduction').evidence['card']['channels']
    asset = next(row for row in channels if row['channel'] == 'O6')
    asset['note'] = ('no engine reading: asset probe produced no engine usage report '
                     '(returncode=1, timed_out=False, seconds=0.4): ERROR: Scene instance is missing.')
    return result, asset


@pytest.mark.parametrize('mode', ['brief', 'gdd', 'skeleton'])
def test_broken_asset_probe_start_scene_is_candidate_failure_only_for_assets(mode):
    result, asset_source = asset_first_scene_failure(mode)
    card = score_task_result(result)
    axis = _axis(card, 'asset_realization')
    assert axis['credit'] == 0
    assert axis['status'] == 'candidate_failure'
    assert axis['evidence']['failure_code'] == 'candidate_asset_start_scene_failed'
    asset_source['note'] = 'asset probe did not run'
    control = score_task_result(result)
    for name in ('task_checkpoints', 'hidden_scenarios'):
        assert _axis(card, name) == _axis(control, name)


@pytest.mark.parametrize('cause', ['timeout', 'unreached', 'later_scene', 'copy'])
def test_asset_start_scene_attribution_needs_failed_first_scene_and_completed_probe(cause):
    result, asset = asset_first_scene_failure('skeleton')
    if cause == 'timeout':
        asset['note'] = asset['note'].replace('returncode=1, timed_out=False', 'returncode=-9, timed_out=True')
    elif cause == 'unreached':
        result.mode1_context['candidate_truth']['levels'][0]['stop_reason'] = 'no_record'
    elif cause == 'later_scene':
        result.mode1_context['candidate_truth']['levels'].reverse()
    elif cause == 'copy':
        asset['note'] = 'no engine reading: asset scratch preparation failed'
    card = score_task_result(result)
    assert _axis(card, 'asset_realization')['status'] == 'not_instrumented_zero'
