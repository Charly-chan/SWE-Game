
from pathlib import Path

import pytest

from evalsys.engine.import_retry import configure_scratch_import


@pytest.mark.parametrize("source", [
    'config_version=5\n[application]\nconfig/name="Demo"\n',
    'config_version=5\n[editor]\nimport/use_multiple_threads=true\nother=7\n[rendering]\nx=1\n',
    'config_version=5\n[editor]\nother=7\n[rendering]\nx=1\n',
    'config_version=5\n[editor]\nimport/use_multiple_threads=false\n',
])
def test_serial_setting_is_idempotent_and_preserves_other_settings(tmp_path, source):
    project = tmp_path / "project.godot"
    project.write_text(source)
    configure_scratch_import(tmp_path)
    updated = project.read_text()
    assert updated.count("import/use_multiple_threads=false") == 1
    assert "import/use_multiple_threads=true" not in updated
    assert updated.count("[editor]") == 1
    if "other=7" in source:
        assert "other=7\n[rendering]\nx=1" in updated
    configure_scratch_import(tmp_path)
    assert project.read_text() == updated


def _project(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "project.godot").write_text("config_version=5\n[editor]\nimport/use_multiple_threads=true\n")
    return root


def test_probe_scratch_and_warm_reuse_do_not_change_source(tmp_path, monkeypatch):
    from evalsys.probe import inject

    source = _project(tmp_path)
    original = (source / "project.godot").read_text()
    monkeypatch.setattr(inject, "SCRATCH_ROOT", tmp_path)
    scratch = inject.make_scratch(source, tmp_path / "copy")
    assert "import/use_multiple_threads=false" in (scratch / "project.godot").read_text()
    imported = scratch / ".godot/imported"
    imported.mkdir(parents=True)
    (imported / "warm.fontdata").write_bytes(b"cache")
    (scratch / "project.godot").write_text(original)
    monkeypatch.setenv("GB_ROUTES_KEEP_SCRATCH", "1")
    reused = inject.make_scratch(source, scratch)
    assert "import/use_multiple_threads=false" in (reused / "project.godot").read_text()
    assert (source / "project.godot").read_text() == original


def test_objective_scratch_does_not_change_source(tmp_path, monkeypatch):
    from evalsys.ocard import adapters

    source = _project(tmp_path)
    original = (source / "project.godot").read_text()
    monkeypatch.setattr(adapters, "SCRATCH_ROOT", tmp_path / "copies")
    with adapters.make_scratch(source) as scratch:
        assert "import/use_multiple_threads=false" in (Path(scratch.path) / "project.godot").read_text()
    assert (source / "project.godot").read_text() == original


def test_runtime_gate_scratch_does_not_change_source(tmp_path, monkeypatch):
    from evalsys.ocard import adapters

    gate = adapters.load_tool_module(adapters.CHECK_RUNTIME)
    source = _project(tmp_path)
    original = (source / "project.godot").read_text()
    monkeypatch.setattr(gate, "SCRATCH_ROOT", str(tmp_path / "copies"))
    scratch = gate.make_scratch(str(source), "demo")
    assert scratch is not None
    assert "import/use_multiple_threads=false" in (Path(scratch) / "project.godot").read_text()
    assert (source / "project.godot").read_text() == original
