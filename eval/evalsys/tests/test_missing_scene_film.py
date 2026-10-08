
from pathlib import Path
from types import SimpleNamespace

import pytest

from evalsys.routes.agent import Op
from evalsys.taskgen.replay_film import film_submission_replay
from evalsys.taskgen.visual_materials import VisualEvidenceError, prepare_candidate_frames


def film_fixture(tmp_path, monkeypatch, *, scene_exists=False, godot=True):
    project = tmp_path / 'game'
    project.mkdir()
    scene = 'res://levels/level_01.tscn'
    if scene_exists:
        path = project / 'levels/level_01.tscn'
        path.parent.mkdir()
        path.write_text('[gd_scene format=3]\n[node name="Level" type="Node"]\n')
    interface = SimpleNamespace(project_root=project, levels=[SimpleNamespace(scene=scene)])
    monkeypatch.setattr('evalsys.taskgen.replay_film.godot_available',
                        lambda: Path('godot') if godot else None)
    monkeypatch.setattr('evalsys.routes.runner.RouteSession', lambda *a, **k: object())
    return project, interface


def record(project, interface, out):
    return film_submission_replay(project, out, interface=interface,
        ops=[Op('state', frames=30, actions=('gb_right',))], predicate='whole_game_clear()')


def test_missing_first_scene_is_candidate_failure_before_recording(tmp_path, monkeypatch):
    project, interface = film_fixture(tmp_path, monkeypatch)
    def no_engine(*args):
        pytest.fail('the nonexistent scene must be identified before scratch preparation')
    monkeypatch.setattr('evalsys.taskgen.replay_film.prepare_session', no_engine)
    film = record(project, interface, tmp_path / 'film')
    assert film.stop_reason == 'scene_missing'
    assert 'res://levels/level_01.tscn' in film.error
    with pytest.raises(VisualEvidenceError) as caught:
        prepare_candidate_frames([{'id': 'demo', 'film': film.to_dict()}], tmp_path / 'frames')
    assert caught.value.owner == 'candidate'
    assert caught.value.code == 'candidate_scene_missing'


def test_unavailable_engine_remains_evaluator_failure(tmp_path, monkeypatch):
    project, interface = film_fixture(tmp_path, monkeypatch, godot=False)
    film = record(project, interface, tmp_path / 'film')
    with pytest.raises(VisualEvidenceError) as caught:
        prepare_candidate_frames([{'id': 'demo', 'film': film.to_dict()}], tmp_path / 'frames')
    assert caught.value.owner == 'evaluator'
    assert caught.value.code == 'recorder_failed'


@pytest.mark.parametrize('detail', ['cold import timed out', 'scratch copy failed: no space'])
def test_existing_scene_does_not_hide_preparation_failure(tmp_path, monkeypatch, detail):
    project, interface = film_fixture(tmp_path, monkeypatch, scene_exists=True)
    def failed_preparation(session, record):
        record['prepare'] = {'driver_error': detail}
        return False
    monkeypatch.setattr('evalsys.taskgen.replay_film.prepare_session', failed_preparation)
    film = record(project, interface, tmp_path / 'film')
    assert film.stop_reason != 'scene_missing'
    with pytest.raises(VisualEvidenceError) as caught:
        prepare_candidate_frames([{'id': 'demo', 'film': film.to_dict()}], tmp_path / 'frames')
    assert caught.value.owner == 'evaluator'


def import_failure():
    return {'attempts': [{'scratch': {'ok': True}, 'inject': {'ok': True},
        'import': {'ok': False, 'timed_out': False, 'returncode': 0,
                   'errors': ["ERROR: Cannot open file 'res://main.tscn'."]}}],
        'driver_error': "cold import failed: cannot open res://main.tscn"}


def test_missing_other_scene_confirmed_by_import_is_candidate_failure(tmp_path, monkeypatch):


    project, interface = film_fixture(tmp_path, monkeypatch, scene_exists=True)
    def failed_preparation(session, record):
        record['prepare'] = import_failure()
        return False
    monkeypatch.setattr('evalsys.taskgen.replay_film.prepare_session', failed_preparation)
    film = record(project, interface, tmp_path / 'film')
    assert film.stop_reason == 'scene_missing'
    with pytest.raises(VisualEvidenceError) as caught:
        prepare_candidate_frames([{'id': 'demo', 'film': film.to_dict()}], tmp_path / 'frames')
    assert caught.value.owner == 'candidate'
    assert caught.value.code == 'candidate_scene_missing'


