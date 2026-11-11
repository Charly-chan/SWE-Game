from __future__ import annotations
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
EVAL_ROOT = Path(__file__).resolve().parents[2]
_RECORD_DIR = EVAL_ROOT / 'tools' / 'gates'
if str(_RECORD_DIR) not in sys.path:
    sys.path.insert(0, str(_RECORD_DIR))
from harness_scan import film_refusal, injected_autoload_names, scan_auto_win_ready, scan_project
INJECTION_SITE_PATTERNS = (re.compile('(GB\\w+)=\\\\?"\\*res://'), re.compile('autoload_name\\s*=\\s*["\\\'](GB\\w+)["\\\']'), re.compile('inject_autoload\\([^)]*,\\s*["\\\'](GB\\w+)["\\\']\\s*\\)'), re.compile('_AUTOLOAD\\s*(?::\\s*str\\s*)?=\\s*["\\\'](GB\\w+)["\\\']'))

def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')

def _project(root: Path, *, main: str | None=None, autoloads: list[tuple[str, str]] | None=None) -> None:
    lines = ['[application]\nconfig/name="fixture"\n']
    if main:
        lines.append(f'run/main_scene="res://{main}"\n')
    if autoloads:
        lines.append('\n[autoload]\n\n')
        for name, path in autoloads:
            lines.append(f'{name}="*{path}"\n')
    (root / 'project.godot').write_text(''.join(lines), encoding='utf-8')
GRANT_BODY = 'extends Node\nfunc _ready():\n    if "--gb-route-plan" in OS.get_cmdline_user_args():\n        model.health = 1000000\n'

