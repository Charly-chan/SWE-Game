

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evalsys.taskgen.antigrant import (
    Finding,
    ScanReport,
    harness_scan_path,
    is_blocking_finding,
    new_blocking_sites,
    scan_auto_win_ready,
    scan_project,
    scan_tree,
)
from evalsys.interface.contract import ACTIONS
from evalsys.tasks import eval_root

from _interface_fixture import write_conformant_project

GRANT_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _register_autoload(root: Path, name: str, rel: str) -> None:

    path = root / "project.godot"
    text = path.read_text(encoding="utf-8")
    if "[autoload]" not in text:
        text = text.rstrip() + f'\n\n[autoload]\n{name}="*res://{rel}"\n'
    else:
        text = text.rstrip() + f'\n{name}="*res://{rel}"\n'
    path.write_text(text, encoding="utf-8")


class AntiGrantScanTests(unittest.TestCase):
    def test_shared_module_is_on_disk(self) -> None:
        path = harness_scan_path()
        self.assertTrue(path.is_file(), path)
        self.assertEqual("harness_scan.py", path.name)

    def test_clean_fixture_is_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            report = scan_tree(root)
            self.assertTrue(report.ok, report.to_dict())
            self.assertEqual("load_graph", report.inclusion)

    def test_corpus_pattern_is_a_finding_when_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            (root / "grant.gd").write_text(
                'extends Node\n'
                'func _physics_process(_d):\n'
                '\tif "--gb-route-plan" in OS.get_cmdline_user_args():\n'
                '\t\tmodel.health = 1000000\n',
                encoding="utf-8",
            )
            _register_autoload(root, "Grant", "grant.gd")
            report = scan_tree(root)
            kinds = {f.kind for f in report.findings}
            self.assertIn("harness_flag", kinds, report.to_dict())
            self.assertTrue(report.blocks_film, report.to_dict())
            self.assertFalse(report.ok)

    def test_orphan_grant_is_out_of_scope(self) -> None:

        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            (root / "grant.gd").write_text(
                'extends Node\n'
                'func _ready():\n'
                '\tif "--gb-route-plan" in OS.get_cmdline_user_args():\n'
                '\t\tmodel.health = 1000000\n',
                encoding="utf-8",
            )
            report = scan_tree(root)
            self.assertTrue(report.ok, report.to_dict())

    def test_env_read_without_harness_key_is_not_a_harness_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            (root / "locale.gd").write_text(
                'extends Node\nfunc _ready():\n\tvar lang = OS.get_environment("LANG")\n',
                encoding="utf-8",
            )
            _register_autoload(root, "Locale", "locale.gd")
            report = scan_tree(root)
            self.assertTrue(report.ok, [f.to_dict() for f in report.findings])
            kinds = {f.kind for f in report.findings}
            self.assertTrue(not kinds or kinds <= {"env_read", "other_cli"}, kinds)

    def test_orphan_ready_auto_win_is_out_of_scope(self) -> None:


        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            (root / "boot.gd").write_text(
                'extends Node\n'
                'func _ready() -> void:\n'
                '\tget_tree().change_scene_to_file("res://win.tscn")\n',
                encoding="utf-8",
            )
            ready = scan_auto_win_ready(root)
            self.assertEqual([], ready)
            report = scan_tree(root)
            self.assertTrue(report.ok, report.to_dict())
            self.assertFalse(report.blocks_film)
            self.assertNotIn("auto_win_ready", {f.kind for f in report.findings})

    def test_autoload_ready_auto_win_blocks(self) -> None:

        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            (root / "boot.gd").write_text(
                'extends Node\n'
                'func _ready() -> void:\n'
                '\tget_tree().change_scene_to_file("res://win.tscn")\n',
                encoding="utf-8",
            )
            _register_autoload(root, "Boot", "boot.gd")
            ready = scan_auto_win_ready(root)
            self.assertIn("auto_win_ready", {f.kind for f in ready})
            report = scan_tree(root)
            self.assertIn("auto_win_ready", {f.kind for f in report.findings})
            self.assertTrue(report.blocks_film, report.to_dict())
            self.assertFalse(report.ok)
            self.assertEqual("auto_win_ready", report.authenticity)

    def test_comments_are_not_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            (root / "note.gd").write_text(
                "extends Node\n# do not check OS.get_cmdline_user_args for --gb-route-plan\n",
                encoding="utf-8",
            )
            _register_autoload(root, "Note", "note.gd")
            report = scan_tree(root)
            self.assertTrue(report.ok, [f.to_dict() for f in report.findings])

    def test_limits_are_on_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            report = scan_tree(root)
            self.assertTrue(any("concatenated" in item for item in report.limits))

    def test_recorded_corpus_grant_shape_is_detected(self) -> None:


        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            script = root / "world" / "level_root.gd"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(
                (GRANT_FIXTURE_DIR / "route_plan_grant.gd").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            _register_autoload(root, "LevelRoot", "world/level_root.gd")

            report = scan_tree(root)
            flags = [f for f in report.findings if f.kind == "harness_flag"]
            self.assertEqual(
                3, len(flags), report.to_dict()
            )
            self.assertEqual(
                ["world/level_root.gd"] * 3, [f.path for f in flags], report.to_dict()
            )
            self.assertTrue(all(is_blocking_finding(f) for f in flags), report.to_dict())
            self.assertFalse(report.ok, report.to_dict())
            self.assertTrue(report.blocks_film, report.to_dict())
            self.assertEqual("harness_granted", report.authenticity)

            shared = scan_project(root)
            self.assertTrue(shared.blocks_film)

    def test_the_corpus_is_free_of_blocking_grants(self) -> None:


        games_root = eval_root().parent / "games"
        if not games_root.is_dir():
            self.skipTest("games/ is not on disk")
        offenders = {}
        for project in sorted(p for p in games_root.iterdir() if p.is_dir()):
            if not (project / "project.godot").is_file():
                continue
            report = scan_tree(project)
            blocking = [f.to_dict() for f in report.findings if is_blocking_finding(f)]
            if blocking:
                offenders[project.name] = blocking
        self.assertEqual({}, offenders)

    def test_cat_defense_concessions_do_not_fail_ok(self) -> None:

        project = eval_root().parent / "games" / "cat_defense"
        if not (project / "project.godot").is_file():
            self.skipTest("cat_defense is not on disk")
        report = scan_tree(project)
        concessions = [f for f in report.findings if f.classification == "concession"]
        grants = [f for f in report.findings if is_blocking_finding(f)]
        self.assertTrue(concessions, report.to_dict())
        self.assertEqual([], grants, [f.to_dict() for f in grants])
        self.assertTrue(report.ok, report.to_dict())
        self.assertFalse(report.blocks_film)
        self.assertEqual("harness_concessions_only", report.authenticity)

    def test_mode4_reclassified_concession_is_not_a_new_flag(self) -> None:

        current = ScanReport(
            root="fixture",
            findings=[
                Finding(
                    "bot/capture_agent.gd",
                    40,
                    "harness_flag",
                    "concession: GB_ROUTE_OUT",
                    'OS.get_environment("GB_ROUTE_OUT")',
                    classification="concession",
                ),
                Finding(
                    "src/grant.gd",
                    12,
                    "harness_flag",
                    "grant: --gb-route-plan",
                    'if "--gb-route-plan" in OS.get_cmdline_user_args():',
                    classification="grant",
                ),
            ],
        )

        fresh = new_blocking_sites(current, {})
        self.assertEqual(["src/grant.gd"], [f.path for f in fresh])

        inherited = new_blocking_sites(
            current,
            {
                "findings": [
                    {"path": "src/grant.gd", "line": 12, "kind": "harness_flag"},
                ]
            },
        )
        self.assertEqual([], inherited)


class BlockingFindingTests(unittest.TestCase):
    def test_node_probe_grant_blocks_like_a_harness_flag_grant(self) -> None:
        from evalsys.taskgen.antigrant import Finding
        probe = Finding("a.gd", 3, "node_probe", "gb_goal probed", "if p.is_in_group(\"gb_goal\")",
                        classification="grant")
        self.assertTrue(is_blocking_finding(probe))
        self.assertTrue(is_blocking_finding(Finding("a.gd", 3, "node_probe", "", "")))
        self.assertFalse(is_blocking_finding(
            Finding("a.gd", 3, "node_probe", "", "", classification="concession")))
        self.assertTrue(is_blocking_finding(Finding("a.gd", 3, "harness_flag", "", "")))
        self.assertTrue(is_blocking_finding(Finding("a.gd", 3, "auto_win_ready", "", "")))
        self.assertFalse(is_blocking_finding(Finding("a.gd", 3, "harness_read", "", "")))


class HealthDecreaseScannerTests(unittest.TestCase):


    DECREASING = (
        "health = 1.0 if won else 0.0",
        "\thealth -= dmg",
        "health += -dmg",
        "set_health(health - dmg)",
        "hp = clampf(hp - hit, 0.0, max_hp)",
        "health = max(0, health - x)",
        'stats["health"] -= 1',
        "stats['health'] = stats['health'] - 1",
        "GameState.health = maxf(0.0, GameState.health - amount)",
        "\tif dead: health = 0",
    )
    NOT_DECREASING = (
        "var health: float = 1.0",
        "var health := 0.0",
        "@export var hp: int = 0",
        "if health <= 0:",
        "if health == 0:",
        "health_bar.value = 0",
        "health = 100",
        "health += hp",
        "health = 0.5 * max_health",
        "score = max_health - 1",
        'label.text = "health: %d" % health',
    )

    def test_pattern_accepts_legal_decreases_and_rejects_lookalikes(self) -> None:
        from evalsys.taskgen.antigrant import HEALTH_DECREASE_RE

        for line in self.DECREASING:
            self.assertTrue(HEALTH_DECREASE_RE.search(line), line)
        for line in self.NOT_DECREASING:
            self.assertFalse(HEALTH_DECREASE_RE.search(line), line)

    def test_declaration_literal_zero_is_not_a_decrease_but_ternary_is(self) -> None:
        from evalsys.taskgen.antigrant import health_decrease_evidence

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.gd").write_text(
                "extends Node2D\n"
                "var health: float = 1.0\n"
                "var won := false\n"
                "func finish(w: bool) -> void:\n"
                "\twon = w\n"
                "\thealth = 1.0 if won else 0.0\n",
                encoding="utf-8",
            )
            hits = health_decrease_evidence(root)
            self.assertEqual([6], [h.line for h in hits])
            (root / "main.gd").write_text(
                "extends Node2D\nvar health: float = 0.0\nfunc _ready():\n\thealth = 1.0\n",
                encoding="utf-8",
            )
            self.assertEqual([], health_decrease_evidence(root))

    def test_health_item_scans_the_declared_property_of_a_real_project(self) -> None:


        import json

        from evalsys.taskgen.evaluate import _health_item
        from evalsys.taskgen.submission import load_submission
        from evalsys.verdict import Verdict

        with tempfile.TemporaryDirectory() as tmp:
            game = write_conformant_project(Path(tmp) / "game")
            levels = json.loads((game / "gb_levels.json").read_text(encoding="utf-8"))
            levels["numeric"]["health"] = "gb_player.integrity"
            (game / "gb_levels.json").write_text(json.dumps(levels), encoding="utf-8")
            (game / "ship.gd").write_text(
                "extends Node2D\nvar integrity := 1.0\nfunc finish(won: bool) -> void:\n"
                "\tintegrity = 1.0 if won else 0.0\n",
                encoding="utf-8",
            )
            item = _health_item(load_submission(Path(tmp)))
            self.assertEqual(Verdict.PASSED, item.verdict, item.detail)
            self.assertEqual("integrity", item.evidence["declared_property"])
            (game / "ship.gd").write_text(
                "extends Node2D\nvar integrity := 1.0\n", encoding="utf-8"
            )
            item = _health_item(load_submission(Path(tmp)))
            self.assertEqual(Verdict.FAILED, item.verdict)
            self.assertIn("integrity", item.detail)

    def test_declared_numeric_health_property_is_scanned(self) -> None:
        from evalsys.taskgen.antigrant import (
            declared_health_property,
            health_decrease_evidence,
        )

        self.assertEqual("integrity", declared_health_property("gb_player.integrity"))
        self.assertEqual("integrity", declared_health_property("/root/GB:integrity"))
        self.assertEqual("health", declared_health_property("health"))
        self.assertEqual("", declared_health_property(""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ship.gd").write_text(
                "extends Node2D\nvar integrity := 3\nfunc hit():\n\tintegrity -= 1\n",
                encoding="utf-8",
            )
            self.assertEqual([], health_decrease_evidence(root))
            hits = health_decrease_evidence(root, extra_names=("integrity",))
            self.assertEqual([("ship.gd", 4)], [(h.path, h.line) for h in hits])


class HealthOwnerBindingTests(unittest.TestCase):


    def _project(self, root: Path, *, player_gd: str, enemy_gd: str, main_gd: str = "",
                 health: str = "health") -> Path:
        import json

        game = write_conformant_project(root / "game")
        levels = json.loads((game / "gb_levels.json").read_text(encoding="utf-8"))
        levels["numeric"]["health"] = health
        (game / "gb_levels.json").write_text(json.dumps(levels), encoding="utf-8")
        (game / "player.gd").write_text(
            "\n".join(
                f'func _poll_{name}(): return Input.is_action_pressed("{name}")'
                for name in ACTIONS
            ) + "\n" + player_gd,
            encoding="utf-8",
        )
        (game / "enemy.gd").write_text(enemy_gd, encoding="utf-8")
        (game / "main.gd").write_text(main_gd or "extends Node2D\n", encoding="utf-8")
        (game / "level.tscn").write_text(
            "[gd_scene load_steps=4 format=3]\n\n"
            '[ext_resource type="Script" path="res://main.gd" id="1_m"]\n'
            '[ext_resource type="Script" path="res://player.gd" id="2_p"]\n'
            '[ext_resource type="Script" path="res://enemy.gd" id="3_e"]\n\n'
            '[node name="Level" type="Node2D"]\nscript = ExtResource("1_m")\n\n'
            '[node name="Player" type="Node2D" parent="." groups=["gb_player"]]\n'
            'script = ExtResource("2_p")\n\n'
            '[node name="Enemy" type="Node2D" parent="." groups=["gb_enemy"]]\n'
            'script = ExtResource("3_e")\n',
            encoding="utf-8",
        )
        return root

    def _item(self, root: Path):
        from evalsys.taskgen.evaluate import _health_item
        from evalsys.taskgen.submission import load_submission

        return _health_item(load_submission(root))

    def test_enemy_hp_decrease_is_not_evidence_for_declared_health(self) -> None:
        from evalsys.verdict import Verdict

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                Path(tmp),
                player_gd="extends Node2D\nvar health := 3\n",
                enemy_gd="extends Node2D\nvar hp := 5\nfunc hit(d):\n\thp -= d\n\tif hp <= 0: queue_free()\n",
                main_gd="extends Node2D\nvar enemies := []\nfunc _process(d):\n\tfor e in enemies:\n\t\te.hp -= d * 90\n",
            )
            item = self._item(root)
            self.assertEqual(Verdict.FAILED, item.verdict, item.detail)
            self.assertIn("no decreasing assignment to that property", item.detail)
            self.assertEqual(["health"], item.evidence["scanned_names"])
            self.assertEqual({"owner": 0, "unattributed": 0, "other_object": 0, "mirror": 0},
                             item.evidence["binding_counts"])
            self.assertEqual([], item.evidence["mirrors"])

    def test_enemy_write_to_same_named_health_is_other_object(self) -> None:
        from evalsys.verdict import Verdict

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                Path(tmp),
                player_gd="extends Node2D\nvar health := 3\n",
                enemy_gd="extends Node2D\nvar health := 5\nfunc hit(d):\n\thealth -= d\n",
                main_gd="extends Node2D\nfunc _on_shot(boss):\n\tboss.health -= 1\n",
            )
            item = self._item(root)
            self.assertEqual(Verdict.FAILED, item.verdict, item.detail)
            self.assertIn("only decreasing writes to it are on other objects", item.detail)
            self.assertEqual(2, item.evidence["binding_counts"]["other_object"])
            self.assertEqual(0, item.evidence["binding_counts"]["owner"])

    def test_player_and_autoload_and_player_reference_writes_are_owner(self) -> None:
        from evalsys.verdict import Verdict

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                Path(tmp),
                player_gd="extends Node2D\nvar health := 3\nfunc take_damage(d):\n\thealth = maxi(0, health - d)\n",
                enemy_gd="extends Node2D\nvar health := 5\nfunc hit(d):\n\thealth -= d\n",
                main_gd="extends Node2D\n@onready var player = $Player\nfunc _on_hazard():\n\tplayer.health -= 1\n",
            )
            item = self._item(root)
            self.assertEqual(Verdict.PASSED, item.verdict, item.detail)
            counts = item.evidence["binding_counts"]
            self.assertEqual(2, counts["owner"])
            self.assertEqual(1, counts["other_object"])
            self.assertIn("res://player.gd", item.evidence["owner_scripts"])
            self.assertIn("res://main.gd", item.evidence["owner_scripts"])


        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                Path(tmp),
                player_gd="extends Node2D\n",
                enemy_gd="extends Node2D\nvar hp := 2\nfunc hit():\n\thp -= 1\n",
                main_gd="extends Node2D\nfunc _on_hit():\n\tGB.health -= 1\n",
                health="/root/GB:health",
            )
            game = root / "game"
            (game / "gb.gd").write_text("extends Node\nvar health := 3\n", encoding="utf-8")
            godot = game / "project.godot"
            godot.write_text(
                godot.read_text(encoding="utf-8") + '\n[autoload]\n\nGB="*res://gb.gd"\n',
                encoding="utf-8",
            )
            item = self._item(root)
            self.assertEqual(Verdict.PASSED, item.verdict, item.detail)
            self.assertEqual("GB", item.evidence["declared_owner"])
            self.assertEqual("health", item.evidence["declared_property"])
            self.assertEqual(1, item.evidence["binding_counts"]["owner"])


