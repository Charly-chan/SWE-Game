from __future__ import annotations
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from evalsys.interface import load_submission_interface
from evalsys.interface.loader import project_root
from evalsys.routes.agent import CANONICAL_ACTIONS, FORBIDDEN_ACTIONS, allowed_action_set, mash_ops, op_table_for_agent, validate_ops
from evalsys.taskgen.engine import MASH_PROBE_FRAMES, _mash_won, run_extended_mash
from evalsys.taskgen.evaluate import _mash_item
from evalsys.tasks import eval_root
from evalsys.verdict import Verdict
from _interface_fixture import read_manifest, write_conformant_project, write_manifest
_SCRATCH = Path('/tmp/swe-game/gb_taskgen_scratch')
_WHY = 'six verbs cannot name this human-reachable input'

def _key_binding(name: str, keycode: int=90) -> str:
    return f'{name}={{\n"deadzone": 0.5,\n"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":{keycode},"physical_keycode":0,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)\n]\n}}'

def _add_extra(root: Path, ident: str, *, why: str=_WHY, keycode: int=90, read: bool=True, bind: bool=True) -> None:
    godot = root / 'project.godot'
    text = godot.read_text(encoding='utf-8')
    if bind:
        text = text.rstrip() + '\n' + _key_binding(ident, keycode) + '\n'
    else:
        text = text.rstrip() + f'\n{ident}={{\n"deadzone": 0.5,\n"events": [Object()]\n}}\n'
    godot.write_text(text, encoding='utf-8')
    if read:
        player = root / 'player.gd'
        player.write_text(player.read_text(encoding='utf-8') + f'\nfunc _poll_extra(): return Input.is_action_pressed("{ident}")\n', encoding='utf-8')
    manifest = read_manifest(root)
    extras = list(manifest.get('extended_actions') or [])
    extras.append({'id': ident, 'why': why})
    manifest['extended_actions'] = extras
    write_manifest(root, manifest)

