from pathlib import Path

import pytest

from evalsys.taskgen.mode5.evidence import collect_reference_graph
from evalsys.taskgen.mode5.source import inspect_sources, supports_predicate
from evalsys.taskgen.mode5.reference import collect_reference, role_layout_support
from test_mode5_evidence import scene, put


def inspect(root, body, helpers=""):
    scene(root)
    put(root, "Assets/Player.cs", "class Player { float health; void Update() {\n" + body + "\n } " + helpers + " }")
    graph = collect_reference_graph(root, ["Assets/Entry.unity"])
    return inspect_sources(root, graph)


def test_reachable_numeric_change_supports_specific_direction(tmp_path):
    facts = inspect(tmp_path, 'Damage(); GBState.ReportNumeric("health", health);', 'void Damage() { health--; }')
    assert supports_predicate("numeric_delta(health) < 0", facts)
    assert not supports_predicate("numeric_delta(health) > 0", facts)
    assert not supports_predicate("numeric_delta(score) > 0", facts)
    assert len(facts.methods) == 2


@pytest.mark.parametrize("body,helpers", [
    ('// health--; GBState.ReportNumeric("health", health);\n', ''),
    ('var fake = @"health--; ReportNumeric(""health"", health);";', ''),
    ('if (false) { health--; GBState.ReportNumeric("health", health); }', ''),
    ('if (false) GBState.ReportNumeric("health", health); health--;', ''),
    ('while (false) { health--; GBState.ReportNumeric("health", health); }', ''),
    ('return; health--; GBState.ReportNumeric("health", health);', ''),
    ('', 'void Uncalled() { health--; GBState.ReportNumeric("health", health); }'),
    ('#if NEVER_DEFINED\nhealth--; GBState.ReportNumeric("health", health);\n#endif\n', ''),
])
def test_fake_or_dead_source_no_credit(tmp_path, body, helpers):
    facts = inspect(tmp_path, body, helpers)
    assert not facts.numeric_changes


def test_unattached_script_not_execution_root(tmp_path):
    inspect(tmp_path, '')
    put(tmp_path, "Assets/Fake.cs", 'class Fake { void Update() { GBOutcome.ReportSuccess(); } }')
    facts = inspect_sources(tmp_path, collect_reference_graph(tmp_path, ["Assets/Entry.unity"]))
    assert not facts.outcomes


def test_other_class_in_attached_file_not_execution_root(tmp_path):
    inspect(tmp_path, '')
    put(tmp_path, "Assets/Player.cs", 'class Player { void Update() {} } class Fake { void Update() { GBOutcome.ReportSuccess(); } }')
    facts = inspect_sources(tmp_path, collect_reference_graph(tmp_path, ["Assets/Entry.unity"]))
    assert not facts.outcomes


def test_same_class_name_in_unattached_file_not_execution_root(tmp_path):
    inspect(tmp_path, '')
    put(tmp_path, "Assets/Unused/Player.cs", 'namespace Other { class Player { void Update() { GBOutcome.ReportSuccess(); } } }')
    facts = inspect_sources(tmp_path, collect_reference_graph(tmp_path, ["Assets/Entry.unity"]))
    assert not facts.outcomes


def test_explicit_runtime_initializer_is_execution_root(tmp_path):
    inspect(tmp_path, '')
    put(tmp_path, "Assets/Bootstrap.cs", 'class Bootstrap { [UnityEngine.RuntimeInitializeOnLoadMethod] static void Install() { GBOutcome.ReportSuccess(); } }')
    facts = inspect_sources(tmp_path, collect_reference_graph(tmp_path, ["Assets/Entry.unity"]))
    assert facts.outcomes["success"]


def test_runtime_initializer_name_without_attribute_is_not_root(tmp_path):
    inspect(tmp_path, '')
    put(tmp_path, "Assets/Bootstrap.cs", 'class Bootstrap { static void RuntimeInitializeOnLoadMethod() { GBOutcome.ReportSuccess(); } }')
    facts = inspect_sources(tmp_path, collect_reference_graph(tmp_path, ["Assets/Entry.unity"]))
    assert not facts.outcomes


def test_unknown_and_negative_numeric_rhs_not_positive_direction(tmp_path):
    facts = inspect(tmp_path, 'health += -5; GBState.ReportNumeric("health", health);')
    assert not supports_predicate("numeric_delta(health) > 0", facts)
    facts = inspect(tmp_path, 'health += damage; GBState.ReportNumeric("health", health);')
    assert not supports_predicate("numeric_delta(health) > 0", facts)


