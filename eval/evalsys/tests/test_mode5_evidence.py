from pathlib import Path

import pytest

from evalsys.taskgen.mode5.evidence import Limits, collect_reference_graph


def put(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def scene(root, *, active=1, enabled=1, guid="a" * 32):
    put(root, "Assets/Entry.unity", f"""%YAML 1.1
--- !u!1 &1
GameObject:
  m_Component:
  - component: {{fileID: 2}}
  - component: {{fileID: 3}}
  m_IsActive: {active}
--- !u!4 &2
Transform:
  m_GameObject: {{fileID: 1}}
  m_Father: {{fileID: 0}}
--- !u!114 &3
MonoBehaviour:
  m_GameObject: {{fileID: 1}}
  m_Enabled: {enabled}
  m_Script: {{fileID: 11500000, guid: {guid}, type: 3}}
""")
    put(root, "Assets/Player.cs", "class Player { void Update() {} }")
    put(root, "Assets/Player.cs.meta", "guid: " + guid + "\n")


def test_only_attached_referenced_scripts_not_file_inventory(tmp_path):
    scene(tmp_path)
    put(tmp_path, "Assets/Unused.cs", "class FakeWin { /* success health input */ }")
    put(tmp_path, "Assets/unused.png", "not-an-actual-image")
    result = collect_reference_graph(tmp_path, ["Assets/Entry.unity"])
    assert result.complete
    assert result.attached_scripts == {"Assets/Player.cs"}
    assert "Assets/Unused.cs" not in result.authored_files
    assert "Assets/unused.png" not in result.reachable_files
    assert result.to_dict()["schema"].endswith(".v1")


@pytest.mark.parametrize("active,enabled", [(0, 1), (1, 0)])
def test_inactive_or_disabled_component_not_used_content(tmp_path, active, enabled):
    scene(tmp_path, active=active, enabled=enabled)
    result = collect_reference_graph(tmp_path, ["Assets/Entry.unity"])
    assert not result.attached_scripts


def test_sdk_not_candidate_authored(tmp_path):
    scene(tmp_path)
    original = tmp_path / "Assets/Player.cs"
    original.unlink()
    (tmp_path / "Assets/Player.cs.meta").unlink()
    put(tmp_path, "Assets/GameBenchmarkSDK/Player.cs", "class Player {}")
    put(tmp_path, "Assets/GameBenchmarkSDK/Player.cs.meta", "guid: " + "a" * 32)
    result = collect_reference_graph(tmp_path, ["Assets/Entry.unity"])
    assert "Assets/GameBenchmarkSDK/Player.cs" in result.reachable_files
    assert "Assets/GameBenchmarkSDK/Player.cs" not in result.authored_files
    assert not result.attached_scripts


def test_unchanged_baseline_is_not_candidate_authored(tmp_path):
    project, baseline = tmp_path / "submission", tmp_path / "baseline"
    scene(project)
    scene(baseline)
    result = collect_reference_graph(project, ["Assets/Entry.unity"], baseline_project=baseline)
    assert not result.authored_files
    assert not result.attached_scripts


def test_duplicate_guid_does_not_select_arbitrary_good_asset(tmp_path):
    scene(tmp_path)
    put(tmp_path, "Assets/Fake.cs", "class Fake {}")
    put(tmp_path, "Assets/Fake.cs.meta", "guid: " + "a" * 32)
    result = collect_reference_graph(tmp_path, ["Assets/Entry.unity"])
    assert not result.complete
    assert not result.attached_scripts
    assert result.unresolved_references


def test_cross_file_file_id_does_not_link_orphan_local_component(tmp_path):
    scene(tmp_path)
    path = tmp_path / "Assets/Entry.unity"
    path.write_text(path.read_text(encoding="utf-8") + """
--- !u!114 &11500000
MonoBehaviour:
  m_Script: {fileID: 11500000, guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, type: 3}
""", encoding="utf-8")
    put(tmp_path, "Assets/Unused.cs", "class Unused {}")
    put(tmp_path, "Assets/Unused.cs.meta", "guid: " + "b" * 32)
    result = collect_reference_graph(tmp_path, ["Assets/Entry.unity"])
    assert result.attached_scripts == {"Assets/Player.cs"}


def test_asset_graph_follows_referenced_prefab_and_texture(tmp_path):
    scene(tmp_path)
    entry = tmp_path / "Assets/Entry.unity"
    entry.write_text(entry.read_text(encoding="utf-8") + """
  prefab: {fileID: 1, guid: cccccccccccccccccccccccccccccccc, type: 3}
""", encoding="utf-8")
    put(tmp_path, "Assets/Content.prefab", """--- !u!1 &1
GameObject:
  m_Component:
  - component: {fileID: 2}
  - component: {fileID: 3}
  m_IsActive: 1
--- !u!4 &2
Transform:
  m_GameObject: {fileID: 1}
  m_Father: {fileID: 0}
--- !u!212 &3
SpriteRenderer:
  m_GameObject: {fileID: 1}
  m_Enabled: 1
  m_Sprite: {fileID: 21300000, guid: dddddddddddddddddddddddddddddddd, type: 3}
""")
    put(tmp_path, "Assets/Content.prefab.meta", "guid: " + "c" * 32)
    put(tmp_path, "Assets/Sprite.png", "texture-payload")
    put(tmp_path, "Assets/Sprite.png.meta", "guid: " + "d" * 32)
    result = collect_reference_graph(tmp_path, ["Assets/Entry.unity"])
    assert "Assets/Sprite.png" in result.reachable_files
    assert "Assets/Content.prefab" in result.reachable_files


@pytest.mark.parametrize("bad", ["../outside.unity", "Assets/../../outside.unity", "C:/outside.unity"])
def test_scene_traversal_rejected(tmp_path, bad):
    result = collect_reference_graph(tmp_path, [bad])
    assert not result.complete
    assert not result.reachable_files


def test_bound_exhaustion_is_explicit_not_silent_missing_score(tmp_path):
    scene(tmp_path)
    result = collect_reference_graph(tmp_path, ["Assets/Entry.unity"],
                                     limits=Limits(files=1))
    assert not result.complete
    assert "file enumeration limit exceeded" in result.issues


def test_empty_entry_scene_set_is_incomplete(tmp_path):
    assert not collect_reference_graph(tmp_path, []).complete


def test_invalid_unity_document_ids_are_reported_not_crashed(tmp_path):
    scene(tmp_path)
    entry = tmp_path / "Assets/Entry.unity"
    entry.write_text(entry.read_text(encoding="utf-8") + "\n--- !u!1 &1\nGameObject:\n", encoding="utf-8")
    result = collect_reference_graph(tmp_path, ["Assets/Entry.unity"])
    assert not result.complete
    assert any("document IDs" in issue for issue in result.issues)


def test_text_named_unity_scene_is_not_serialized_evidence(tmp_path):
    put(tmp_path, "Assets/Entry.unity", "camera shader win health movement")
    result = collect_reference_graph(tmp_path, ["Assets/Entry.unity"])
    assert not result.complete
    assert not result.documents


def test_guid_in_name_or_comment_is_not_a_resource_reference(tmp_path):
    scene(tmp_path)
    entry = tmp_path / "Assets/Entry.unity"
    entry.write_text(entry.read_text(encoding="utf-8") + """
  m_Name: 'fake {fileID: 1, guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, type: 3}'
  # fake: {fileID: 1, guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, type: 3}
""", encoding="utf-8")
    put(tmp_path, "Assets/Fake.cs", "class Fake {}")
    put(tmp_path, "Assets/Fake.cs.meta", "guid: " + "b" * 32)
    result = collect_reference_graph(tmp_path, ["Assets/Entry.unity"])
    assert "Assets/Fake.cs" not in result.reachable_files


def test_symlink_excluded(tmp_path):
    scene(tmp_path)
    linked = tmp_path / "Assets/outside.cs"
    try:
        linked.symlink_to(tmp_path / "Assets/Player.cs")
    except OSError:
        pytest.skip("host does not permit unprivileged symlinks")
    result = collect_reference_graph(tmp_path, ["Assets/Entry.unity"])
    assert not result.complete
    assert "Assets/outside.cs" not in result.reachable_files