class LoaderExtendedActionTests(unittest.TestCase):

    def test_absent_field_is_a_pass_and_hashes_like_the_pre_extension_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            loaded = load_submission_interface(root)
            self.assertTrue(loaded.report.ok, loaded.report.to_dict())
            self.assertEqual((), loaded.extended_actions)
            self.assertEqual('pass', loaded.report.checks['extended_actions']['status'])
            payload_absent = loaded.normalized_sha256
            manifest = read_manifest(root)
            manifest['extended_actions'] = []
            write_manifest(root, manifest)
            again = load_submission_interface(root)
            self.assertEqual(payload_absent, again.normalized_sha256)
            self.assertNotEqual(loaded.source_sha256, again.source_sha256)
            self.assertNotIn('extended_actions', loaded.provenance())

    def test_harvest_ledger_digest_matches_the_frozen_certificate(self) -> None:
        game = eval_root().parent / 'games' / 'harvest_ledger'
        cert = eval_root() / 'tasks' / 'harvest_ledger' / 'certificate.rt1.json'
        if not game.is_dir() or not cert.is_file():
            self.skipTest('harvest_ledger gold is not on disk')
        loaded = load_submission_interface(game)
        interface = json.loads(cert.read_text(encoding='utf-8'))['interface']
        frozen_ids = tuple((str(entry['id']) for entry in interface.get('extended_actions') or []))
        self.assertEqual(frozen_ids, loaded.extended_action_ids)
        self.assertEqual(interface['normalized_sha256'], loaded.normalized_sha256)

    def _corpus_manifests(self) -> list[tuple[Path, list[dict]]]:
        games_root = eval_root().parent / 'games'
        if not games_root.is_dir():
            self.skipTest('games/ is not on disk')
        registry = json.loads((eval_root() / 'routes' / 'registry.json').read_text(encoding='utf-8'))
        registered = set(registry['tasks'])
        self.assertTrue(registered, 'registry.json lists no tasks, so the sweep has no floor to check against and every invariant below would pass vacuously')
        out: list[tuple[Path, list[dict]]] = []
        for game in sorted((p for p in games_root.iterdir() if p.is_dir())):
            manifest = project_root(game) / 'gb_levels.json'
            if not manifest.is_file():
                continue
            payload = json.loads(manifest.read_text(encoding='utf-8'))
            out.append((game, list(payload.get('extended_actions') or [])))
        missed = sorted(registered - {game.name for game, _ in out})
        self.assertEqual([], missed, f'registered games the corpus sweep never opened: {missed}. Each one has a route and a certificate, so every invariant in this class silently skipped a game it is supposed to hold.')
        return out

    def test_corpus_declarations_are_validated_frozen_and_mash_covered(self) -> None:
        declaring = [(game, raw) for game, raw in self._corpus_manifests() if raw]
        for game, raw in declaring:
            with self.subTest(game=game.name):
                loaded = load_submission_interface(game)
                check = loaded.report.checks['extended_actions']
                self.assertEqual('pass', check['status'], check['detail'])
                ids = loaded.extended_action_ids
                self.assertEqual(tuple((str(item.get('id')) for item in raw)), ids, 'the loader dropped or reordered a declared id')
                self.assertEqual([{'id': item.id, 'why': item.why} for item in loaded.extended_actions], loaded.provenance()['extended_actions'], 'a certificate would freeze a different set than the game declared')
                for ident in ids:
                    self.assertNotIn(ident, CANONICAL_ACTIONS, ident)
                    self.assertNotIn(ident, FORBIDDEN_ACTIONS, ident)
                    self.assertFalse(ident.startswith(('gb_', 'ui_')), ident)
                self.assertEqual(frozenset(CANONICAL_ACTIONS) | frozenset(ids), allowed_action_set(ids), 'the dispatch gate and the loader disagree about this set')
                ops = mash_ops(ids, MASH_PROBE_FRAMES)
                self.assertTrue(ops, 'declared extras with no mash negative control')
                self.assertEqual(MASH_PROBE_FRAMES, sum((op.frames for op in ops)))
                for op in ops:
                    self.assertEqual(ids, op.actions)

    def test_corpus_games_that_declare_nothing_hash_like_the_pre_extension_corpus(self) -> None:
        for game, raw in self._corpus_manifests():
            if raw:
                continue
            with self.subTest(game=game.name):
                loaded = load_submission_interface(game)
                self.assertEqual((), loaded.extended_action_ids)
                self.assertNotIn('extended_actions', loaded.provenance())

    def test_registered_certificates_and_their_games_agree_on_extras(self) -> None:
        tasks = eval_root() / 'tasks'
        games_root = eval_root().parent / 'games'
        skip = {'terraforge', 'tiny_rts', 'pixel_platformer_v2'}
        found = 0
        disagree: list[str] = []
        for cert in sorted(tasks.glob('*/certificate.rt1.json')):
            task_id = cert.parent.name
            if task_id in skip or not (games_root / task_id).is_dir():
                continue
            iface = json.loads(cert.read_text(encoding='utf-8')).get('interface') or {}
            frozen = tuple((str(entry['id']) for entry in iface.get('extended_actions') or []))
            found += 1
            live = load_submission_interface(games_root / task_id).extended_action_ids
            if frozen != live:
                disagree.append(f'{task_id}: certificate {frozen} vs game {live}')
        self.assertGreaterEqual(found, 20)
        self.assertEqual([], disagree, '\n'.join(disagree))

    def test_declared_extra_must_have_a_human_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            _add_extra(root, 'place', bind=False)
            loaded = load_submission_interface(root)
            self.assertFalse(loaded.report.ok)
            self.assertIn('gb_levels.json.extended_actions', loaded.report.missing)
            self.assertIn('not bound', loaded.report.checks['extended_actions']['detail'])

    def test_declared_extra_must_be_read_as_a_string_literal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            _add_extra(root, 'place', read=False)
            loaded = load_submission_interface(root)
            self.assertFalse(loaded.report.ok)
            self.assertIn('string literal', loaded.report.checks['extended_actions']['detail'])

    def test_pause_reset_gb_and_ui_names_are_reserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            _add_extra(root, 'gb_action')
            loaded = load_submission_interface(root)
            self.assertIn('reserved', loaded.report.checks['extended_actions']['detail'])
            for ident in ('pause', 'ui_accept'):
                manifest = read_manifest(root)
                manifest['extended_actions'] = [{'id': ident, 'why': _WHY}]
                write_manifest(root, manifest)
                loaded = load_submission_interface(root)
                self.assertIn('reserved', loaded.report.checks['extended_actions']['detail'], ident)

    def test_cmd_finish_is_a_legal_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            _add_extra(root, 'cmd_finish')
            loaded = load_submission_interface(root)
            self.assertTrue(loaded.report.ok, loaded.report.to_dict())
            self.assertEqual(('cmd_finish',), loaded.extended_action_ids)

    def test_a_justified_bound_and_read_extra_passes_and_changes_the_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            before = load_submission_interface(root)
            _add_extra(root, 'place')
            after = load_submission_interface(root)
            self.assertTrue(after.report.ok, after.report.to_dict())
            self.assertEqual(('place',), after.extended_action_ids)
            self.assertNotEqual(before.normalized_sha256, after.normalized_sha256)
            self.assertEqual([{'id': 'place', 'why': _WHY}], after.provenance()['extended_actions'])

    def test_short_why_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            _add_extra(root, 'place', why='needed')
            loaded = load_submission_interface(root)
            self.assertFalse(loaded.report.ok)
            self.assertIn('why', loaded.report.checks['extended_actions']['detail'])

