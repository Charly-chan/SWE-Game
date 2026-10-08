

from dataclasses import replace

from evalsys.routes import agent, budget, runner, schema


def transaction():
    return schema.Route(
        route_id="fixture/repair/two_runs", tier=2,
        goal=schema.Goal("whole_game_clear()", milestones=[
            {"name": "first_victory", "predicate": "whole_game_clear()"},
            {"name": "fresh_second_run", "predicate": "device_numeric(movplat, 0, run_number) == 2 AND numeric(score) == 0"},
        ]),
        budget=schema.Budget(steps=10, frames=180),
        required_devices=["hazard#0"],
    )


def plan(route):
    return runner.build_plan(
        route, budget.derive(2, 2.0), [],
        scene="res://main.tscn", io_dir="/unused",
        declared_levels=["res://main.tscn"],
        success_scenes=["res://victory.tscn"],
    )


def test_default_success_continuation_is_absent_from_serialized_routes_and_plan():
    route = transaction()
    assert route.continue_after_success is False
    assert "continue_after_success" not in route.to_dict()
    assert "continue_after_success" not in plan(route)
    assert schema.Route.from_dict(route.to_dict()).to_dict() == route.to_dict()


def test_explicit_success_continuation_roundtrips_only_to_evaluator_plan():
    route = replace(transaction(), continue_after_success=True)
    assert route.validate() == []
    restored = schema.Route.from_dict(route.to_dict())
    assert restored.continue_after_success is True
    assert plan(restored)["continue_after_success"] is True
    assert "continue_after_success" not in schema.player_view(restored)
    assert agent.validate_ops([{"op": "continue_after_success", "frames": 1}]).refusals


def test_success_continuation_cannot_change_campaign_certification_semantics():
    route = replace(transaction(), tier=5, continue_after_success=True)
    assert any("requires a segment transaction" in finding for finding in route.validate())


def test_fixed_success_transaction_uses_declared_tape_cap_not_online_op_cap():
    route = replace(transaction(), continue_after_success=True,
                    budget=schema.Budget(steps=240, frames=4000))
    measured = budget.derive(2, 21.6, declared_steps=240, declared_frames=4000)
    assert measured.ops == 40
    assert runner._authored_op_limit(route, measured) == 240
    assert runner._authored_op_limit(replace(route, continue_after_success=False), measured) == 40
    assert measured.frames == 4000
    ops = [{"op": "wait", "frames": 1}] * 229
    assert not agent.validate_ops(ops, op_cap=runner._authored_op_limit(route, measured), frame_cap=measured.frames).refusals
    assert agent.validate_ops(ops, op_cap=measured.ops, frame_cap=measured.frames).refusals
    assert agent.validate_ops(ops + [{"op": "wait", "frames": 1}] * 12,
                              op_cap=runner._authored_op_limit(route, measured), frame_cap=measured.frames).refusals
    assert agent.validate_ops([{"op": "wait", "frames": 4001}],
                              op_cap=runner._authored_op_limit(route, measured), frame_cap=measured.frames).refusals


def test_first_victory_does_not_satisfy_ordered_two_run_goal():
    route = replace(transaction(), continue_after_success=True)
    report = {
        "rows": [
            {"f": 0, "g": {}, "n": {"score": 3}, "d": {}, "wgc": True},
        ],
        "stop_reason": "ops_exhausted", "group_totals": {},
    }
    reading = runner.reading_from_report(route, report)
    assert reading.reached is False
    assert reading.milestones_reached == ["first_victory"]


def test_rebound_observer_and_second_victory_complete_ordered_goal():
    route = replace(transaction(), continue_after_success=True)
    report = {
        "rows": [
            {"f": 0, "g": {}, "n": {"score": 3}, "d": {}, "wgc": True},
            {"f": 1, "g": {}, "n": {"score": 0}, "d": {"movplat#0": {"present": True, "run_number": 2}}},
            {"f": 2, "g": {}, "n": {"score": 3}, "d": {}, "wgc": True},
        ],
        "stop_reason": "ops_exhausted", "group_totals": {},
    }
    reading = runner.reading_from_report(route, report)
    assert reading.reached is True
    assert reading.goal_frame == 2
    assert reading.milestones_reached == ["first_victory", "fresh_second_run"]


def test_transaction_flag_does_not_become_a_submission_input_operation():
    accepted = agent.validate_ops([
        {"op": "wait", "frames": 1, "continue_after_success": True},
    ])
    assert not accepted.refusals
    assert accepted.accepted[0].to_dict() == {"op": "wait", "frames": 1}


def test_absent_old_main_cannot_count_as_observed_fresh_zero_counters():
    predicate = schema.validate_predicate(
        "device_present(movplat, 0) AND numeric(score) == 0 "
        "AND device_numeric(movplat, 0, deaths) == 0"
    ).predicate
    assert predicate is not None
    assert predicate.evaluate({
        "numeric": {"score": 0},
        "devices": {"movplat#0": {"present": False}},
    }) is False
    assert predicate.evaluate({
        "numeric": {"score": 0},
        "devices": {"movplat#0": {"present": True, "deaths": 0}},
    }) is True


def test_success_transaction_driver_rebinds_live_scene_and_keeps_real_inputs():
    source = runner.ROUTE_DRIVER.read_text(encoding="utf-8")
    block = source.split("func _tick_success_transaction(", 1)[1].split("\n\nfunc ", 1)[0]
    assert "_build_device_index()" in block
    assert '_whole_game_outcome = ""' in block
    assert '_whole_game_clear = false' in block
    assert '_rows[_rows.size() - 1]["wgc"] = true' in block
    assert "_advance_ops()" in block
    assert "_try_entry()" not in block
    assert "change_scene_to_file" not in block