class HealthMirrorAliasTests(unittest.TestCase):


    _project = HealthOwnerBindingTests._project
    _item = HealthOwnerBindingTests._item

    def _autoload(self, root: Path, name: str, script: str, body: str) -> None:
        game = root / "game"
        (game / script).write_text(body, encoding="utf-8")
        godot = game / "project.godot"
        godot.write_text(
            godot.read_text(encoding="utf-8") + f'\n[autoload]\n\n{name}="*res://{script}"\n',
            encoding="utf-8",
        )

    def test_local_decrement_mirrored_into_autoload_owner_counts(self) -> None:
        from evalsys.verdict import Verdict

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                Path(tmp),
                player_gd=(
                    "extends Node2D\nvar hp: float = 100.0\n"
                    "func take_damage(amount):\n\thp = maxf(0.0, hp - amount)\n\tRunState.health = hp\n"
                    "func heal(amount):\n\thp = minf(100.0, hp + amount)\n\tRunState.health = hp\n"
                ),
                enemy_gd="extends Node2D\nvar hp := 5\nfunc hit(d):\n\thp -= d\n",
                main_gd=(
                    "extends Node2D\n@onready var player = $Player\n"
                    "func _sync():\n\tRunState.health = player.hp\n"
                ),
            )
            self._autoload(root, "RunState", "run_state.gd", "extends Node\nvar health: float = 100.0\n")
            item = self._item(root)
            self.assertEqual(Verdict.PASSED, item.verdict, item.detail)
            self.assertIn("through a mirrored source", item.detail)
            counts = item.evidence["binding_counts"]
            self.assertEqual(1, counts["mirror"])
            self.assertEqual(1, counts["owner"])


            self.assertEqual(0, counts["other_object"])
            hits = item.evidence["hits"]

            self.assertEqual([("player.gd", 12, "health_decrease_mirror", "owner")],
                             [(h["path"], h["line"], h["kind"], h["classification"]) for h in hits])


            self.assertRegex(hits[0]["detail"], r"RunState\.health = (?:player\.)?hp")
            sources = {(m["source_receiver"], m["source_property"]) for m in item.evidence["mirrors"]}
            self.assertEqual({("", "hp"), ("player", "hp")}, sources)

    def test_child_component_alias_resolves_through_the_scene(self) -> None:

        from evalsys.verdict import Verdict

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                Path(tmp),
                player_gd=(
                    "extends Node2D\n@onready var health_comp: Node = $Health\nvar health: float = 100.0\n"
                    "func _ready():\n\thealth = float(health_comp.hp)\n"
                    "func _physics_process(_d):\n\thealth = float(health_comp.hp)\n"
                ),
                enemy_gd="extends Node2D\n@onready var health_comp: Node = $Health\n",
            )
            game = root / "game"
            (game / "health_component.gd").write_text(
                "extends Node\nvar hp: int = 100\nfunc damage(taken):\n\thp = maxi(0, hp - taken)\n",
                encoding="utf-8",
            )
            (game / "level.tscn").write_text(
                "[gd_scene load_steps=5 format=3]\n\n"
                '[ext_resource type="Script" path="res://main.gd" id="1_m"]\n'
                '[ext_resource type="Script" path="res://player.gd" id="2_p"]\n'
                '[ext_resource type="Script" path="res://enemy.gd" id="3_e"]\n'
                '[ext_resource type="Script" path="res://health_component.gd" id="4_h"]\n\n'
                '[node name="Level" type="Node2D"]\nscript = ExtResource("1_m")\n\n'
                '[node name="Player" type="Node2D" parent="." groups=["gb_player"]]\n'
                'script = ExtResource("2_p")\n\n'
                '[node name="Health" type="Node" parent="Player"]\nscript = ExtResource("4_h")\n\n'
                '[node name="Enemy" type="Node2D" parent="." groups=["gb_enemy"]]\n'
                'script = ExtResource("3_e")\n\n'
                '[node name="Health" type="Node" parent="Enemy"]\nscript = ExtResource("4_h")\n',
                encoding="utf-8",
            )


            item = self._item(root)
            self.assertEqual(Verdict.FAILED, item.verdict, item.detail)
            self.assertEqual([], item.evidence["mirrors"][0]["source_scripts"])

            (game / "player.tscn").write_text(
                "[gd_scene load_steps=3 format=3]\n\n"
                '[ext_resource type="Script" path="res://player.gd" id="2_p"]\n'
                '[ext_resource type="Script" path="res://health_component.gd" id="4_h"]\n\n'
                '[node name="Player" type="Node2D" groups=["gb_player"]]\nscript = ExtResource("2_p")\n\n'
                '[node name="Health" type="Node" parent="."]\nscript = ExtResource("4_h")\n',
                encoding="utf-8",
            )
            item = self._item(root)
            self.assertEqual(Verdict.PASSED, item.verdict, item.detail)
            self.assertEqual(1, item.evidence["binding_counts"]["mirror"])
            self.assertEqual(
                ["res://health_component.gd"], item.evidence["mirrors"][0]["source_scripts"]
            )
            self.assertEqual(("health_component.gd", 4), tuple(
                (h["path"], h["line"]) for h in item.evidence["hits"]
            )[0])

    def test_alias_from_a_non_owner_script_or_to_an_enemy_source_is_not_evidence(self) -> None:
        from evalsys.verdict import Verdict

        with tempfile.TemporaryDirectory() as tmp:


            root = self._project(
                Path(tmp),
                player_gd="extends Node2D\nvar health := 3\n",
                enemy_gd=(
                    "extends Node2D\nvar hp := 5\nvar health := 5\n"
                    "func hit(d):\n\thp -= d\n\thealth = hp\n"
                ),
                main_gd="extends Node2D\nvar label_hp := 0\nfunc _process(_d):\n\tlabel_hp = GB.health\n",
            )
            self._autoload(root, "GB", "gb.gd", "extends Node\nvar health := 3\n")
            item = self._item(root)
            self.assertEqual(Verdict.FAILED, item.verdict, item.detail)
            self.assertEqual(0, item.evidence["binding_counts"]["mirror"])
            self.assertEqual([], item.evidence["mirrors"])

    def test_statement_colon_is_not_a_receiver_and_ext_resource_attrs_are_order_free(self) -> None:


        from evalsys.taskgen.antigrant import _ext_resources, _receiver_before
        from evalsys.verdict import Verdict

        self.assertIsNone(_receiver_before("if not safe: health=0; return", "health"))
        self.assertIsNone(_receiver_before("if hit:\thealth -= 1", "health"))
        self.assertEqual("GB", _receiver_before("GB:health -= 1", "health"))
        self.assertEqual("Player", _receiver_before("$Player:health -= 1", "health"))
        self.assertEqual("boss", _receiver_before("boss.health -= 1", "health"))
        self.assertEqual(
            [("Script", "res://main.gd", "1"), ("PackedScene", "res://player.tscn", "2_p")],
            _ext_resources(
                '[ext_resource path="res://main.gd" type="Script" id="1"]\n'
                '[ext_resource type="PackedScene" path="res://player.tscn" id="2_p"]\n'
                '[ext_resource type="Texture2D" path="res://a.png" id="3"]\n'
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                Path(tmp),
                player_gd="extends Node2D\n",
                enemy_gd="extends Node2D\n",
                main_gd=(
                    "extends Node2D\nvar health: float = 1.0\nfunc _physics_process(_d):\n"
                    '\tif not safe: health=0; get_tree().change_scene_to_file("res://lose.tscn"); return\n'
                ),
            )
            game = root / "game"
            scene = (game / "level.tscn").read_text(encoding="utf-8")
            scene = scene.replace(
                '[ext_resource type="Script" path="res://main.gd" id="1_m"]',
                '[ext_resource path="res://main.gd" type="Script" id="1_m"]',
            )
            (game / "level.tscn").write_text(scene, encoding="utf-8")
            item = self._item(root)
            self.assertEqual(Verdict.PASSED, item.verdict, item.detail)
            self.assertIn("res://main.gd", item.evidence["owner_scripts"])
            self.assertEqual(1, item.evidence["binding_counts"]["owner"])
            self.assertEqual(0, item.evidence["binding_counts"]["other_object"])

    def test_health_write_binding_reports_mirrors_directly(self) -> None:
        from evalsys.taskgen.antigrant import health_write_binding

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                Path(tmp),
                player_gd="extends Node2D\nvar hp := 9\nfunc _hurt():\n\thp -= 1\n\tself.health = hp\nvar health := 9\n",
                enemy_gd="extends Node2D\n",
            )
            binding = health_write_binding(root / "game", "health", levels=["res://level.tscn"])
            self.assertTrue(binding["writable"])
            self.assertEqual(1, binding["mirror"])
            self.assertEqual([("player.gd", 13, "", "hp", ["res://player.gd"])], [
                (m["path"], m["line"], m["source_receiver"], m["source_property"], m["source_scripts"])
                for m in binding["mirrors"]
            ])

    def test_getter_alias_follows_player_component_but_not_enemy_only_writes(self) -> None:
        from evalsys.verdict import Verdict

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                Path(tmp), health="current_health",
                player_gd=("extends Node2D\nvar hp := 9\n"
                           "var current_health: int:\n\tget:\n\t\treturn int(hp)\n"
                           "func hurt(d):\n\thp -= d\n"),
                enemy_gd="extends Node2D\nvar hp := 3\nfunc hurt(d):\n\thp -= d\n",
            )
            self.assertEqual(Verdict.PASSED, self._item(root).verdict)
            player = root / "game/player.gd"
            player.write_text(player.read_text().replace("hp -= d", "pass"))
            self.assertEqual(Verdict.FAILED, self._item(root).verdict)

    def test_getter_on_autoload_follows_lives_write(self) -> None:
        from evalsys.verdict import Verdict

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp), player_gd="extends Node2D\n", enemy_gd="extends Node2D\n")
            self._autoload(root, "GB", "gb.gd",
                           "extends Node\nvar lives := 3\nvar health: int:\n"
                           "\tget:\n\t\treturn lives\nfunc hurt():\n\tlives -= 1\n")
            item = self._item(root)
            self.assertEqual(Verdict.PASSED, item.verdict, item.detail)
            self.assertEqual(1, item.evidence["binding_counts"]["mirror"])

    def test_gb_state_is_numeric_owner_and_comment_is_not_a_write(self) -> None:
        from evalsys.verdict import Verdict

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                Path(tmp), health="life", player_gd="extends Node2D\n",
                enemy_gd="extends Node\nvar life := 10\nfunc judge(v):\n\tlife = clampi(life + verdict_life(v), 0, 10)\n",
            )
            scene = root / "game/level.tscn"
            self.assertEqual(Verdict.FAILED, self._item(root).verdict)
            scene.write_text(scene.read_text().replace('groups=["gb_enemy"]', 'groups=["gb_state"]'))
            self.assertEqual(Verdict.PASSED, self._item(root).verdict)
            (root / "game/enemy.gd").write_text("extends Node\nvar life := 10\n# life = 0 on defeat\n")
            self.assertEqual(Verdict.FAILED, self._item(root).verdict)


class IdleDifferentialTests(unittest.TestCase):
    def test_scene_changed_vs_budget_is_a_grant(self) -> None:
        from evalsys.taskgen.engine import _idle_diverged

        env = {"reached": False, "stop_reason": "budget_frames"}
        flagged = {"reached": False, "stop_reason": "scene_changed"}
        self.assertTrue(_idle_diverged(env, flagged))
        self.assertFalse(_idle_diverged(env, env))


if __name__ == "__main__":
    unittest.main()
