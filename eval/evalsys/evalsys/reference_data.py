
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import sys
import tarfile
import tempfile
import time
from urllib.request import Request, urlopen

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def matches(path: Path, item: dict) -> bool:
    return path.is_file() and (not path.is_symlink()) and (path.stat().st_size == item['size']) and (sha256(path) == item['sha256'])

def ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    if not context.cert_store_stats().get('x509_ca') and (not os.environ.get('SSL_CERT_FILE')):
        for bundle in ('/etc/ssl/certs/ca-certificates.crt', '/etc/ssl/cert.pem', '/etc/pki/tls/certs/ca-bundle.crt'):
            if Path(bundle).is_file():
                context.load_verify_locations(cafile=bundle)
                break
    return context

@contextmanager
def locked(path: Path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield

def download(data: dict, item: dict, cache: Path) -> Path:
    target = cache / item['sha256']
    with locked(cache / (item['sha256'] + '.lock')):
        if matches(target, item):
            return target
        partial = target.with_suffix('.part')
        if target.is_symlink() or partial.is_symlink():
            raise ValueError('download cache entries must not be symlinks')
        url = f"https://huggingface.co/datasets/{data['repo_id']}/resolve/{data['revision']}/{item['path']}"
        print(f"Downloading {item['path']} ({item['size']:,} bytes)", file=sys.stderr, flush=True)
        for attempt in range(3):
            try:
                offset = partial.stat().st_size if partial.exists() else 0
                if offset >= item['size']:
                    partial.unlink()
                    offset = 0
                headers = {'User-Agent': 'SWE-Game-reference-data/1'}
                if offset:
                    headers['Range'] = f'bytes={offset}-'
                with urlopen(Request(url, headers=headers), timeout=60, context=ssl_context()) as response:
                    resume = offset and response.status == 206
                    if resume and (not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-')):
                        raise ValueError('unexpected download range')
                    with partial.open('ab' if resume else 'wb') as output:
                        shutil.copyfileobj(response, output, 1024 * 1024)
                if not matches(partial, item):
                    partial.unlink()
                    raise ValueError(f"checksum/size mismatch: {item['path']}")
                partial.replace(target)
                return target
            except (OSError, ValueError) as exc:
                if attempt == 2:
                    raise RuntimeError(f"Could not download {item['path']}: {exc}") from exc
                time.sleep(attempt + 1)
    raise AssertionError('unreachable')

def has_project(path: Path) -> bool:
    return (path / 'project.godot').is_file() or len(list(path.glob('*/project.godot'))) == 1

from .frozen_data import ROOT, LOCK_PATH, load_manifest, bundle_index, install_files


def ensure_game(game_id: str, *, videos: str = 'none', root: Path = ROOT) -> Path:
    if videos not in ('none', 'primary', 'all'):
        raise ValueError('unknown video selection')
    data = load_manifest(root)
    if game_id not in data['games']:
        raise ValueError(f'unknown game: {game_id}')
    destination = root / 'games' / game_id
    with locked(root / '.reference-data-locks' / f'{game_id}.lock'):
        index, bundle = bundle_index(data, game_id)
        if not has_project(destination):
            install_files(data, game_id, index['reference'], bundle, destination)
        if videos != 'none':
            from .frozen_data import _cache, safe_relative
            for item in data['games'][game_id]['videos'].values():
                target = destination / safe_relative(item['destination'])
                if any(p.is_symlink() for p in [target, *target.parents]):
                    raise ValueError('video path must not traverse symlinks')
                if matches(target, item):
                    continue
                if target.exists():
                    raise FileExistsError(f'refusing to overwrite: {target}')
                source = download(data, item, _cache(data))
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                    temporary = Path(output.name)
                    try:
                        with source.open('rb') as input_file:
                            shutil.copyfileobj(input_file, output)
                        output.flush()
                        temporary.chmod(0o644)
                        temporary.replace(target)
                    finally:
                        temporary.unlink(missing_ok=True)
    return destination


def ensure_catalog_path(path: Path, *, root: Path = ROOT) -> None:
    path = path.absolute()
    if path.parent == root / 'games' and not has_project(path):
        if path.name in load_manifest(root)['games']:
            ensure_game(path.name, root=root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', required=True, help='catalog game ID, or all')
    parser.add_argument('--with-videos', action='store_true')
    args = parser.parse_args()
    try:
        games = load_manifest()['games'] if args.game == 'all' else [args.game]
        for game_id in games:
            ensure_game(game_id, videos='primary' if args.with_videos else 'none')
            print(f'Ready: games/{game_id}', flush=True)
    except (OSError, ValueError, RuntimeError, tarfile.TarError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