class HarnessScanTests(unittest.TestCase):

    def test_grant_in_loaded_gameplay_blocks_film(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'world/level.gd', GRANT_BODY)
            _project(root, autoloads=[('Level', 'res://world/level.gd')])
            scan = scan_project(root)
            self.assertTrue(scan.blocks_film)
            self.assertEqual(scan.authenticity(), 'harness_granted')
            self.assertIsNotNone(film_refusal(scan))
            self.assertIsNone(film_refusal(scan, acknowledged_reason='L5 re-film after fix'))

    def test_unreferenced_tools_grant_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'play.gd', 'extends Node\n')
            _write(root, 'tools/cheat.gd', GRANT_BODY)
            _project(root, autoloads=[('Play', 'res://play.gd')])
            scan = scan_project(root)
            self.assertFalse(scan.blocks_film)
            self.assertEqual(scan.authenticity(), 'no_harness_flag_in_gameplay')

    def test_autoload_in_bridge_dir_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'bridge/agent_bridge.gd', GRANT_BODY)
            _project(root, autoloads=[('AgentBridge', 'res://bridge/agent_bridge.gd')])
            scan = scan_project(root)
            self.assertTrue(scan.blocks_film)
            self.assertEqual(scan.hits[0].path, 'bridge/agent_bridge.gd')

    def test_unreferenced_file_is_ignored_even_in_world(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'play.gd', 'extends Node\n')
            _write(root, 'world/orphan_grant.gd', GRANT_BODY)
            _project(root, autoloads=[('Play', 'res://play.gd')])
            scan = scan_project(root)
            self.assertFalse(scan.hits)

    def test_autoload_in_tools_dir_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'tools/cheat.gd', GRANT_BODY)
            _project(root, autoloads=[('Cheat', 'res://tools/cheat.gd')])
            scan = scan_project(root)
            self.assertTrue(scan.blocks_film)

    def test_seed_concession_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'world/floor.gd', 'if "--gb-route-plan" in OS.get_cmdline_user_args() and route_shift_seed >= 0:\n    RunState.initialize_route_shift(RunState.route_shift_seed)\n')
            _project(root, autoloads=[('Floor', 'res://world/floor.gd')])
            scan = scan_project(root)
            self.assertTrue(scan.harness_flag_hits)
            self.assertFalse(scan.blocks_film)
            self.assertEqual(scan.authenticity(), 'harness_concessions_only')
            self.assertIsNone(film_refusal(scan))

    def test_unclassified_harness_flag_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'actors/player.gd', 'if "--gb-route-plan" in OS.get_cmdline_user_args():\n    do_the_special_thing()\n')
            _project(root, autoloads=[('Player', 'res://actors/player.gd')])
            scan = scan_project(root)
            self.assertEqual(scan.grant_hits[0].classification, 'unknown')
            self.assertTrue(scan.blocks_film)

    def test_other_cli_is_visible_and_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'core/scene_manager.gd', 'func _bot_mode():\n    for a in OS.get_cmdline_user_args():\n        if a.begins_with("--bot="):\n            return a\n    return ""\n')
            _project(root, autoloads=[('SM', 'res://core/scene_manager.gd')])
            scan = scan_project(root)
            self.assertEqual(scan.hits[0].kind, 'other_cli')
            self.assertFalse(scan.blocks_film)

    def test_node_probe_direct_branch_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'core/globals/day_night.gd', 'extends Node\n\nfunc _process(delta: float) -> void:\n\tif get_node_or_null("/root/GBRouteDriver") != null or get_node_or_null("/root/GBRuntimeProbe") != null:\n\t\treturn\n\tgame_time += delta * const_game_time_per_minute\n\tupdate_time()\n\tif Ledger.started and current_hour >= Ledger.economy.daytime_end_hour:\n\t\tLedger.request_end_day("clock")\n')
            _project(root, autoloads=[('DayNight', 'res://core/globals/day_night.gd')])
            scan = scan_project(root)
            self.assertEqual(len(scan.node_probe_hits), 1)
            hit = scan.node_probe_hits[0]
            self.assertEqual(hit.path, 'core/globals/day_night.gd')
            self.assertEqual(hit.line, 4)
            self.assertIn(hit.classification, ('grant', 'unknown'))
            self.assertTrue(scan.blocks_film)
            self.assertEqual(scan.authenticity(), 'node_probe_granted')
            self.assertIn('injected evaluator autoload', film_refusal(scan) or '')

    def test_node_probe_behind_a_wrapper_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'core/run_state.gd', 'extends Node\n\nfunc route_session() -> bool:\n\treturn get_node_or_null("/root/GBRouteDriver") != null\n')
            _write(root, 'ui/shift_results.gd', 'extends Control\n\nfunc _process(_delta: float) -> void:\n\tif not RunState.route_session():\n\t\treturn\n\tShop.buy_next_fixture()\n\tget_tree().change_scene_to_file("res://world/shop_floor.tscn")\n')
            _project(root, autoloads=[('RunState', 'res://core/run_state.gd')], main='ui/shift_results.gd')
            scan = scan_project(root)
            self.assertEqual([(h.path, h.line) for h in scan.node_probe_hits], [('core/run_state.gd', 4)])
            self.assertEqual(scan.node_probe_hits[0].classification, 'unknown')
            self.assertTrue(scan.blocks_film)

    def test_node_probe_on_render_path_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'core/shots.gd', 'extends Node\n\nfunc grab() -> void:\n\tif get_node_or_null("/root/GBCaptureProbe") != null:\n\t\treturn\n\tvar image := get_viewport().get_texture().get_image()\n\timage.save_png("user://shot.png")\n')
            _project(root, autoloads=[('Shots', 'res://core/shots.gd')])
            scan = scan_project(root)
            self.assertEqual(len(scan.node_probe_hits), 1)
            self.assertEqual(scan.node_probe_hits[0].classification, 'concession')
            self.assertFalse(scan.node_probe_grant_hits)
            self.assertFalse(scan.blocks_film)
            self.assertIsNone(film_refusal(scan))

    def test_node_probe_axis_interface_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'actors/player.gd', 'extends CharacterBody3D\n\nfunc _apply_axis_pitch(delta: float) -> void:\n\tvar probe := get_node_or_null("/root/GBHarnessProbe")\n\tif probe == null:\n\t\treturn\n\tvar ly := float(probe.axis_value("look_y"))\n\t_camera_rig.pitch_by(-ly * Tuning.AXIS_PITCH_DEG_PER_SEC * delta)\n')
            _project(root, autoloads=[('Player', 'res://actors/player.gd')])
            scan = scan_project(root)
            self.assertEqual(scan.node_probe_hits[0].classification, 'concession')
            self.assertFalse(scan.blocks_film)

    def test_node_probe_with_grant_body_is_a_grant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'actors/player.gd', 'extends Node\n\nfunc _ready() -> void:\n\tif get_node_or_null("/root/GBRouteDriver") != null:\n\t\tmodel.health = 1000000\n')
            _project(root, autoloads=[('Player', 'res://actors/player.gd')])
            scan = scan_project(root)
            self.assertEqual(scan.node_probe_hits[0].classification, 'grant')
            self.assertTrue(scan.blocks_film)

    def test_node_probe_in_comment_is_not_a_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'bot/mode_base.gd', 'extends RefCounted\n## When true, emit like GBRouteDriver -> probe.press(action, false):\n## action_press/release + InputEventAction, no mapped key.\nvar harness_style_input := false\n')
            _project(root, autoloads=[('Bot', 'res://bot/mode_base.gd')])
            scan = scan_project(root)
            self.assertFalse(scan.hits)

    def test_naming_a_node_after_the_probe_is_not_a_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'tests/mode_axis_reach.gd', 'extends Node\n\nfunc _ready() -> void:\n\tvar axis := Node.new()\n\taxis.name = "GBHarnessProbe"\n\tget_tree().root.add_child(axis)\n')
            _project(root, autoloads=[('Reach', 'res://tests/mode_axis_reach.gd')])
            scan = scan_project(root)
            self.assertFalse(scan.node_probe_hits)
            self.assertFalse(scan.blocks_film)

    def test_node_lookup_split_across_lines_is_still_a_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'core/run_state.gd', 'extends Node\n\nfunc route_session() -> bool:\n\treturn get_node_or_null(\n\t\t"/root/GBRouteDriver"\n\t) != null\n')
            _project(root, autoloads=[('RunState', 'res://core/run_state.gd')])
            scan = scan_project(root)
            self.assertEqual([(h.path, h.line) for h in scan.node_probe_hits], [('core/run_state.gd', 5)])
            self.assertTrue(scan.blocks_film)

    def test_injected_autoload_registry_covers_every_injection_site(self) -> None:
        registry = injected_autoload_names()
        spliced: dict[str, str] = {}
        for path in sorted(EVAL_ROOT.rglob('*.py')):
            if '__pycache__' in path.parts or path.name.startswith('test_'):
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
            for pattern in INJECTION_SITE_PATTERNS:
                for name in pattern.findall(text):
                    spliced.setdefault(name, str(path.relative_to(EVAL_ROOT)))
        self.assertTrue(spliced, 'found no injection sites at all; the patterns rotted')
        for name, where in sorted(spliced.items()):
            self.assertIn(name, registry, f'{name} is injected by {where}')

    def test_env_read_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'core/audio.gd', 'var muted = OS.get_environment("GB_MUTE_AUDIO") == "1"\n')
            _project(root, autoloads=[('Audio', 'res://core/audio.gd')])
            scan = scan_project(root)
            self.assertEqual(scan.hits[0].kind, 'env_read')
            self.assertFalse(scan.blocks_film)

    def test_scene_reference_pulls_attached_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'scenes/main.tscn', '[gd_scene load_steps=2 format=3]\n\n[ext_resource type="Script" path="res://world/level.gd" id="1"]\n')
            _write(root, 'world/level.gd', GRANT_BODY)
            _project(root, main='scenes/main.tscn')
            scan = scan_project(root)
            self.assertTrue(scan.blocks_film)

    def test_class_name_reference_is_followed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'play.gd', 'extends Node\nfunc _ready():\n    var motor = ParkourMotor.new()\n')
            _write(root, 'actors/parkour_motor.gd', 'class_name ParkourMotor\n' + GRANT_BODY)
            _project(root, autoloads=[('Play', 'res://play.gd')])
            scan = scan_project(root)
            self.assertTrue(scan.blocks_film)
            self.assertEqual(scan.hits[0].path, 'actors/parkour_motor.gd')

    def test_unmentioned_class_name_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'play.gd', 'extends Node\n')
            _write(root, 'actors/cheat.gd', 'class_name CheatMotor\n' + GRANT_BODY)
            _project(root, autoloads=[('Play', 'res://play.gd')])
            scan = scan_project(root)
            self.assertFalse(scan.blocks_film)

    def test_comment_only_flag_is_not_a_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'world/world.gd', 'extends Node\n# This used to detect `--gb-route-plan` and mark the first six objectives\n')
            _project(root, autoloads=[('World', 'res://world/world.gd')])
            scan = scan_project(root)
            self.assertFalse(scan.harness_flag_hits)
            self.assertFalse(scan.blocks_film)

    def test_json_manifest_pulls_listed_scenes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'core/router.gd', 'const MANIFEST := "res://data/levels.json"\n')
            _write(root, 'data/levels.json', '{"levels": ["res://scenes/level_01.tscn"]}\n')
            _write(root, 'scenes/level_01.tscn', '[gd_scene load_steps=2 format=3]\n\n[ext_resource type="Script" path="res://world/level.gd" id="1"]\n')
            _write(root, 'world/level.gd', GRANT_BODY)
            _project(root, autoloads=[('Router', 'res://core/router.gd')])
            scan = scan_project(root)
            self.assertTrue(scan.blocks_film)
            self.assertEqual(scan.hits[0].path, 'world/level.gd')

    def test_no_project_godot_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'world/level.gd', GRANT_BODY)
            scan = scan_project(root)
            self.assertTrue(scan.blocks_film)

    def test_audio_log_and_only_are_not_harness_grants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'core/audio.gd', 'func _ready():\n    for a in OS.get_cmdline_user_args():\n        if a == "--audio-log" or a.begins_with("--no-trim") or a.begins_with("--only="):\n            log_audio = true\n')
            _project(root, autoloads=[('Audio', 'res://core/audio.gd')])
            scan = scan_project(root)
            self.assertTrue(scan.hits)
            self.assertFalse(scan.harness_flag_hits)
            self.assertFalse(scan.blocks_film)
            self.assertEqual(scan.hits[0].kind, 'other_cli')