class ValidateOpsExtraTests(unittest.TestCase):

    def test_an_undeclared_native_action_is_still_refused(self) -> None:
        check = validate_ops([{'op': 'hold', 'action': 'place', 'frames': 10}])
        self.assertEqual('unknown_action', check.refusals[0].reason)
        allowed = validate_ops([{'op': 'hold', 'action': 'place', 'frames': 10}], extra_actions=('place',))
        self.assertTrue(allowed.ok, allowed.to_dict())
        self.assertEqual('place', allowed.accepted[0].action)

    def test_pause_stays_forbidden_even_if_passed_as_an_extra(self) -> None:
        check = validate_ops([{'op': 'tap', 'action': 'gb_reset', 'frames': 2}], extra_actions=('gb_reset',))
        self.assertEqual('forbidden_action', check.refusals[0].reason)

    def test_canonical_ops_are_unchanged_when_extras_are_empty(self) -> None:
        check = validate_ops([{'op': 'hold', 'action': 'gb_right', 'frames': 8}])
        self.assertTrue(check.ok)
        table = op_table_for_agent()
        self.assertEqual(list(CANONICAL_ACTIONS), table['actions'])
        self.assertEqual([], table['extended_actions'])

    def test_op_table_lists_frozen_extras_as_read_only(self) -> None:
        table = op_table_for_agent(('place', 'quick_craft'))
        self.assertIn('place', table['actions'])
        self.assertIn('frozen into the interface digest', table['note'])

    def test_mash_ops_hold_every_extra_from_frame_zero(self) -> None:
        ops = mash_ops(('place', 'look_up'), 700)
        self.assertEqual(2, len(ops))
        self.assertEqual(('place', 'look_up'), ops[0].actions)
        self.assertEqual(600, ops[0].frames)
        self.assertEqual(100, ops[1].frames)
        self.assertEqual([], mash_ops((), 1100))

