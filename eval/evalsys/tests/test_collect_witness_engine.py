
import json

import pytest

from evalsys.assertions.universal import u3_collect_removes_one
from evalsys.probe.runner import take_snapshot
from evalsys.taskgen.engine import godot_available
from evalsys.verdict import Verdict


def _project(tmp_path, *, delay=0.0, succeeds=1, decoy=False):
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Collect witness regression"\n'
        'run/main_scene="res://main.tscn"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n')
    (project / "gb_levels.json").write_text(json.dumps({"levels": ["res://main.tscn"]}))
    (project / "main.tscn").write_text(
        '[gd_scene load_steps=2 format=3]\n'
        '[ext_resource type="Script" path="res://main.gd" id="1"]\n'
        '[node name="Main" type="Node2D"]\nscript = ExtResource("1")\n')
    (project / "main.gd").write_text('''extends Node2D
var pickups: Array[Area2D] = []
var triggered := {}
func _ready() -> void:
    var player := CharacterBody2D.new()
    player.name = "Player"
    player.position = Vector2(-200, 0)
    var body := CollisionShape2D.new()
    var body_shape := RectangleShape2D.new()
    body_shape.size = Vector2(10, 10)
    body.shape = body_shape
    player.add_child(body)
    add_child(player)
    player.add_to_group("gb_player")
    for i in 2:
        var target := Area2D.new()
        target.name = "Pickup%d" % i
        target.position = Vector2(100 + i * 150, 0)
        var shape := CollisionShape2D.new()
        var rect := RectangleShape2D.new()
        rect.size = Vector2(30, 30)
        shape.shape = rect
        target.add_child(shape)
        add_child(target)
        target.add_to_group("gb_collectible")
        target.body_entered.connect(_on_entered.bind(i))
        pickups.append(target)
func _on_entered(_body: Node2D, index: int) -> void:
    if triggered.has(index):
        return
    triggered[index] = true
    if DECOY and index == 0:
        pickups[1].queue_free()
    if index != SUCCEEDS:
        return
    if DELAY > 0:
        await get_tree().create_timer(DELAY).timeout
    pickups[index].queue_free()
'''.replace("DECOY", str(decoy).lower()).replace("SUCCEEDS", str(succeeds)).replace("DELAY", str(delay)))
    return project


@pytest.mark.skipif(godot_available() is None, reason="Godot required for real target observation")
def test_first_miss_later_success_keeps_that_targets_counts(tmp_path):
    game = _project(tmp_path)
    truth = take_snapshot(game, dest=tmp_path / "probe", with_liveness=False)
    collect = truth.levels[0].collect
    assert u3_collect_removes_one(truth)[0].verdict == Verdict.PASSED
    assert collect.target == "Pickup1"
    assert collect.alive_before == 2 and collect.alive_after == 1
    assert collect.effect["cycle_index"] == 1


@pytest.mark.skipif(godot_available() is None, reason="Godot required for real delayed observation")
def test_delayed_pickup_requires_full_observation_window(tmp_path):
    game = _project(tmp_path, delay=1.0, succeeds=0)
    short = take_snapshot(game, dest=tmp_path / "short", with_liveness=False, max_cycles=1)
    assert u3_collect_removes_one(short)[0].verdict == Verdict.UNOBSERVABLE
    long = take_snapshot(game, dest=tmp_path / "long", with_liveness=False,
                         max_cycles=1, task_id="ember_and_tide")
    assert u3_collect_removes_one(long)[0].verdict == Verdict.PASSED
    assert long.levels[0].collect.target == "Pickup0"


@pytest.mark.skipif(godot_available() is None, reason="Godot required for target-specific negative")
def test_unrelated_node_removal_does_not_prove_target_effect(tmp_path):
    game = _project(tmp_path, succeeds=-1, decoy=True)
    truth = take_snapshot(game, dest=tmp_path / "probe", with_liveness=False)
    collect = truth.levels[0].collect
    assert collect.delta == -1
    assert collect.target == "Pickup0" and not collect.effect["observed"]
    assert u3_collect_removes_one(truth)[0].verdict == Verdict.UNOBSERVABLE
