
from copy import deepcopy
import json
from pathlib import Path

import pytest

from evalsys.routes.runner import reading_from_report
from evalsys.routes.schema import Budget, Goal, Route
from evalsys.taskgen.content.rubrics import audit_rubric, load_rubric_catalog, rubric_milestones
from evalsys.taskgen.evaluate import _mechanic_trace_item, gdd_mechanics_observable_item
from evalsys.taskgen.antigrant import health_write_binding
from evalsys.verdict import Verdict

CATALOG = load_rubric_catalog()


def isolated_rubric(game, checkpoint):
    rubric = deepcopy(CATALOG[game])
    rubric['mechanic_checks'] = [c for c in rubric['mechanic_checks'] if c['id'] == checkpoint]
    rubric['required_groups'] = []
    rubric['required_numeric_slots'] = []
    return rubric


def read(rubric, rows):
    route = Route(route_id='fixture/L5', tier=5,
                  goal=Goal('whole_game_clear()', observations=rubric_milestones(rubric)),
                  budget=Budget(steps=1, frames=40))
    return reading_from_report(route, {'stop_reason': 'budget_frames', 'rows': rows}).to_dict()


@pytest.mark.parametrize('game', ['ballast_yard', 'hive_flight'])
def test_elapsed_clock_moves_up_but_stopped_clock_fails(game):
    checkpoint = 'committed_run_clock' if game == 'ballast_yard' else 'level_clock'
    rubric = isolated_rubric(game, checkpoint)
    for end, verdict in [(3, Verdict.PASSED), (0, Verdict.FAILED)]:
        reading = read(rubric, [{'f': 0, 'n': {'timer': 0}}, {'f': 1, 'n': {'timer': end}}])
        assert _mechanic_trace_item(reading, rubric).verdict is verdict


@pytest.mark.parametrize('game,checkpoint', [('hazard_circuit', 'level_goal'), ('hive_flight', 'runner_progress')])
def test_level_progress_needs_declared_level_or_success(game, checkpoint):
    rubric = isolated_rubric(game, checkpoint)
    for lv, won, verdict in [(2, False, Verdict.PASSED), (1, True, Verdict.PASSED), (1, False, Verdict.FAILED)]:
        reading = read(rubric, [{'f': 0, 'lv': 1}, {'f': 1, 'lv': lv, 'wgc': won}])
        assert _mechanic_trace_item(reading, rubric).verdict is verdict


def test_hostile_defeat_can_follow_spawn_above_initial_census():
    rubric = isolated_rubric('toon_shooter', 'hostile_defeat')
    for census, verdict in [([0, 3, 2], Verdict.PASSED), ([0, 3, 3], Verdict.FAILED)]:
        reading = read(rubric, [{'f': f, 'g': {'gb_enemy': n}} for f, n in enumerate(census)])
        assert reading['observations_triggered'] == ['hostile_defeat']
        assert _mechanic_trace_item(reading, rubric).verdict is verdict


def test_resource_drop_can_be_collected_after_spawning():
    rubric = isolated_rubric('terraforge', 'resource_pickup')
    for census, verdict in [([0, 1, 0], Verdict.PASSED), ([0, 1, 1], Verdict.FAILED)]:
        reading = read(rubric, [{'f': f, 'g': {'gb_collectible': n}} for f, n in enumerate(census)])
        assert _mechanic_trace_item(reading, rubric).verdict is verdict


@pytest.mark.parametrize('ending', [False, True])
def test_scene_reset_is_not_a_defeated_hostile(ending):
    rubric = isolated_rubric('toon_shooter', 'hostile_defeat')
    reading = read(rubric, [
        {'f': 0, 'lv': 1, 'g': {'gb_enemy': 2}},
        {'f': 1, 'lv': 1 if ending else 2, 'wgc': ending, 'g': {'gb_enemy': 0}},
    ])
    assert _mechanic_trace_item(reading, rubric).verdict is Verdict.FAILED


@pytest.mark.parametrize('game,checkpoint', [('pixel_platformer', 'pig_defeat'), ('terraforge', 'husk_defeat'), ('shadow_walker', 'capture_contact')])
def test_optional_paths_do_not_block_clear_or_earn_unobserved_credit(game, checkpoint):
    rubric = isolated_rubric(game, checkpoint)
    reading = read(rubric, [{'f': 0, 'g': {'gb_enemy': 2}}, {'f': 1, 'g': {'gb_enemy': 2}, 'wgc': True}])
    item = _mechanic_trace_item(reading, rubric)
    assert item.verdict is Verdict.PASSED
    assert item.credit == 0
    assert item.evidence['expected'] == [checkpoint]
    assert item.evidence['optional_missing'] == [checkpoint]
    graded = gdd_mechanics_observable_item(rubric, present_groups=[], numeric_values={}, trace_reading=reading)
    assert graded.credit == 0
    assert graded.evidence['denominator'] == 1
    required = deepcopy(rubric)
    required['mechanic_checks'][0]['observable']['required_on_witness'] = True
    assert _mechanic_trace_item(reading, required).verdict is Verdict.FAILED


@pytest.mark.parametrize('game,checkpoint', [('city_delivery', 'delivery_pay'), ('harvest_ledger', 'day_clock')])
def test_undeclared_measurement_is_explicitly_unmeasurable(game, checkpoint):
    rubric = isolated_rubric(game, checkpoint)
    assert rubric['mechanic_checks'][0]['measurable'] is False
    assert rubric['mechanic_checks'][0]['reason']
    assert rubric_milestones(rubric) == []
    assert audit_rubric(game, CATALOG[game])['ready']


@pytest.mark.parametrize('game', ['pixel_sabotage', 'pulse_lane', 'wizard_chase'])
def test_reference_health_binding_follows_existing_state(game):
    project = Path(__file__).resolve().parents[3] / 'games' / game
    interface = json.loads((project / 'gb_levels.json').read_text())
    binding = health_write_binding(project, interface['numeric']['health'], levels=interface['levels'])
    assert binding['writable']
    if game != 'pulse_lane':
        assert binding['mirror'] > 0
    else:
        assert any(h.path == 'src/core/run_state.gd' and h.classification == 'owner' for h in binding['hits'])
    assert not any(h.snippet.startswith('#') for h in binding['hits'])
