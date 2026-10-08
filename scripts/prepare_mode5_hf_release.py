"""Prepare fixed HF game bundles; change only port tasks, never upload.

Run on Linux with Python 3.10+. The output contains an upload tree, checksum
records, and a draft GitHub manifest. Finalize the manifest after HF publication.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval/evalsys'))


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8')


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def prepare_game(data, game_id, output):
    from evalsys.frozen_data import bundle_index, install_files, _validate_index
    from evalsys.reference_data import matches
    from evalsys.taskgen.package import TaskPackage
    from evalsys.taskgen.mode5.package_release import prepare_scaffold
    from evalsys.taskgen.mode5.release_data import install_released_suite, validate_released_package
    from evalsys.taskgen.visual_materials import freeze_visual_rubric

    original, source = bundle_index(data, game_id)
    index = copy.deepcopy(original)
    blobs = {}
    changed = []
    for variant, entries in index['tasks'].items():
        if '--port--' not in variant:
            continue
        with tempfile.TemporaryDirectory(prefix='mode5-fixed-task-') as temporary:
            package_root = Path(temporary) / 'package'
            # Videos retain their existing external checksum records; no need
            # to download or republish them during package authoring.
            install_files(data, game_id, [e for e in entries if not e.get('video')], source, package_root)
            package = TaskPackage.read(package_root)
            install_released_suite(package, ROOT)
            prepare_scaffold(package)
            freeze_visual_rubric(package.hidden, game_id)
            validate_released_package(package, ROOT)
            old_modes = {e['path']: e['mode'] for e in entries}
            replacement = [copy.deepcopy(e) for e in entries if e.get('video')]
            for file in sorted(package_root.rglob('*')):
                if not file.is_file():
                    continue
                relative = file.relative_to(package_root).as_posix()
                payload = file.read_bytes()
                sha = digest(payload)
                blobs[sha] = payload
                replacement.append({'path': relative, 'sha256': sha,
                                    'size': len(payload), 'mode': old_modes.get(relative, 0o644)})
            index['tasks'][variant] = sorted(replacement, key=lambda e: e['path'])
            changed.append(variant)
    if not changed:
        raise ValueError(f'no fixed port task in {game_id}')
    assert index['reference'] == original['reference']
    assert all(entries == original['tasks'][name] for name, entries in index['tasks'].items()
               if '--port--' not in name)
    _validate_index(index, game_id)
    references = {}
    for entries in [index['reference'], *index['tasks'].values()]:
        for entry in entries:
            if not entry.get('video'):
                references[entry['sha256']] = entry
    index_bytes = encoded(index)
    path = output / 'hf-upload' / data['games'][game_id]['bundle']['path']
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.USTAR_FORMAT) as archive:
            def add(name, payload):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(payload), 0o644, 0
                archive.addfile(info, io.BytesIO(payload))
            add('index.json', index_bytes)
            for sha, entry in sorted(references.items()):
                if sha in blobs:
                    payload = blobs[sha]
                else:
                    file = source / 'blobs' / sha
                    if not matches(file, entry):
                        raise ValueError(f'unchanged blob checksum mismatch: {sha}')
                    payload = file.read_bytes()
                if digest(payload) != sha or len(payload) != entry['size']:
                    raise ValueError('output blob identity mismatch')
                add('blobs/' + sha, payload)
    return {'path': data['games'][game_id]['bundle']['path'],
            'sha256': digest(path.read_bytes()), 'size': path.stat().st_size,
            'index_sha256': digest(index_bytes)}, changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--game', default='all')
    parser.add_argument('--finalize-hf-revision', default='', help='new immutable HF dataset commit after upload')
    args = parser.parse_args()
    output = args.out.expanduser().absolute()
    if args.finalize_hf_revision:
        if not re.fullmatch(r'[0-9a-f]{40}', args.finalize_hf_revision) or args.finalize_hf_revision == '0' * 40:
            parser.error('HF revision must be a real immutable commit SHA')
        draft = json.loads((output / 'task-data.draft.json').read_text())
        plan = json.loads((output / 'publication-plan.json').read_text())
        if set(plan['games']) != set(draft['games']):
            parser.error('final release requires all 41 rebuilt game bundles; a sample is not publishable')
        draft['revision'] = args.finalize_hf_revision
        (output / 'task-data.ready.json').write_bytes(encoded(draft))
        print('Prepared GitHub manifest:', output / 'task-data.ready.json')
        return 0
    if output.exists():
        parser.error('--out must be a new directory; preserve previous artifacts')
    from evalsys.frozen_data import load_manifest
    from evalsys.taskgen.mode5.release_data import load_released_suite, suite_ready
    data = load_manifest(ROOT)
    selected = sorted(data['games']) if args.game == 'all' else [args.game]
    for game_id in selected:
        if game_id not in data['games']:
            parser.error('unknown game: ' + game_id)
        suite, gate = load_released_suite(ROOT, game_id)
        if not suite_ready(suite, gate, game_id):
            parser.error('uncalibrated release: ' + game_id)
    output.mkdir(parents=True)
    draft = copy.deepcopy(data)
    draft['revision'] = 'REPLACE_WITH_NEW_HF_COMMIT_SHA'
    plan = {'base_dataset_revision': data['revision'], 'games': {},
            'mode5_source_commit': json.loads((ROOT / 'data/mode5/manifest.json').read_text())['source_commit']}
    for game_id in selected:
        bundle, variants = prepare_game(data, game_id, output)
        draft['games'][game_id]['bundle'] = bundle
        plan['games'][game_id] = {'bundle': bundle, 'port_variants': variants,
                                 'other_modes_unchanged': True, 'reference_and_videos_unchanged': True}
        (output / 'task-data.draft.json').write_bytes(encoded(draft))
        (output / 'publication-plan.json').write_bytes(encoded(plan))
        print(f'{game_id}: rebuilt {len(variants)} fixed port task(s), {bundle["size"]:,} bytes', flush=True)
    print('No uploads performed. Publish hf-upload/, then finalize using the returned HF commit SHA.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
