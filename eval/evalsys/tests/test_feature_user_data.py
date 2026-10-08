
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from evalsys.routes.agent import Op
from evalsys.routes.runner import RouteReading
from evalsys.scard.replay import ReplayFilm
from evalsys.taskgen.engine import fresh_user_data, godot_available, run_self_play
from evalsys.taskgen.modes import parse_mode
from evalsys.taskgen.replay_film import film_submission_replay


def test_fresh_user_data_restores_environment_and_keeps_existing_saves(tmp_path, monkeypatch):
    original = tmp_path / "existing-user-data"
    original.mkdir()
    (original / "save.json").write_text("existing collaborator progress")
    monkeypatch.setenv("GB_SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setenv("XDG_DATA_HOME", str(original))
    before_home = os.environ.get("HOME")
    with pytest.raises(RuntimeError):
        with fresh_user_data() as directory:
            assert list(directory.iterdir()) == []
            assert os.environ["XDG_DATA_HOME"] == str(directory)
            (directory / "save.json").write_text("temporary progress")
            raise RuntimeError("failed replay")
    assert not directory.exists()
    assert os.environ["XDG_DATA_HOME"] == str(original)
    assert os.environ.get("HOME") == before_home
    assert (original / "save.json").read_text() == "existing collaborator progress"


def test_unset_xdg_is_unset_after_feature_run(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_SCRATCH_ROOT", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    with fresh_user_data():
        assert os.environ.get("XDG_DATA_HOME")
    assert "XDG_DATA_HOME" not in os.environ


def test_two_feature_sessions_and_every_control_start_from_empty_save(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_SCRATCH_ROOT", str(tmp_path))
    original = tmp_path / "collaborator-data"
    original.mkdir()
    (original / "save.json").write_text("untouched")
    monkeypatch.setenv("XDG_DATA_HOME", str(original))
    observed = []

    class Session:
        def run(self, route, agent, budget, **kwargs):
            directory = Path(os.environ["XDG_DATA_HOME"])
            save = directory / "godot" / "app_userdata" / "same-project" / "save.json"
            assert not save.exists(), "a previous witness/control contaminated this run"
            save.parent.mkdir(parents=True, exist_ok=True)
            save.write_text("progress from " + kwargs["run_tag"])
            observed.append((kwargs["run_tag"], directory))
            return RouteReading(route.route_id, route.tier, segment_clean=True, stop_reason="ops_exhausted")

    interface = SimpleNamespace(extended_action_ids=("interact",), analog_axes=())
    session = Session()
    with patch("evalsys.taskgen.engine.ensure_taskgen_scratch", return_value=tmp_path), \
         patch("evalsys.taskgen.engine.godot_available", return_value=Path("godot")), \
         patch("evalsys.taskgen.engine.godot_version", return_value="fixture"), \
         patch("evalsys.taskgen.engine.prepare_session", return_value=True), \
         patch("evalsys.routes.runner.RouteSession", return_value=session):
        for _ in range(2):
            run_self_play(tmp_path / "same-project", mode=parse_mode("gdd"),
                          ops=[Op("state", frames=30, actions=("gb_right",))],
                          predicate="whole_game_clear()", interface=interface, feature_demo=True)
    assert len(observed) == 14
    assert len({path for _, path in observed}) == 14
    assert all(not path.exists() for _, path in observed)
    assert os.environ["XDG_DATA_HOME"] == str(original)
    assert (original / "save.json").read_text() == "untouched"


def test_legacy_self_play_does_not_change_user_data_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_SCRATCH_ROOT", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", "existing-data-root")

    class Session:
        def run(self, route, agent, budget, **kwargs):
            assert os.environ["XDG_DATA_HOME"] == "existing-data-root"
            return RouteReading(route.route_id, route.tier, segment_clean=True, stop_reason="ops_exhausted")

    with patch("evalsys.taskgen.engine.ensure_taskgen_scratch", return_value=tmp_path), \
         patch("evalsys.taskgen.engine.godot_available", return_value=Path("godot")), \
         patch("evalsys.taskgen.engine.godot_version", return_value="fixture"), \
         patch("evalsys.taskgen.engine.prepare_session", return_value=True), \
         patch("evalsys.routes.runner.RouteSession", return_value=Session()):
        run_self_play(tmp_path / "game", mode=parse_mode("gdd"), ops=[Op("state", frames=30, actions=("gb_right",))],
                      predicate="whole_game_clear()", interface=SimpleNamespace(extended_action_ids=(), analog_axes=()))
    assert not list(tmp_path.glob("feature-user-*"))


def test_filmed_features_also_start_from_empty_user_data(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_SCRATCH_ROOT", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", "existing-data-root")
    seen = []

    class Session:
        def run(self, route, agent, budget, **kwargs):
            directory = Path(os.environ["XDG_DATA_HOME"])
            assert directory.is_dir() and not list(directory.iterdir())
            (directory / "save.json").write_text("filming changed progress")
            seen.append(directory)
            return RouteReading(route.route_id, route.tier, segment_clean=True, stop_reason="ops_exhausted")

    with patch("evalsys.taskgen.replay_film.godot_available", return_value=Path("godot")), \
         patch("evalsys.taskgen.replay_film.film_from_movie", side_effect=lambda movie, directory, **kw: ReplayFilm(directory=str(directory))) as sample:
        for index in range(2):
            film_submission_replay(tmp_path / "game", tmp_path / f"film-{index}",
                                   interface=SimpleNamespace(), ops=[Op("state", frames=30, actions=("gb_right",))],
                                   predicate="whole_game_clear()", session=Session(), feature_demo=True)
        assert all(call.kwargs["sample_interval_s"] == .25 for call in sample.call_args_list)
    assert len(set(seen)) == 2 and all(not path.exists() for path in seen)
    assert os.environ["XDG_DATA_HOME"] == "existing-data-root"


@pytest.mark.skipif(godot_available() is None, reason="Godot engine not installed")
def test_actual_godot_user_store_is_empty_on_two_runs_of_same_project(tmp_path, monkeypatch):
    project = tmp_path / "same-project"
    project.mkdir()
    (project / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Feature Save Isolation"\n')
    (project / "save_probe.gd").write_text(
        'extends SceneTree\n'
        'func _initialize():\n'
        '\tprint("FRESH_SAVE:", int(FileAccess.file_exists("user://progress.txt")))\n'
        '\tvar file = FileAccess.open("user://progress.txt", FileAccess.WRITE)\n'
        '\tfile.store_string("a prior run wrote this")\n'
        '\tfile.close()\n'
        '\tquit()\n'
    )
    monkeypatch.setenv("GB_SCRATCH_ROOT", str(tmp_path / "scratch"))
    for _ in range(2):
        with fresh_user_data() as directory:
            proc = subprocess.run([str(godot_available()), "--headless", "--path", str(project),
                                   "--script", "res://save_probe.gd"],
                                  capture_output=True, text=True, timeout=20)
            assert proc.returncode == 0, proc.stdout + proc.stderr
            assert "FRESH_SAVE:0" in proc.stdout
            assert list(directory.rglob("progress.txt")), "Godot did not use the isolated data directory"
