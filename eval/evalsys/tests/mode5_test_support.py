"""Private controller fixtures for testing the single release adapter."""
from evalsys.taskgen.mode5.adapter import SNAPSHOT_SCHEMA, frozen_obligations
from evalsys.taskgen.mode5.score import REGISTRY_VERSION, CRITERIA


def controller_engine():
    rubric = {"required_actions": ["gb_left"], "required_groups": ["gb_player"],
              "required_numeric_slots": ["health"], "min_levels": 1,
              "mechanic_checks": [{"id": "damage", "observable": {"predicate": "numeric_delta(health) < 0"}}]}
    obligations = frozen_obligations(rubric)
    return {"build": {"status": "fail", "attribution": "submission"},
            "mode5_static": {
                "schema": SNAPSHOT_SCHEMA, "registry_version": REGISTRY_VERSION,
                "obligations": obligations, "graph": {"complete": True},
                "observations": {name: [] if name == "playability.final_goal" else [
                    {"obligation": unit, "level": "static_supported", "references": ["controller/test"], "coverage": 1}
                    for unit in obligations[name]] for name in CRITERIA},
            }}


def summary_reading(game, total):
    from test_mode5_scoring import record
    reading = record(game)
    # Keep visual's static ceiling; normalize other components to reconcile.
    visual = min(60, total)
    other = (total - .15 * visual) / .85
    for name, component in reading["components"].items():
        component["score"] = visual if name == "visual" else other
        component["points"] = component["score"] * component["weight"] / 100
    reading["total"] = total
    return reading
