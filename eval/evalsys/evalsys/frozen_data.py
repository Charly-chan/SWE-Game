
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = Path('data/task-data.json')
_HEX = re.compile(r'[0-9a-f]{64}')


def safe_relative(value: str) -> Path:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '..' in path.parts or '\\' in value
            or '\x00' in value or re.match(r'^[A-Za-z]:', value)
            or path.as_posix() != value or value in {'.', '..'}):
        raise ValueError(f'invalid released path: {value!r}')
    return Path(*path.parts)


def load_manifest(root: Path = ROOT) -> dict:
    data = json.loads((root / LOCK_PATH).read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not data.get('games'):
        raise ValueError('unsupported or empty task-data manifest')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', data.get('repo_id', '')):
        raise ValueError('invalid dataset repository')
    if (not re.fullmatch(r'[0-9a-f]{40}', data.get('revision', ''))
            or data['revision'] == '0' * 40):
        raise ValueError('task-data revision must be an immutable commit SHA')
    for game_id, game in data['games'].items():
        if not re.fullmatch(r'[a-z0-9_]+', game_id):
            raise ValueError('invalid game id')
        if not _HEX.fullmatch(game['bundle'].get('index_sha256', '')):
            raise ValueError('invalid bundle index checksum')
        for item in [game['bundle'], *game.get('videos', {}).values()]:
            safe_relative(item['path'])
            if not _HEX.fullmatch(item['sha256']) or not isinstance(item['size'], int) or item['size'] <= 0:
                raise ValueError('invalid release checksum or size')
    return data


def _cache(data: dict) -> Path:
    return Path(os.environ.get('SWE_GAME_CACHE', str(Path.home() / '.cache/swe-game'))) / data['revision']


def bundle_index(data: dict, game_id: str) -> tuple[dict, Path]:
    from .reference_data import download, locked, sha256
    game = data['games'][game_id]
    cache = _cache(data)
    archive = download(data, game['bundle'], cache)
    destination = cache / ('bundle-' + game['bundle']['sha256'])
    with locked(cache / (destination.name + '.lock')):
        if not (destination / 'index.json').is_file():
            with tempfile.TemporaryDirectory(prefix='.bundle-', dir=cache) as scratch:
                staging = Path(scratch)
                seen = set()
                with tarfile.open(archive, 'r:gz') as tar:
                    for member in tar:
                        path = safe_relative(member.name)
                        if (not member.isfile() or member.name in seen
                                or not (member.name == 'index.json' or len(path.parts) == 2
                                        and path.parts[0] == 'blobs' and _HEX.fullmatch(path.name))):
                            raise ValueError(f'invalid bundle entry: {member.name}')
                        seen.add(member.name)
                        target = staging / path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with tar.extractfile(member) as source, target.open('xb') as output:
                            shutil.copyfileobj(source, output)
                        if path.parts[0] == 'blobs' and sha256(target) != path.name:
                            raise ValueError('bundle blob checksum mismatch')
                if sha256(staging / 'index.json') != game['bundle']['index_sha256']:
                    raise ValueError('bundle index checksum mismatch')
                _validate_index(json.loads((staging / 'index.json').read_text()), game_id)
                if destination.exists():
                    raise ValueError('incomplete bundle cache; remove it and retry')
                staging.rename(destination)
    if destination.is_symlink():
        raise ValueError('bundle cache must not be a symlink')
    if sha256(destination / 'index.json') != game['bundle']['index_sha256']:
        raise ValueError('cached bundle index checksum mismatch')
    index = json.loads((destination / 'index.json').read_text())
    _validate_index(index, game_id)
    return index, destination


def _validate_index(index: dict, game_id: str) -> None:
    if index.get('schema_version') != 1 or index.get('game_id') != game_id:
        raise ValueError('bundle identity mismatch')
    for entries in [index.get('reference', []), *index['tasks'].values()]:
        seen = set()
        for entry in entries:
            safe_relative(entry['path'])
            if entry['path'] in seen or not _HEX.fullmatch(entry['sha256']):
                raise ValueError('duplicate path or invalid blob id')
            seen.add(entry['path'])
            if (entry.get('mode') not in (0o644, 0o755)
                    or type(entry.get('size')) is not int or entry['size'] < 0):
                raise ValueError('invalid file mode or size')


def install_files(data: dict, game_id: str, entries: list[dict], bundle: Path, destination: Path) -> None:
    from .reference_data import download, matches
    destination = Path(destination).absolute()
    for parent in [destination, *destination.parents]:
        if parent.is_symlink():
            raise ValueError(f'output path must not traverse a symlink: {parent}')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise FileExistsError(f'refusing to overwrite: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.swe-game-', dir=destination.parent) as scratch:
        staging = Path(scratch) / 'files'
        staging.mkdir()
        for entry in entries:
            target = staging / safe_relative(entry['path'])
            if entry.get('video'):
                item = data['games'][game_id]['videos'][entry['sha256']]
                source = download(data, item, _cache(data))
            else:
                source = bundle / 'blobs' / entry['sha256']
            if not matches(source, entry):
                raise ValueError(f'file checksum/size mismatch: {entry["path"]}')
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open('rb') as input_file, target.open('xb') as output:
                shutil.copyfileobj(input_file, output)
            target.chmod(entry['mode'])
        if destination.exists():
            destination.rmdir()
        staging.rename(destination)


def install_task(game_id: str, variant: str, out: Path, *, root: Path = ROOT) -> Path:
    data = load_manifest(root)
    if game_id not in data['games']:
        raise ValueError(f'unknown game: {game_id}')
    index, bundle = bundle_index(data, game_id)
    if variant not in index['tasks']:
        raise ValueError(f'unavailable task variant: {variant}')
    install_files(data, game_id, index['tasks'][variant], bundle, out)
    return out
