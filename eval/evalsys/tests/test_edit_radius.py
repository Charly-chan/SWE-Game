

import json

import pytest

from evalsys.taskgen.edit_radius import (
    edit_radius_item, enclosing_function, hunk_functions, measure_edit_radius,
)
from evalsys.taskgen.submission import EVAL_SMUGGLE_NAMES, SUBMISSION_DELIVERABLE_NAMES
from evalsys.verdict import Verdict


FAULTY = "var value = 0\n\nfunc repair():\n\treturn 0\n" + "\n" * 8 + "func other():\n\treturn 2\n"
REPAIRED = FAULTY.replace("return 0", "return 1")
ORACLE = {"mutations": [{"site": {"path": "scripts/logic.gd", "line": 4}}]}


def _write(root, files):
    root.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
    return root


def _measure(tmp_path, submitted, *, faulty=None, oracle=None):
    before = _write(tmp_path / "faulty", faulty if faulty is not None else {"scripts/logic.gd": FAULTY})
    after = _write(tmp_path / "submitted", submitted)
    return edit_radius_item(before, after, oracle if oracle is not None else ORACLE)


def test_tight_edit(tmp_path):
    item = _measure(tmp_path, {"scripts/logic.gd": REPAIRED})
    assert item.verdict is Verdict.PASSED
    assert item.credit == item.evidence["edit_radius"] == 1.0
    assert item.evidence["file_focus"] == item.evidence["func_focus"] == 1.0
    assert item.evidence["patched_functions"] == {"scripts/logic.gd": ["repair"]}
    assert item.evidence["hunk_functions"] == {"scripts/logic.gd": ["repair"]}

    assert measure_edit_radius(
        {"scripts/logic.gd": FAULTY.encode()}, {"scripts/logic.gd": REPAIRED.encode()}, ORACLE,
    ) == item


def test_unrelated_added_file(tmp_path):
    item = _measure(tmp_path, {"scripts/logic.gd": REPAIRED, "extra.gd": "extends Node\n"})
    assert item.evidence["file_focus"] == 0.5
    assert item.evidence["func_focus"] == 1
    assert item.credit == 0.5 + 0.5 * (0.6 * 0.5 + 0.4 * 1) == 0.85
    assert item.evidence["unrelated_files"] == ["extra.gd"]


def test_second_hunk_in_unpatched_function(tmp_path):
    item = _measure(tmp_path, {"scripts/logic.gd": REPAIRED.replace("return 2", "return 3")})
    assert item.evidence["file_focus"] == 1
    assert item.evidence["func_focus"] == 0.5
    assert item.evidence["hunks_in_patch_files"] == 2
    assert item.evidence["hunks_in_patched_functions"] == 1
    assert item.evidence["hunk_functions"] == {"scripts/logic.gd": ["repair", "other"]}
    assert item.credit == 0.9


def test_scene_only_patch(tmp_path):
    item = _measure(tmp_path, {"scene.tscn": "value = 1\n", "extra.txt": "added"},
                    faulty={"scene.tscn": "value = 0\n"},
                    oracle={"mutations": [{"site": {"path": "scene.tscn", "line": 1}}]})
    assert item.evidence["func_focus"] is None
    assert item.evidence["hunks_in_patch_files"] is None
    assert item.evidence["hunks_in_patched_functions"] is None
    assert item.evidence["hunk_functions"] == item.evidence["patched_functions"] == {}
    assert item.credit == 0.5 + 0.5 * item.evidence["file_focus"] == 0.75


def test_byte_identical(tmp_path):
    item = _measure(tmp_path, {"scripts/logic.gd": FAULTY})
    assert item.verdict is Verdict.INCONCLUSIVE
    assert item.detail == "submission is byte-identical to the faulty build; no edit to measure"
    assert item.evidence["edit_radius"] is None
    assert item.evidence["files_touched"] == []


def test_noise_ignored(tmp_path):
    noise = ["texture.png.import", "script.gd.uid", ".godot/cache", "nested/.godot/cache",
             ".git/config", "nested/.git/config", *[f"nested/{name}" for name in EVAL_SMUGGLE_NAMES]]
    before = {"scripts/logic.gd": FAULTY, **{name: "old" for name in noise}}
    after = {"scripts/logic.gd": FAULTY, **{name: "new" for name in noise[:-1]}}
    after["added.uid"] = "new"
    item = _measure(tmp_path, after, faulty=before)
    assert item.verdict is Verdict.INCONCLUSIVE
    assert item.evidence["files_touched"] == []


