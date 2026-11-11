from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evalsys.context import AssetAssignment, AssetObservation, EvaluationContext
from evalsys.interface import (
    TaskInterfaceRequirements, load_submission_interface,
)
from evalsys.ocard.channels import (
    score_o2_context, score_o6_context, score_o9_context,
)
from evalsys.verdict import Verdict

from _interface_fixture import read_manifest, write_conformant_project, write_manifest


def _ctx(
    root: Path, *, requirements=None, tier="D1", assignment=None, task_mode="",
):
    req = requirements or TaskInterfaceRequirements.empty()
    interface = load_submission_interface(root, requirements=req)
    return EvaluationContext(
        project=root,
        task_id="fixture",
        tier=tier,
        mode="X_CPU",
        interface=interface,
        requirements=req,
        expectations=SimpleNamespace(anchor_composition={"bearing": "center"}),
        asset_assignment=assignment,
        task_mode=task_mode,
    )


class ContextAttributionTests(unittest.TestCase):
    def test_conformant_interface_earns_full_o2_credit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            result = score_o2_context(_ctx(root))
            self.assertEqual(1.0, result.interval.lo)
            self.assertEqual(1.0, result.interval.hi)
            self.assertTrue(all(item.credit == 1.0 for item in result.items))

    def test_o2_mutations_only_fail_their_owned_rungs(self) -> None:
        mutations = {}
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)

            root = write_conformant_project(base / "no_player")
            scene = (root / "level.tscn").read_text(encoding="utf-8")
            (root / "level.tscn").write_text(scene.replace("gb_player", "player"), encoding="utf-8")
            mutations["groups"] = score_o2_context(_ctx(root))

            root = write_conformant_project(base / "dead_action")
            source = (root / "player.gd").read_text(encoding="utf-8")
            (root / "player.gd").write_text(source.replace("gb_jump", "local_jump"), encoding="utf-8")
            mutations["actions"] = score_o2_context(_ctx(root))

            root = write_conformant_project(base / "no_levels")
            manifest = read_manifest(root)
            manifest["levels"] = []
            write_manifest(root, manifest)
            mutations["levels"] = score_o2_context(_ctx(root))

            root = write_conformant_project(base / "shared_ending")
            manifest = read_manifest(root)
            manifest["endings"]["defeat"] = manifest["endings"]["victory"]
            write_manifest(root, manifest)
            mutations["endings"] = score_o2_context(_ctx(root))

            root = write_conformant_project(base / "bad_device")
            manifest = read_manifest(root)
            manifest["device_ids"] = {"reference_only#0": "Device"}
            write_manifest(root, manifest)
            mutations["optional_fields_valid"] = score_o2_context(_ctx(root))

            root = write_conformant_project(base / "required_anchor")
            manifest = read_manifest(root)
            manifest.pop("anchor_camera")
            write_manifest(root, manifest)
            req = TaskInterfaceRequirements.from_dict({
                "O9": {"required": True, "fields": ["anchor_camera"], "reason": "fixture"}
            })
            mutations["task_required_observables"] = score_o2_context(
                _ctx(root, requirements=req)
            )

        for expected, result in mutations.items():
            failed = {
                item.id.removeprefix("O2/") for item in result.items
                if item.verdict is Verdict.FAILED
            }
            self.assertEqual({expected}, failed, (expected, result.to_dict()))

    def test_task_observable_omission_is_failed_not_unmeasurable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp), observables=False)
            req = TaskInterfaceRequirements.from_dict({
                "O9": {"required": True, "fields": ["anchor_camera"]},
            })
            ctx = _ctx(root, requirements=req)
            self.assertEqual(Verdict.FAILED, score_o9_context(ctx).items[0].verdict)

    def test_stale_frozen_o10_requirement_is_dropped_on_load(self) -> None:


        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp), observables=False)
            req = TaskInterfaceRequirements.from_dict({
                "O9": {"required": False, "fields": []},
                "O10": {"required": True, "fields": ["audio_buses"]},
            })
            self.assertEqual(["O9"], sorted(req.channels))
            self.assertEqual((), req.required_fields())
            ctx = _ctx(root, requirements=req)
            result = score_o2_context(ctx)
            self.assertNotIn(
                "task_required_O10", ctx.interface.report.checks,
            )
            by_id = {item.id: item for item in result.items}
            self.assertIs(Verdict.PASSED, by_id["O2/task_required_observables"].verdict)

    def test_o6_distinguishes_tier_and_evaluator_debt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            d1 = score_o6_context(_ctx(root, tier="D1"))
            self.assertEqual(Verdict.UNOBSERVABLE, d1.items[0].verdict)

            missing_manifest = score_o6_context(_ctx(root, tier="D2"))
            self.assertEqual(Verdict.UNMEASURABLE, missing_manifest.items[0].verdict)

            no_probe = score_o6_context(_ctx(
                root, tier="D2", assignment=AssetAssignment(("res://a.png",))
            ))
            self.assertEqual(Verdict.UNMEASURABLE, no_probe.items[0].verdict)

            observed = score_o6_context(_ctx(
                root,
                tier="D2",
                assignment=AssetAssignment(
                    ("res://a.png", "res://b.png"),
                    AssetObservation(("res://a.png",), True),
                ),
            ))
            self.assertEqual(
                [Verdict.PASSED, Verdict.FAILED],
                [item.verdict for item in observed.items],
            )

    def test_o6_is_not_applicable_to_bugfix_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            result = score_o6_context(_ctx(root, tier="D3", task_mode="bugfix"))

            self.assertEqual(Verdict.UNOBSERVABLE, result.items[0].verdict)
            self.assertEqual("O6/asset_coverage", result.items[0].id)
            self.assertEqual("bugfix", result.evidence["task_mode"])

    def test_truth_from_another_interface_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            ctx = _ctx(root)
            ctx.truth = SimpleNamespace(interface={
                **ctx.interface.provenance(), "normalized_sha256": "0" * 64
            })
            with self.assertRaisesRegex(ValueError, "interface provenance mismatch"):
                ctx.assert_interface_provenance(ctx.truth, source="test truth")


if __name__ == "__main__":
    unittest.main()