class MashControlTests(unittest.TestCase):

    def test_scene_changed_without_success_is_not_a_win_button(self) -> None:
        for deaths in (0, 1):
            self.assertFalse(_mash_won({'reached': False, 'stop_reason': 'scene_changed', 'deaths': deaths}))
        self.assertTrue(_mash_won({'reached': True, 'stop_reason': 'goal_reached'}))
        self.assertTrue(_mash_won({'reached': False, 'stop_reason': 'goal_reached'}))
        self.assertTrue(_mash_won({'reached': True, 'stop_reason': 'scene_changed'}))
        self.assertFalse(_mash_won({'reached': False, 'stop_reason': 'budget_frames'}))

    def test_inapplicable_mash_leaves_the_denominator(self) -> None:
        item = _mash_item({'applicable': False, 'won': False, 'reason': 'no extras'})
        self.assertEqual(Verdict.UNOBSERVABLE, item.verdict)
        self.assertFalse(item.verdict.in_denominator)

    def test_a_winning_mash_is_a_binary_failure(self) -> None:
        item = _mash_item({'applicable': True, 'won': True, 'extras': ['cmd_finish'], 'reading': {'stop_reason': 'goal_reached', 'reached': True}})
        self.assertEqual(Verdict.FAILED, item.verdict)
        self.assertIn('win button', item.detail)

    def test_a_quiet_mash_passes(self) -> None:
        item = _mash_item({'applicable': True, 'won': False, 'extras': ['hotbar_1'], 'reading': {'stop_reason': 'budget_frames', 'reached': False}})
        self.assertEqual(Verdict.PASSED, item.verdict)

    def test_run_extended_mash_is_inapplicable_when_nothing_is_declared(self) -> None:
        result = run_extended_mash(session=None, extra_actions=(), predicate='whole_game_clear()')
        self.assertFalse(result['applicable'])
        self.assertFalse(result['won'])

class FrozenDispatchTests(unittest.TestCase):

    def test_session_dispatch_defaults_to_the_loaded_interface(self) -> None:
        from evalsys.routes.runner import RouteSession
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            _add_extra(root, 'place')
            session = RouteSession(root)
            self.assertEqual(('place',), session.extra_actions())
            stolen = RouteSession(root, dispatch_extended=())
            self.assertEqual((), stolen.extra_actions())
            check = stolen.check_ops([{'op': 'hold', 'action': 'place', 'frames': 4}])
            self.assertEqual('unknown_action', check.refusals[0].reason)

class EngineMashGateTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        from evalsys.taskgen.engine import PREFERRED_GODOT, godot_available, godot_version
        os.environ['GODOT_BIN'] = PREFERRED_GODOT
        os.environ['GB_SCRATCH_ROOT'] = str(_SCRATCH)
        os.environ['GB_ROUTES_SCRATCH'] = str(_SCRATCH / 'gb_routes_scratch')
        cls.bin = godot_available()
        cls.version = godot_version(cls.bin) if cls.bin else ''

    def _require_451(self) -> None:
        if self.bin is None or '4.5.1' not in self.version:
            self.skipTest(f'need Godot 4.5.1, have {self.bin} ({self.version})')

    def _drop(self, path: Path) -> None:
        allowed = _SCRATCH.resolve()
        real = path.resolve()
        try:
            real.relative_to(allowed)
        except ValueError:
            return
        if real != allowed and real.is_dir():
            shutil.rmtree(real, ignore_errors=True)

    def _runnable(self, root: Path, ident: str, script: str) -> Path:
        from evalsys.interface.contract import ACTIONS
        root.mkdir(parents=True, exist_ok=True)
        bindings = '\n'.join((_key_binding(name, 65 + index) for index, name in enumerate(ACTIONS)))
        extra = _key_binding(ident, 90)
        (root / 'project.godot').write_text(f'config_version=5\n\n[application]\nconfig/name="MashControl"\nrun/main_scene="res://level.tscn"\nconfig/features=PackedStringArray("4.5", "GL Compatibility")\n\n[rendering]\nrenderer/rendering_method="gl_compatibility"\nrenderer/rendering_method.mobile="gl_compatibility"\n\n[physics]\ncommon/physics_ticks_per_second=60\n\n[input]\n\n{bindings}\n{extra}\n', encoding='utf-8')
        (root / 'player.gd').write_text('\n'.join((f'func _poll_{name}(): return Input.is_action_pressed("{name}")' for name in ACTIONS)) + f'\nfunc _poll_extra(): return Input.is_action_pressed("{ident}")\n', encoding='utf-8')
        (root / 'level.gd').write_text(script, encoding='utf-8')
        (root / 'level.tscn').write_text('[gd_scene load_steps=2 format=3]\n\n[ext_resource type="Script" path="res://level.gd" id="1"]\n\n[node name="Level" type="Node"]\nscript = ExtResource("1")\n\n[node name="Player" type="Node" parent="." groups=["gb_player"]]\n', encoding='utf-8')
        (root / 'win.tscn').write_text('[gd_scene format=3]\n\n[node name="Win" type="Node"]\n', encoding='utf-8')
        (root / 'lose.tscn').write_text('[gd_scene format=3]\n\n[node name="Lose" type="Node"]\n', encoding='utf-8')
        write_manifest(root, {'levels': ['res://level.tscn'], 'endings': {'victory': 'res://win.tscn', 'defeat': 'res://lose.tscn'}, 'extended_actions': [{'id': ident, 'why': _WHY}]})
        return root

    def _mash(self, root: Path, frames: int=180) -> dict:
        from evalsys.interface import load_submission_interface
        from evalsys.routes.runner import RouteSession
        from evalsys.taskgen.engine import ensure_taskgen_scratch
        ensure_taskgen_scratch()
        interface = load_submission_interface(root)
        self.assertTrue(interface.report.ok, interface.report.to_dict())
        scratch = _SCRATCH / 'gb_routes_scratch' / (root.name + '_routes')
        self._drop(scratch)
        session = RouteSession(root, interface=interface, scratch=scratch)
        session.prepare()
        try:
            self.assertTrue(session.prep is not None and session.prep.ok, getattr(session, 'driver_error', '') or 'prepare failed')
            return run_extended_mash(session, extra_actions=interface.extended_action_ids, predicate='whole_game_clear()', frames=frames, task_id=root.name)
        finally:
            self._drop(scratch)

    def test_positive_control_cmd_finish_is_caught(self) -> None:
        self._require_451()
        _SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir='/tmp/swe-game', prefix='gb_mash_win_') as tmp:
            root = self._runnable(Path(tmp), 'cmd_finish', 'extends Node\nfunc _physics_process(_d):\n    if Input.is_action_pressed("cmd_finish"):\n        get_tree().change_scene_to_file("res://win.tscn")\n')
            mash = self._mash(root)
            reading = mash.get('reading') or {}
            self.assertTrue(mash['applicable'], mash)
            self.assertTrue(mash['won'], mash)
            self.assertIn(str(reading.get('stop_reason') or ''), {'scene_changed', 'goal_reached', 'success', 'victory'}, mash)

    def test_clean_control_non_winning_extra_stays_quiet(self) -> None:
        self._require_451()
        _SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir='/tmp/swe-game', prefix='gb_mash_ok_') as tmp:
            root = self._runnable(Path(tmp), 'hotbar_1', 'extends Node\nvar _n := 0\nfunc _physics_process(_d):\n    if Input.is_action_pressed("hotbar_1"):\n        _n += 1\n')
            mash = self._mash(root)
            reading = mash.get('reading') or {}
            self.assertTrue(mash['applicable'], mash)
            self.assertFalse(mash['won'], mash)
            self.assertIn(str(reading.get('stop_reason') or ''), {'budget_frames', 'ops_exhausted'}, mash)
if __name__ == '__main__':
    unittest.main()
