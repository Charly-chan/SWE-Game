import hashlib
import json
import subprocess

import pytest

from evalsys import harness


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True,
        )

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    monkeypatch.setattr(harness, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(harness, "_COMMITTED", {})
    name = "gb_probe.gd"
    original = "1" * 64
    content = b"extends Node\n\nfunc value():\n    return 1\n"
    sha = hashlib.sha256(content).hexdigest()
    relative = f"eval/harness/versions/{sha}/{name}"
    face = tmp_path / relative
    face.parent.mkdir(parents=True)
    face.write_bytes(content)
    manifest = tmp_path / "eval/harness/released-identities.json"
    data = {"schema_version": 1, "faces": {name: {
        original: {"path": relative, "sha256": sha},
    }}}
    manifest.write_text(json.dumps(data))

    def commit():
        git("add", ".")
        git("commit", "-qm", "Fixture")

    return name, original, face, manifest, data, commit


def test_release_identity_requires_committed_binding_and_bytes(checkout):
    name, original, face, manifest, data, commit = checkout
    assert harness.locate_committed(name, original) is None
    commit()
    identity = harness.locate_committed(name, original)
    assert identity.endswith(data["faces"][name][original]["path"])
    harness.assert_identifiable({name: original})
    assert harness.locate_committed(name, "2" * 64) is None


@pytest.mark.parametrize("change", ["path", "content", "missing"])
def test_invalid_release_identity_fails_closed(checkout, change):
    name, original, face, manifest, data, commit = checkout
    if change == "path":
        data["faces"][name][original]["path"] = "../private.gd"
        manifest.write_text(json.dumps(data))
    elif change == "content":
        face.write_text("extends Node\nfunc value():\n    return 99\n")
    else:
        face.unlink()
    commit()
    assert harness.locate_committed(name, original) is None
    with pytest.raises(ValueError, match="not a committed revision"):
        harness.assert_identifiable({name: original})


def test_uncommitted_manifest_cannot_authorize_new_identity(checkout):
    name, original, face, manifest, data, commit = checkout
    commit()
    data["faces"][name]["2" * 64] = data["faces"][name][original]
    manifest.write_text(json.dumps(data))
    assert harness.locate_committed(name, "2" * 64) is None