def test_receiver_call_does_not_activate_same_named_helper(tmp_path):
    facts = inspect(tmp_path, 'other.Damage(); GBState.ReportNumeric("health", health);', 'void Damage() { health--; }')
    assert not facts.numeric_changes


@pytest.mark.parametrize("rhs", ["0", "0.0f", "00.000", "-0.0", "1 - 1000", "1 + negative"])
def test_zero_numeric_update_cannot_verify_direction(tmp_path, rhs):
    facts = inspect(tmp_path, f'health += {rhs}; GBState.ReportNumeric("health", health);')
    assert not supports_predicate("numeric_delta(health) > 0", facts)
    assert not supports_predicate("numeric_delta(health) < 0", facts)


def test_connected_ui_restart_supports_state_reset(tmp_path):
    facts = inspect(tmp_path, 'button.onClick.AddListener(Restart);',
                    'void Restart() { progress = 0; health = 100; player.transform.position = spawn; }')
    assert facts.flow["reset"]
    facts = inspect(tmp_path, '', 'void Restart() { progress = 0; health = 100; player.transform.position = spawn; }')
    assert not facts.flow


def test_explicit_procedural_component_is_execution_root(tmp_path):
    inspect(tmp_path, 'gameObject.AddComponent<Controller>();')
    put(tmp_path, "Assets/Controller.cs", 'class Controller { void Start() { GBOutcome.ReportSuccess(); } }')
    facts = inspect_sources(tmp_path, collect_reference_graph(tmp_path, ["Assets/Entry.unity"]))
    assert facts.outcomes["success"]


def test_typeof_is_not_component_construction(tmp_path):
    inspect(tmp_path, 'var t = typeof(Controller);')
    put(tmp_path, "Assets/Controller.cs", 'class Controller { void Start() { GBOutcome.ReportSuccess(); } }')
    facts = inspect_sources(tmp_path, collect_reference_graph(tmp_path, ["Assets/Entry.unity"]))
    assert not facts.outcomes


def test_dynamic_role_requires_real_entity_construction(tmp_path):
    facts = inspect(tmp_path, 'var marker = gameObject.AddComponent<GBEntity>(); marker.Configure("gb_enemy", "enemy");')
    assert facts.dynamic_roles["gb_enemy"]
    facts = inspect(tmp_path, 'var marker = new Other(); marker.Configure("gb_enemy", "enemy");')
    assert not facts.dynamic_roles


def test_static_input_requires_real_read_consumer(tmp_path):
    facts = inspect(tmp_path, 'var move = map.FindAction("Player/gb_left"); if (move.IsPressed()) Move();')
    assert facts.consumed_actions["gb_left"]
    facts = inspect(tmp_path, 'var move = map.FindAction("Player/gb_left");')
    assert not facts.consumed_actions


def test_reference_applicability_not_all_effect_types(tmp_path):
    put(tmp_path, 'main.tscn', '[node name="World" type="Node2D"]\n[node name="Sprite" type="AnimatedSprite2D" parent="."]\n')
    facts = collect_reference(tmp_path, {})
    assert facts["feedback_requirements"] == ["animation"]
    assert "audio" not in facts["feedback_requirements"]
    assert "particles" not in facts["feedback_requirements"]
    assert "material" not in facts["render_requirements"]
    assert "interactive_ui" not in facts["ui_requirements"]


def test_role_order_not_pixel_identity():
    ref = {"gb_player": [[0, 0]], "gb_goal": [[10, 3]]}
    assert role_layout_support(ref, {"gb_player": [[100, 2]], "gb_goal": [[120, 4]]})
    assert not role_layout_support(ref, {"gb_player": [[100, 2]], "gb_goal": [[90, 4]]})
    assert not role_layout_support(ref, {"gb_player": [[100, 2]]})


@pytest.mark.parametrize("points", [[], [[float("nan"), 0]], [[True, 0]], [[0]], "fake"])
def test_layout_requires_finite_real_coordinates(points):
    ref = {"gb_player": [[0, 0]], "gb_goal": [[10, 3]]}
    assert not role_layout_support(ref, {"gb_player": points, "gb_goal": [[20, 3]]})


def test_destroy_is_not_population_increase(tmp_path):
    facts = inspect(tmp_path, 'if (entity.Role == "gb_enemy") Destroy(entity.gameObject);')
    assert supports_predicate("count_delta(gb_enemy) < 0", facts)
    assert not supports_predicate("count_delta(gb_enemy) > 0", facts)
