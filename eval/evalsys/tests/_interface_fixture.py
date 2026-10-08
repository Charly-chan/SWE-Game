from __future__ import annotations

import json
from pathlib import Path

from evalsys.interface.contract import ACTIONS, GROUPS


def write_conformant_project(root: Path, *, observables: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    actions = "[input]\n\n" + "\n".join(
        f'{name}={{\n"deadzone": 0.5,\n"events": [Object()]\n}}'
        for name in ACTIONS
    )
    (root / "project.godot").write_text(
        '[application]\nrun/main_scene="res://level.tscn"\n' + actions,
        encoding="utf-8",
    )
    (root / "level.tscn").write_text(
        '[gd_scene format=3]\n[node name="Player" type="Node" groups=["gb_player"]]\n'
        + "\n".join(f"; vocabulary {name}" for name in GROUPS),
        encoding="utf-8",
    )
    (root / "player.gd").write_text(
        "\n".join(
            f'func _poll_{name}(): return Input.is_action_pressed("{name}")'
            for name in ACTIONS
        ),
        encoding="utf-8",
    )
    (root / "win.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
    (root / "lose.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
    manifest = {
        "levels": ["res://level.tscn"],
        "endings": {"victory": "res://win.tscn", "defeat": "res://lose.tscn"},
        "numeric": {"progress": "game_progress"},
    }
    if observables:
        manifest.update({
            "anchor_camera": "res://level.tscn::Camera",
            "audio_buses": ["Master"],
        })
    (root / "gb_levels.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def read_manifest(root: Path) -> dict:
    return json.loads((root / "gb_levels.json").read_text(encoding="utf-8"))


def write_manifest(root: Path, value: dict) -> None:
    (root / "gb_levels.json").write_text(json.dumps(value), encoding="utf-8")