def test_root_deliverables_ignored_but_nested_copies_count(tmp_path):
    before = {"scripts/logic.gd": FAULTY, "BUILD.md": "old build notes"}
    after = {
        "scripts/logic.gd": REPAIRED, "GDD.md": "design", "ops.json": "[]",
        "demos.json": "[]", "docs/GDD.md": "nested game edit",
    }
    item = measure_edit_radius(
        {path: data.encode() for path, data in before.items()},
        {path: data.encode() for path, data in after.items()}, ORACLE,
    )
    assert item.evidence["files_touched"] == ["docs/GDD.md", "scripts/logic.gd"]
    assert item.evidence["unrelated_files"] == ["docs/GDD.md"]
    assert item.evidence["deliverables_ignored"] == sorted(SUBMISSION_DELIVERABLE_NAMES)
    assert item.evidence["file_focus"] == 0.5

    assert _measure(tmp_path, after, faulty=before) == item
    unchanged = {"GDD.md": b"same", "ops.json": b"[]"}
    assert measure_edit_radius(unchanged, unchanged, ORACLE).evidence["deliverables_ignored"] == []


@pytest.mark.parametrize("deleted", ["extra.bin", "scripts/logic.gd"])
def test_deleted_file_counts(tmp_path, deleted):
    before = {"scripts/logic.gd": FAULTY, "extra.bin": b"\x00\xff"}
    after = {"scripts/logic.gd": REPAIRED, "extra.bin": b"\x00\xff"}
    del after[deleted]
    item = _measure(tmp_path, after, faulty=before)
    assert deleted in item.evidence["files_touched"]
    assert item.verdict is Verdict.PASSED
    assert item.evidence["file_focus"] == (0.5 if deleted == "extra.bin" else 1)


def test_file_scope_site(tmp_path):
    oracle = {"mutations": [{"site": {"path": "scripts/logic.gd", "line": 1}}]}
    item = _measure(tmp_path, {"scripts/logic.gd": FAULTY.replace("value = 0", "value = 1")}, oracle=oracle)
    assert item.evidence["patched_functions"] == {"scripts/logic.gd": ["<toplevel>"]}
    assert item.evidence["hunk_functions"] == {"scripts/logic.gd": ["<toplevel>"]}
    assert item.credit == 1


def test_hunk_fallback_and_multiple_sites(tmp_path):
    oracle = {"mutations": [
        {"site": {"line": 4}, "hunks": ["scripts/logic.gd: applied hidden bundle edit first"]},
        {"site": {"path": "scripts/logic.gd", "line": 14}},
        {"hunks": ["scene.tscn: applied hidden bundle edit second"]},
    ]}
    item = _measure(tmp_path, {"scripts/logic.gd": REPAIRED.replace("return 2", "return 3")}, oracle=oracle)
    assert item.evidence["files_in_patch_set"] == ["scene.tscn", "scripts/logic.gd"]
    assert item.evidence["patched_functions"] == {"scripts/logic.gd": ["other", "repair"]}
    assert item.credit == 1


def test_no_fault_site_path(tmp_path):
    item = _measure(tmp_path, {"scripts/logic.gd": REPAIRED}, oracle={"mutations": [{"site": {"line": 4}}]})
    assert item.verdict is Verdict.INCONCLUSIVE
    assert item.detail == "oracle names no fault-site file"
    assert item.evidence["edit_radius"] is None


def test_first_change_not_leading_context_and_insertions():
    faulty = "func previous():\n\tpass\n\nfunc target():\n\treturn 0\n"
    assert hunk_functions(faulty, faulty.replace("return 0", "return 1")) == ["target"]
    assert hunk_functions(faulty, "var added = 1\n" + faulty) == ["<toplevel>"]
    assert hunk_functions(faulty, faulty.replace("\tpass\n", "\tpass\n\tprint(1)\n")) == ["previous"]
    assert hunk_functions(faulty, faulty.replace("func target", "\tprint(1)\nfunc target")) == ["previous"]
    assert hunk_functions(faulty, faulty + "\tprint(1)\n") == ["target"]
    assert enclosing_function(["static func target():", "\tpass"], 2) == "target"


def test_evaluate_task_emits_static_item_with_engine_off(tmp_path, monkeypatch):
    from evalsys.taskgen import evaluate
    from evalsys.taskgen.package import TaskPackage

    pkg = TaskPackage(tmp_path / "package", {"schema_version": 1, "mode": "bugfix", "game_id": "unit"})
    _write(pkg.visible / "game", {"scripts/logic.gd": FAULTY, "project.godot": "config_version=5\n"})
    _write(pkg.hidden, {"oracle.json": json.dumps(ORACLE)})
    sub = _write(tmp_path / "sub", {"scripts/logic.gd": REPAIRED, "project.godot": "config_version=5\n"})
    monkeypatch.setattr(evaluate.TaskPackage, "read", lambda _: pkg)
    monkeypatch.setenv("GB_SCRATCH_ROOT", str(tmp_path / "scratch"))
    result = evaluate.evaluate_task(pkg.root, sub, engine="off", score_against_source=False)
    items = [item for item in result.items if item.id == "edit_radius"]
    assert len(items) == 1
    assert items[0].verdict is Verdict.PASSED
    assert items[0].credit == 1
    assert "edit_radius" in evaluate.FIDELITY_ITEM_IDS
    assert "edit_radius" not in evaluate.ENGINE_ITEM_IDS
    assert not result.engine["ran"]
