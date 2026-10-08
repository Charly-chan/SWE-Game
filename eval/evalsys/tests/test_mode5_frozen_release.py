import json
import shutil

import pytest

from evalsys.taskgen import generate
from evalsys.taskgen.mode5.package_release import prepare_scaffold
from evalsys.taskgen.mode5 import release_data
from evalsys.taskgen.mode5.community_profile import load_default_profile, task_environment_lock
from evalsys.taskgen.package import write_json, sha256_file
from evalsys.taskgen.unity.unity_sdk import TARGET_UNITY_ROOT, validate_scaffold_integrity
from evalsys.taskgen.unity.unity_suite_compiler import suite_content_digest


def test_frozen_port_install_adapts_scaffold_and_pins_environment(tmp_path, monkeypatch):
    calls = []
    def install(game, variant, dest):
        calls.append((game, variant))
        shutil.copytree(TARGET_UNITY_ROOT, dest / 'visible/target_unity')
        (dest / 'hidden/unity').mkdir(parents=True)
        write_json(dest / 'hidden/unity/scaffold_integrity.json', {'candidate_mutable': ['Assets/Game/**']})
        write_json(dest / 'manifest.json', {'schema_version': 1, 'mode': 'port', 'game_id': game})
        for name in ('HANDOFF.md', 'PROMPT.md', 'visible/PROMPT.md'):
            (dest / name).write_text('Frozen port task\n', encoding='utf-8')
        # The publisher builds these fixed bytes; runtime generation only
        # installs them and verifies the pinned release identity.
        package = generate.TaskPackage.read(dest)
        release_data.install_released_suite(package, generate.ROOT)
        prepare_scaffold(package)
    monkeypatch.setattr(generate, 'load_manifest', lambda: {'games': {'shadow_walker': {'cases': []}}})
    monkeypatch.setattr(generate, 'install_task', install)
    monkeypatch.setattr(generate, 'freeze_visual_rubric', lambda *a: None)
    package = generate.generate_task('shadow_walker', mode='port', out=tmp_path / 'task')
    assert calls == [('shadow_walker', 'shadow_walker--port--default--kit0--video1')]
    project = package.visible / 'target_unity'
    lock = package.visible / 'environment.lock.json'
    assert json.loads(lock.read_text(encoding='utf-8')) == task_environment_lock(load_default_profile())
    assert package.manifest['environment_lock']['sha256'] == sha256_file(lock)
    integrity = json.loads((package.hidden / 'unity/scaffold_integrity.json').read_text(encoding='utf-8'))
    assert integrity['profile_id'] == 'mode5-community-docker-v1'
    assert validate_scaffold_integrity(project, integrity['files']).ok
    interface = json.loads((project / 'Assets/GameBenchmark/gb_interface.json').read_text(encoding='utf-8'))
    assert interface['scaffold_digest'] == integrity['scaffold_digest']
    dependencies = json.loads((project / 'Packages/manifest.json').read_text(encoding='utf-8'))['dependencies']
    assert dependencies['com.unity.ugui'] == '2.0.0'
    assert 'gb-unity check' in (package.visible / 'ENVIRONMENT.md').read_text(encoding='utf-8')
    assert (package.root / 'PROMPT.md').read_bytes() == (package.visible / 'PROMPT.md').read_bytes()


@pytest.mark.parametrize('tamper', ['suite', 'gate', 'game', 'status'])
def test_release_readiness_requires_matching_calibration(tamper):
    suite = {'schema': 'gamebench.unity-hidden-suite.v1', 'game_id': 'example',
             'status': 'calibrated', 'runtime_ready': True, 'scenarios': []}
    suite['content_digest'] = suite_content_digest(suite)
    gate = {'schema': 'gamebench.unity-calibration.v1', 'game_id': 'example',
            'status': 'calibrated', 'runtime_ready': True, 'suite_digest': suite['content_digest']}
    assert release_data.suite_ready(suite, gate, 'example')
    if tamper == 'suite':
        suite['scenarios'] = [{'changed': True}]
    elif tamper == 'gate':
        gate['suite_digest'] = 'sha256:stale'
    elif tamper == 'game':
        gate['game_id'] = 'other'
    else:
        gate['status'] = 'pending'
    assert not release_data.suite_ready(suite, gate, 'example')


def test_release_suite_reads_checked_release_files(tmp_path):
    directory = tmp_path / 'data/mode5/example'
    directory.mkdir(parents=True)
    entries = []
    for name, value in [('suite.json', {'schema': 'suite'}), ('calibration_status.json', {'schema': 'gate'})]:
        blob = directory / name
        write_json(blob, value)
        digest = sha256_file(blob)
        entries.append((name, {'sha256': digest, 'size': blob.stat().st_size}))
    write_json(tmp_path / 'data/mode5/manifest.json', {
        'schema_version': 1, 'version': '2026-10.mode5-calibrated-v3',
        'source_commit': '1' * 40, 'games': {'example': dict(entries)},
    })
    assert release_data.load_released_suite(tmp_path, 'example') == ({'schema': 'suite'}, {'schema': 'gate'})
    (directory / 'suite.json').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        release_data.load_released_suite(tmp_path, 'example')


def test_complete_release_has_41_matching_calibrated_contracts():
    games = json.loads((generate.ROOT / 'data/task-data.json').read_text(encoding='utf-8'))['games']
    release = json.loads((generate.ROOT / 'data/mode5/manifest.json').read_text(encoding='utf-8'))
    assert set(release['games']) == set(games)
    assert len(games) == 41
    for game in games:
        suite, gate = release_data.load_released_suite(generate.ROOT, game)
        assert release_data.suite_ready(suite, gate, game), game


def test_runtime_refuses_older_fixed_mode5_task(tmp_path):
    package = type('Package', (), {'manifest': {'game_id': 'shadow_walker'}, 'hidden': tmp_path})()
    with pytest.raises(ValueError, match='predates this calibrated release'):
        release_data.validate_released_package(package, generate.ROOT)