@pytest.mark.parametrize('cause', ['timeout', 'copy', 'injection', 'killed', 'present', 'settled'])
def test_import_failure_needs_confirmed_absent_candidate_scene(tmp_path, monkeypatch, cause):
    project, interface = film_fixture(tmp_path, monkeypatch, scene_exists=True)
    preparation = import_failure()
    attempt = preparation['attempts'][-1]
    if cause == 'timeout':
        attempt['import']['timed_out'] = True
    elif cause == 'copy':
        attempt['scratch']['ok'] = False
    elif cause == 'injection':
        attempt['inject']['ok'] = False
    elif cause == 'killed':
        attempt['import']['returncode'] = -9
    elif cause == 'present':
        (project / 'main.tscn').write_text('[gd_scene format=3]\n')
    elif cause == 'settled':
        attempt['import']['errors'] = ["settled on reimport: " + attempt['import']['errors'][0]]
    def failed_preparation(session, record):
        record['prepare'] = preparation
        return False
    monkeypatch.setattr('evalsys.taskgen.replay_film.prepare_session', failed_preparation)
    film = record(project, interface, tmp_path / 'film')
    assert film.stop_reason != 'scene_missing'
    with pytest.raises(VisualEvidenceError) as caught:
        prepare_candidate_frames([{'id': 'demo', 'film': film.to_dict()}], tmp_path / 'frames')
    assert caught.value.owner == 'evaluator'


def scene_parse_failure(error_format='prefix'):
    preparation = import_failure()
    error = (
        "ERROR: res://levels/level_01.tscn:1 - Parse Error: Unrecognized file type 'gdn_scene'."
        if error_format == 'prefix' else
        'ERROR: Parse Error: Invalid parameter. [Resource file res://levels/level_01.tscn:15]'
    )
    preparation['attempts'][-1]['import']['errors'] = [
        error,
        'ERROR: Failed loading resource: res://levels/level_01.tscn.',
    ]
    preparation['driver_error'] = 'cold import failed: scene parse error'
    return preparation


@pytest.mark.parametrize('error_format', ['prefix', 'resource'])
def test_candidate_scene_parse_error_prevents_recording(tmp_path, monkeypatch, error_format):
    project, interface = film_fixture(tmp_path, monkeypatch, scene_exists=True)
    malformed_scene = ('[gdn_scene load_steps=4 format=3]\n' if error_format == 'prefix' else
        '[gd_scene format=3]\n[node name="Level" type="CollisionShape2D"]\n'
        'shape = SubResource("RectangleShape2D_8h5p")\n')
    (project / 'levels/level_01.tscn').write_text(malformed_scene)
    def failed_preparation(session, record):
        record['prepare'] = scene_parse_failure(error_format)
        return False
    monkeypatch.setattr('evalsys.taskgen.replay_film.prepare_session', failed_preparation)
    film = record(project, interface, tmp_path / 'film')
    assert film.stop_reason == 'scene_load_failed'
    with pytest.raises(VisualEvidenceError) as caught:
        prepare_candidate_frames([{'id': 'demo', 'film': film.to_dict()}], tmp_path / 'frames')
    assert caught.value.owner == 'candidate'
    assert caught.value.code == 'candidate_scene_load_failed'


@pytest.mark.parametrize('cause', ['timeout', 'copy', 'injection', 'killed',
                                  'not_in_submission', 'settled', 'successful_retry'])
@pytest.mark.parametrize('error_format', ['prefix', 'resource'])
def test_parse_error_needs_completed_import_and_candidate_owned_scene(
        tmp_path, monkeypatch, cause, error_format):
    project, interface = film_fixture(tmp_path, monkeypatch, scene_exists=True)
    preparation = scene_parse_failure(error_format)
    attempt = preparation['attempts'][-1]
    if cause == 'timeout':
        attempt['import']['timed_out'] = True
    elif cause == 'copy':
        attempt['scratch']['ok'] = False
    elif cause == 'injection':
        attempt['inject']['ok'] = False
    elif cause == 'killed':
        attempt['import']['returncode'] = -9
    elif cause == 'not_in_submission':
        attempt['import']['errors'][0] = attempt['import']['errors'][0].replace(
            'levels/level_01.tscn', 'evaluator_only.tscn')
    elif cause == 'settled':
        attempt['import']['errors'][0] = 'settled on reimport: ' + attempt['import']['errors'][0]
    elif cause == 'successful_retry':
        preparation['attempts'].append({'scratch': {'ok': True}, 'inject': {'ok': True},
            'import': {'ok': True, 'timed_out': False, 'returncode': 0, 'errors': []}})
    def failed_preparation(session, record):
        record['prepare'] = preparation
        return False
    monkeypatch.setattr('evalsys.taskgen.replay_film.prepare_session', failed_preparation)
    film = record(project, interface, tmp_path / 'film')
    assert film.stop_reason != 'scene_load_failed'
    with pytest.raises(VisualEvidenceError) as caught:
        prepare_candidate_frames([{'id': 'demo', 'film': film.to_dict()}], tmp_path / 'frames')
    assert caught.value.owner == 'evaluator'
