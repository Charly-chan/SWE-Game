import hashlib
import io
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

import pytest

from evalsys import frozen_data as fixed
from evalsys import reference_data as transfer


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def release(tmp_path, monkeypatch):
    monkeypatch.setenv('SWE_GAME_CACHE', str(tmp_path / 'cache'))
    payload = b'fixed task content\n'
    entry = {'path': 'visible/PROMPT.md', 'sha256': digest(payload),
             'size': len(payload), 'mode': 0o644}
    index = {'schema_version': 1, 'game_id': 'example', 'reference': [entry],
             'tasks': {'example--brief--default--kit0--video1': [entry]}}
    index_bytes = json.dumps(index).encode()
    archive = tmp_path / 'bundle.tar.gz'
    def make_archive(extra=None):
        with tarfile.open(archive, 'w:gz') as stream:
            for name, content in [('index.json', index_bytes),
                                  ('blobs/' + entry['sha256'], payload)]:
                info = tarfile.TarInfo(name); info.size = len(content)
                stream.addfile(info, io.BytesIO(content))
            if extra:
                info, content = extra
                stream.addfile(info, io.BytesIO(content) if content else None)
    make_archive()
    bundle = {'path': 'bundles/example.tar.gz', 'sha256': transfer.sha256(archive),
              'size': archive.stat().st_size, 'index_sha256': digest(index_bytes)}
    data = {'schema_version': 1, 'repo_id': 'owner/dataset', 'revision': 'a' * 40,
            'games': {'example': {'path': 'games/example', 'cases': [],
                                  'bundle': bundle, 'videos': {}}}}
    monkeypatch.setattr(transfer, 'download', lambda *args: archive)
    return data, index, archive, payload, make_archive


@pytest.mark.parametrize('value', ['/tmp/a', '../a', 'a/../b', 'a//b', './a',
                                  'C:/a', 'a\\b', '', '.', 'a\0b'])
def test_reject_unsafe_paths(value):
    with pytest.raises(ValueError):
        fixed.safe_relative(value)


def test_install_exact_files_and_preserve_existing_output(release, tmp_path):
    data, index, _, payload, _ = release
    loaded, cache = fixed.bundle_index(data, 'example')
    out = tmp_path / 'task'
    fixed.install_files(data, 'example', loaded['reference'], cache, out)
    assert (out / 'visible/PROMPT.md').read_bytes() == payload
    with pytest.raises(FileExistsError):
        fixed.install_files(data, 'example', loaded['reference'], cache, out)
    assert (out / 'visible/PROMPT.md').read_bytes() == payload


@pytest.mark.parametrize('attack', ['traversal', 'symlink', 'duplicate'])
def test_reject_malformed_archive(release, attack):
    data, _, _, _, make_archive = release
    info = tarfile.TarInfo('../escape' if attack == 'traversal' else 'index.json')
    if attack == 'symlink':
        info.type = tarfile.SYMTYPE; info.linkname = '/tmp/outside'
    make_archive((info, b''))
    with pytest.raises(ValueError):
        fixed.bundle_index(data, 'example')


def test_cached_index_corruption_is_detected(release):
    data, _, _, _, _ = release
    _, cache = fixed.bundle_index(data, 'example')
    (cache / 'index.json').write_text('{}')
    with pytest.raises(ValueError, match='index checksum'):
        fixed.bundle_index(data, 'example')


def test_corrupt_blob_does_not_leave_partial_output(release, tmp_path):
    data, index, _, _, _ = release
    _, cache = fixed.bundle_index(data, 'example')
    entry = index['reference'][0]
    (cache / 'blobs' / entry['sha256']).write_bytes(b'corrupt')
    out = tmp_path / 'task'
    with pytest.raises(ValueError, match='checksum'):
        fixed.install_files(data, 'example', index['reference'], cache, out)
    assert not out.exists()


def test_output_symlink_is_rejected(release, tmp_path):
    data, index, _, _, _ = release
    _, cache = fixed.bundle_index(data, 'example')
    real = tmp_path / 'real'; real.mkdir()
    alias = tmp_path / 'alias'; alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        fixed.install_files(data, 'example', index['reference'], cache, alias / 'task')
    assert not list(real.iterdir())


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body); self.status = status; self.headers = headers or {}


@pytest.mark.parametrize('honors_range', [True, False])
def test_download_resumes_or_restarts_safely(tmp_path, honors_range):
    payload = b'complete payload'
    item = {'path': 'bundle.tar.gz', 'size': len(payload), 'sha256': digest(payload)}
    (tmp_path / (item['sha256'] + '.part')).write_bytes(payload[:5])
    requests = []
    def open_response(request, **kwargs):
        requests.append(request)
        return (Response(payload[5:], 206, {'Content-Range': f'bytes 5-{len(payload)-1}/{len(payload)}'})
                if honors_range else Response(payload))
    with patch.object(transfer, 'urlopen', open_response):
        target = transfer.download({'repo_id': 'owner/data', 'revision': 'b'*40}, item, tmp_path)
    assert target.read_bytes() == payload
    assert requests[0].get_header('Range') == 'bytes=5-'


def test_download_checksum_failure_is_not_installed(tmp_path):
    item = {'path': 'bundle.tar.gz', 'size': 4, 'sha256': digest(b'good')}
    with patch.object(transfer, 'urlopen', lambda *a, **k: Response(b'evil')), \
         patch.object(transfer.time, 'sleep'):
        with pytest.raises(RuntimeError, match='checksum'):
            transfer.download({'repo_id': 'owner/data', 'revision': 'b'*40}, item, tmp_path)
    assert not (tmp_path / item['sha256']).exists()
