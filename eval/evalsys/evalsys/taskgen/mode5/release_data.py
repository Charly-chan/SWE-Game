"""Read the checksum-pinned Mode 5 calibration release shipped with the evaluator."""

import json
from pathlib import Path

from ...reference_data import matches
from ..unity.unity_suite_compiler import suite_content_digest


def load_released_suite(root: Path, game_id: str) -> tuple[dict, dict]:
    import re
    directory = root / 'data/mode5'
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('schema_version') != 1
            or manifest.get('version') != '2026-10.mode5-calibrated-v3'
            or not re.fullmatch(r'[0-9a-f]{40}', manifest.get('source_commit', ''))
            or not re.fullmatch(r'[a-z0-9_]+', game_id)):
        raise ValueError('unsupported Mode 5 data release')
    entries = manifest['games'][game_id]
    values = []
    for name in ('suite.json', 'calibration_status.json'):
        row = entries[name]
        path = directory / game_id / name
        if not matches(path, row):
            raise ValueError('released Unity suite checksum/size mismatch')
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('released Unity runtime contract must be an object')
        values.append(value)
    return values[0], values[1]


def validate_released_package(package, root: Path) -> None:
    """Runtime validation; authoring changes belong in the published HF bundle."""
    from ..package import sha256_file
    expected, gate = load_released_suite(root, package.manifest['game_id'])
    identity = package.manifest.get('mode5_data') or {}
    directory = package.hidden / 'unity/behavior'
    if (identity.get('version') != '2026-10.mode5-calibrated-v3'
            or identity.get('manifest_sha256') != sha256_file(root / 'data/mode5/manifest.json')
            or identity.get('suite_digest') != expected['content_digest']
            or json.loads((directory / 'suite.json').read_text(encoding='utf-8')) != expected
            or json.loads((directory / 'calibration_status.json').read_text(encoding='utf-8')) != gate):
        raise ValueError('fixed Mode 5 task package predates this calibrated release; '
                         'publish the prepared HF bundles and pin their dataset revision')


def suite_ready(suite: dict, gate: dict, game_id: str) -> bool:
    digest = suite_content_digest(suite)
    return (
        suite.get('schema') == 'gamebench.unity-hidden-suite.v1'
        and suite.get('game_id') == game_id
        and suite.get('content_digest') == digest
        and suite.get('status') == 'calibrated'
        and suite.get('runtime_ready') is True
        and gate.get('schema') == 'gamebench.unity-calibration.v1'
        and gate.get('game_id') == game_id
        and gate.get('suite_digest') == digest
        and gate.get('status') == 'calibrated'
        and gate.get('runtime_ready') is True
    )