class AutoWinReadyTests(unittest.TestCase):

    def _levels(self, root: Path, *, victory: str='res://ui/win.tscn', defeat: str='res://ui/lose.tscn', extra: dict | None=None) -> None:
        endings = {'victory': victory, 'defeat': defeat}
        if extra:
            endings.update(extra)
        _write(root, 'gb_levels.json', json.dumps({'levels': ['res://play.tscn'], 'endings': endings}))

    def test_ready_jump_to_declared_victory_blocks_film(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._levels(root)
            _write(root, 'boot.gd', 'extends Node\nfunc _ready() -> void:\n    get_tree().change_scene_to_file("res://ui/win.tscn")\n')
            _project(root, autoloads=[('Boot', 'res://boot.gd')])
            scan = scan_project(root)
            self.assertTrue(scan.blocks_film)
            self.assertEqual(scan.authenticity(), 'auto_win_ready')
            self.assertEqual(scan.auto_win_hits[0].kind, 'auto_win_ready')
            self.assertEqual(scan.auto_win_hits[0].classification, 'grant')
            self.assertEqual(scan_auto_win_ready(root), scan.auto_win_hits)
            self.assertIsNotNone(film_refusal(scan))

    def test_ready_jump_via_const_alias_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._levels(root, victory='res://scenes/results.tscn')
            _write(root, 'core/level_catalog.gd', 'class_name LevelCatalog\nconst RESULTS_SCENE := "res://scenes/results.tscn"\nconst FAILED_SCENE := "res://scenes/stage_failed.tscn"\n')
            _write(root, 'world/level_root.gd', 'extends Node\nfunc _ready() -> void:\n    _tick_world(0.0)\n    if level_index == 1:\n        get_tree().change_scene_to_file(LevelCatalog.RESULTS_SCENE)\n')
            _project(root, autoloads=[('Catalog', 'res://core/level_catalog.gd'), ('Level', 'res://world/level_root.gd')])
            scan = scan_project(root)
            self.assertTrue(scan.auto_win_hits)
            self.assertTrue(scan.blocks_film)
            self.assertIn('res://scenes/results.tscn', scan.auto_win_hits[0].why)

    def test_ready_jump_to_defeat_is_not_auto_win(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._levels(root)
            _write(root, 'boot.gd', 'extends Node\nfunc _ready() -> void:\n    get_tree().change_scene_to_file("res://ui/lose.tscn")\n')
            _project(root, autoloads=[('Boot', 'res://boot.gd')])
            scan = scan_project(root)
            self.assertFalse(scan.auto_win_hits)
            self.assertFalse(scan.blocks_film)

    def test_button_lambda_in_ready_is_not_a_boot_jump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._levels(root)
            _write(root, 'menu.gd', 'extends Node\nfunc _ready() -> void:\n    play.pressed.connect(func() -> void:\n        get_tree().change_scene_to_file("res://ui/win.tscn"))\n')
            _project(root, autoloads=[('Menu', 'res://menu.gd')])
            scan = scan_project(root)
            self.assertFalse(scan.auto_win_hits)

    def test_unreferenced_auto_win_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._levels(root)
            _write(root, 'play.gd', 'extends Node\n')
            _write(root, 'orphan_boot.gd', 'extends Node\nfunc _ready() -> void:\n    get_tree().change_scene_to_file("res://ui/win.tscn")\n')
            _project(root, autoloads=[('Play', 'res://play.gd')])
            scan = scan_project(root)
            self.assertFalse(scan.auto_win_hits)
            self.assertFalse(scan_auto_win_ready(root))

    def test_no_gb_levels_means_auto_win_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'boot.gd', 'extends Node\nfunc _ready() -> void:\n    get_tree().change_scene_to_file("res://ui/win.tscn")\n')
            _project(root, autoloads=[('Boot', 'res://boot.gd')])
            scan = scan_project(root)
            self.assertFalse(scan.auto_win_hits)

class EnvHarnessTests(unittest.TestCase):

    def test_gb_route_env_grant_blocks_like_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'world/level.gd', 'extends Node\nfunc _ready():\n    if OS.get_environment("GB_ROUTE_PLAN") != "":\n        model.health = 1000000\n')
            _project(root, autoloads=[('Level', 'res://world/level.gd')])
            scan = scan_project(root)
            self.assertEqual(scan.hits[0].kind, 'harness_flag')
            self.assertEqual(scan.hits[0].classification, 'grant')
            self.assertTrue(scan.blocks_film)
            self.assertEqual(scan.authenticity(), 'harness_granted')

    def test_gb_route_out_capture_write_is_concession(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'bot/capture.gd', 'extends Node\nfunc _ready():\n    capture_dir = OS.get_environment("GB_CAPTURE_DIR")\n    _route_path = OS.get_environment("GB_ROUTE_OUT")\n')
            _project(root, autoloads=[('Capture', 'res://bot/capture.gd')])
            scan = scan_project(root)
            route = [h for h in scan.hits if 'GB_ROUTE_OUT' in h.snippet]
            self.assertTrue(route)
            self.assertEqual(route[0].kind, 'harness_flag')
            self.assertEqual(route[0].classification, 'concession')
            self.assertFalse(scan.blocks_film)
            self.assertEqual(scan.authenticity(), 'harness_concessions_only')

    def test_probe_shaped_asset_sampling_is_a_concession(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'core/observer.gd', 'extends Node\nvar _asset_sampling_enabled: bool = false\nfunc _ready() -> void:\n    _scan_collectibles(false)\n    _observe_setting_resources()\n    _asset_sampling_enabled = OS.get_environment("GB_ASSET_SAMPLING") == "1"\nfunc _interface_arg(key: String) -> String:\n    return key\nfunc sample_asset_use() -> void:\n    pass\nconst ASSET_SAMPLE_INTERVAL_FRAMES := 30\nfunc group_census() -> Dictionary:\n    return {}\nfunc world_bounds() -> Dictionary:\n    return {}\n')
            _project(root, autoloads=[('Observer', 'res://core/observer.gd')])
            scan = scan_project(root)
            hit = next((h for h in scan.hits if 'GB_ASSET_SAMPLING' in h.snippet))
            self.assertEqual(hit.kind, 'harness_flag')
            self.assertEqual(hit.classification, 'concession')
            self.assertFalse(scan.blocks_film)
            self.assertEqual(scan.authenticity(), 'harness_concessions_only')

    def test_bare_asset_sampling_read_is_unknown_and_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'player.gd', 'extends Node\nfunc _ready() -> void:\n    var sampling = OS.get_environment("GB_ASSET_SAMPLING") == "1"\n')
            _project(root, autoloads=[('Player', 'res://player.gd')])
            scan = scan_project(root)
            self.assertEqual(scan.hits[0].kind, 'harness_flag')
            self.assertEqual(scan.hits[0].classification, 'unknown')
            self.assertTrue(scan.blocks_film)
            self.assertEqual(scan.authenticity(), 'harness_granted')

    def test_asset_sampling_gated_texture_swap_is_a_grant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'world/skin.gd', 'extends Node\nfunc _ready() -> void:\n    if OS.get_environment("GB_ASSET_SAMPLING") == "1":\n        $Mesh.mesh = load("res://hq.tres")\n        $Sprite.texture = preload("res://pretty.png")\n')
            _project(root, autoloads=[('Skin', 'res://world/skin.gd')])
            scan = scan_project(root)
            self.assertEqual(scan.hits[0].kind, 'harness_flag')
            self.assertEqual(scan.hits[0].classification, 'grant')
            self.assertTrue(scan.blocks_film)
            self.assertEqual(scan.authenticity(), 'harness_granted')

    def test_capture_dir_stays_env_read_unless_grant_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'bot/capture.gd', 'extends Node\nfunc _ready():\n    capture_dir = OS.get_environment("GB_CAPTURE_DIR")\n')
            _project(root, autoloads=[('Capture', 'res://bot/capture.gd')])
            scan = scan_project(root)
            self.assertEqual(scan.hits[0].kind, 'env_read')
            self.assertFalse(scan.blocks_film)
            self.assertEqual(scan.authenticity(), 'no_harness_flag_in_gameplay')

    def test_autopilot_gated_health_pack_is_a_grant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'bot/autopilot.gd', 'extends Node\nfunc _ready():\n    if OS.get_environment("GB_AUTOPILOT") == "1":\n        model.health = 1000000\n')
            _project(root, autoloads=[('Auto', 'res://bot/autopilot.gd')])
            scan = scan_project(root)
            self.assertEqual(scan.hits[0].kind, 'harness_flag')
            self.assertEqual(scan.hits[0].classification, 'grant')
            self.assertTrue(scan.blocks_film)

    def test_same_statement_guards_are_a_hint_not_a_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'scripts/session.gd', 'extends Node\nfunc begin_stage(number: int) -> void:\n    route_direct_stage_start = "--gb-route-plan" in OS.get_cmdline_user_args() \\\n            and number > 1 and score == 0 and cumulative_kills == 0\n    if route_direct_stage_start:\n        score = 10700\n        weapon_level = 2\n')
            _project(root, autoloads=[('Session', 'res://scripts/session.gd')])
            scan = scan_project(root)
            hit = scan.grant_hits[0]
            self.assertEqual(hit.classification, 'grant')
            self.assertTrue(scan.blocks_film)
            self.assertIn('G-L5-REACH', hit.why)
            self.assertIn('score == 0', hit.why)
            self.assertIn('cumulative_kills == 0', hit.why)
            self.assertIn('not an L5 reachability verdict', hit.why)

    def test_guards_on_earlier_continuation_lines_are_included(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'actors/player.gd', 'extends Node\nfunc _physics_process(delta):\n    if inp.jump_pressed \\\n            and "--gb-route-plan" in OS.get_cmdline_user_args() \\\n            and scene_path.ends_with("shaft.tscn"):\n        velocity.y = minf(velocity.y, -1100.0)\n        global_position += Vector2.UP\n')
            _project(root, autoloads=[('Player', 'res://actors/player.gd')])
            scan = scan_project(root)
            hit = scan.grant_hits[0]
            self.assertIn('inp.jump_pressed', hit.why)
            self.assertIn('shaft.tscn', hit.why)
            self.assertEqual(hit.classification, 'grant')

