

from dataclasses import replace
from pathlib import Path

import pytest

from evalsys.routes import budget, runner, schema


def campaign():
    return schema.Route(
        route_id="fixture/L5/campaign", tier=5,
        goal=schema.Goal("whole_game_clear()"),
        budget=schema.Budget(steps=20, frames=1800),
        required_devices=["L1/goal#0", "L2/goal#0"],
    )


def plan(route):
    return runner.build_plan(
        route, budget.derive(5, 21.6), [],
        scene="res://level_1.tscn", io_dir="/unused",
        declared_levels=["res://level_1.tscn", "res://level_2.tscn"],
        success_scenes=["res://stage_cleared.tscn"],
    )


def test_default_route_and_plan_preserve_existing_serialization():
    route = campaign()
    assert route.success_scope == "declared_ending"
    assert "success_scope" not in route.to_dict()
    assert "success_scope" not in plan(route)
    restored = schema.Route.from_dict(route.to_dict())
    assert restored.success_scope == "declared_ending"
    assert restored.to_dict() == route.to_dict()


def test_explicit_campaign_scope_roundtrips_and_reaches_driver():
    route = replace(campaign(), success_scope="all_declared_levels")
    assert route.validate() == []
    restored = schema.Route.from_dict(route.to_dict())
    assert restored.success_scope == "all_declared_levels"
    assert plan(restored)["success_scope"] == "all_declared_levels"


def test_scope_rejects_typos_and_non_campaign_routes():
    assert any("unsupported success_scope" in issue
               for issue in replace(campaign(), success_scope="all").validate())
    assert any("requires tier 5" in issue
               for issue in replace(campaign(), tier=2, success_scope="all_declared_levels").validate())


def test_driver_defers_success_only_under_explicit_campaign_scope():
    body = runner.ROUTE_DRIVER.read_text(encoding="utf-8")
    start = body.index("elif _success_scenes.has(scene):")
    branch = body[start:body.index("elif _continuation_scenes.has(scene):", start)]
    assert '_plan.get("success_scope", "declared_ending")) == "all_declared_levels"' in branch
    assert "and _visited_levels.size() < _required_level_count():" in branch
    assert "pass\n            else:" in branch
    assert "_finish_whole_game_success()" in branch.split("else:", 1)[1]


@pytest.mark.parametrize("game", ["harvest_ledger", "pixel_platformer", "racing", "tiny_rts", "twin_holds"])
def test_shared_result_campaigns_explicitly_opt_into_scope(game):
    root = Path(__file__).resolve().parents[3]
    routes = schema.load_route_file(root / f"eval/tasks/{game}/route.json")
    for route in routes:
        assert route.success_scope == ("all_declared_levels" if route.tier == 5 else "declared_ending")


@pytest.mark.parametrize("game", ["pixel_platformer", "racing"])
def test_shared_run_complete_alias_still_uses_explicit_campaign_scope(game):
    root = Path(__file__).resolve().parents[3]
    session = runner.RouteSession(root / f"games/{game}")
    vocabulary = session.ending_scenes()
    endings = session.interface.endings.scenes
    assert endings["victory"] == endings["run_complete"]
    assert vocabulary.success == [endings["run_complete"]]
    assert vocabulary.continuation == ()
    route = next(r for r in schema.load_route_file(root / f"eval/tasks/{game}/route.json") if r.tier == 5)
    assert plan(route)["success_scope"] == "all_declared_levels"


def test_twin_holds_campaign_keeps_distinct_same_scene_stage_values():
    root = Path(__file__).resolve().parents[3]
    session = runner.RouteSession(root / "games/twin_holds")
    route = next(r for r in schema.load_route_file(root / "eval/tasks/twin_holds/route.json") if r.tier == 5)
    entry = session.level_entry()
    assert len(session.level_scenes()) == 1
    assert entry["selector"] == "Session.level"
    assert entry["values"] == [1, 2, 3]
    actual_plan = runner.build_plan(
        route, budget.derive(5, 21.6), [], scene=session.level_scenes()[0],
        io_dir="/unused", declared_levels=session.level_scenes(), level_entry=entry,
    )
    assert actual_plan["success_scope"] == "all_declared_levels"
    assert actual_plan["level_entry"]["values"] == [1, 2, 3]
    body = runner.ROUTE_DRIVER.read_text(encoding="utf-8")
    assert '_visited_levels["%s#%d" % [scene, index + 1]] = true' in body