class CSharpScanTests(unittest.TestCase):
    GRANT_CS = 'using Godot;\npublic partial class Level : Node {\n    public override void _Ready() {\n        if (OS.GetCmdlineUserArgs().Contains("--gb-route-plan")) {\n            health = 1000000;\n        }\n    }\n}\n'

    def test_cs_argv_grant_on_autoload_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'Level.cs', self.GRANT_CS)
            _project(root, autoloads=[('Level', 'res://Level.cs')])
            scan = scan_project(root)
            self.assertTrue(scan.blocks_film)
            self.assertEqual(scan.hits[0].path, 'Level.cs')
            self.assertEqual(scan.hits[0].kind, 'harness_flag')

    def test_cs_env_route_grant_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'Level.cs', 'using Godot;\npublic partial class Level : Node {\n    public override void _Ready() {\n        if (OS.GetEnvironment("GB_ROUTE_PLAN") != "") {\n            health = 1000000;\n        }\n    }\n}\n')
            _project(root, autoloads=[('Level', 'res://Level.cs')])
            scan = scan_project(root)
            self.assertTrue(scan.blocks_film)
            self.assertEqual(scan.hits[0].kind, 'harness_flag')

    def test_cs_ready_auto_win_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'gb_levels.json', json.dumps({'levels': ['res://Main.tscn'], 'endings': {'victory': 'res://Win.tscn', 'defeat': 'res://Lose.tscn'}}))
            _write(root, 'Boot.cs', 'using Godot;\npublic partial class Boot : Node {\n    public override void _Ready() {\n        GetTree().ChangeSceneToFile("res://Win.tscn");\n    }\n}\n')
            _project(root, autoloads=[('Boot', 'res://Boot.cs')])
            scan = scan_project(root)
            self.assertTrue(scan.auto_win_hits)
            self.assertTrue(scan.blocks_film)

    def test_cs_generic_node_lookup_is_a_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'Clock.cs', 'using Godot;\npublic partial class Clock : Node {\n  public override void _Process(double delta) {\n    if (GetNodeOrNull<Node>("/root/GBRouteDriver") != null) return;\n    GameTime += delta;\n  }\n}\n')
            _project(root, autoloads=[('Clock', 'res://Clock.cs')])
            scan = scan_project(root)
            self.assertEqual(len(scan.node_probe_hits), 1)
            self.assertTrue(scan.blocks_film)

    def test_cs_comment_is_not_a_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'Level.cs', 'using Godot;\npublic partial class Level : Node {\n    // do not check OS.GetCmdlineUserArgs for --gb-route-plan\n    /* health = 1000000 when GB_ROUTE_PLAN is set */\n    public override void _Ready() {}\n}\n')
            _project(root, autoloads=[('Level', 'res://Level.cs')])
            scan = scan_project(root)
            self.assertFalse(scan.harness_flag_hits)
            self.assertFalse(scan.blocks_film)

    def test_tscn_pulls_attached_cs_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'scenes/main.tscn', '[gd_scene load_steps=2 format=3]\n\n[ext_resource type="Script" path="res://Level.cs" id="1"]\n')
            _write(root, 'Level.cs', self.GRANT_CS)
            _project(root, main='scenes/main.tscn')
            scan = scan_project(root)
            self.assertTrue(scan.blocks_film)

    def test_cs_class_reference_without_res_path_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'Play.cs', 'using Godot;\npublic partial class Play : Node {\n    public override void _Ready() { var cheat = new CheatMotor(); }\n}\n')
            _write(root, 'CheatMotor.cs', self.GRANT_CS.replace('class Level', 'class CheatMotor'))
            _project(root, autoloads=[('Play', 'res://Play.cs')])
            scan = scan_project(root)
            self.assertFalse(scan.blocks_film)
            self.assertFalse(scan.hits)

    def test_unreferenced_cs_grant_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'play.gd', 'extends Node\n')
            _write(root, 'Cheat.cs', self.GRANT_CS)
            _project(root, autoloads=[('Play', 'res://play.gd')])
            scan = scan_project(root)
            self.assertFalse(scan.blocks_film)
if __name__ == '__main__':
    unittest.main()
