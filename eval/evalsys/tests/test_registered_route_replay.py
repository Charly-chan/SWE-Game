"""Frozen replay preflight must work with the distributed route metadata."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from evalsys.routes import budget, registry, runner, schema
from evalsys.tasks import registry_path


def replay_session(tmp_path, monkeypatch, *, suite=None):
    interface = SimpleNamespace(project_root=tmp_path, provenance=lambda: {})
    session = runner.RouteSession(
        tmp_path, interface=interface,
        dispatch_extended=suite.extended_actions if suite else (),
        dispatch_axes=suite.analog_axes if suite else (),
    )
    dispatched = []

    def run(route, replay_agent, measured, **kwargs):
        # These tests cover preflight and dispatch, not gameplay success.
        ops = kwargs.get("override_ops")
        if ops is None:
            ops = replay_agent.propose({}, {}, [])
        dispatched.append((route.route_id, len(ops)))
        return runner.RouteReading(
            route.route_id, route.tier, reached=True, segment_clean=True,
            used_required=True, steps=len(ops), stop_reason="ops_exhausted",
        )

    monkeypatch.setattr(session, "run", run)
    return session, dispatched


def segment(kind="withheld", *, declared_steps=240):
    return schema.Route(
        route_id="fixture/C1/long_tape", tier=2,
        provenance=schema.Provenance(kind=kind),
        budget=schema.Budget(steps=declared_steps, frames=4000),
        authored_solution={"ops": [{"op": "wait", "frames": 1}] * 236},
    )


def measured_budget(route):
    return budget.derive(
        route.tier, 21.6, declared_steps=route.budget.steps,
        declared_frames=route.budget.frames,
    )


@pytest.mark.parametrize("kind", schema.PROVENANCE_KINDS)
def test_fixed_tape_dispatch_is_independent_of_provenance(kind, tmp_path, monkeypatch):
    route = segment(kind)
    measured = measured_budget(route)
    assert measured.ops == 40
    session, dispatched = replay_session(tmp_path, monkeypatch)

    replay = runner.replay_authored_route(route, session, measured)
    gold = runner.validate_route_on_gold(route, session, measured)

    assert replay.stop_reason != "plan_refused", replay.detail
    assert gold.ok, gold.detail
    assert dispatched == [(route.route_id, 236), (route.route_id, 236)]
    # Online proposals still use their measured operation budget.
    check = session.check_ops(route.authored_solution["ops"], op_cap=measured.ops,
                              frame_cap=measured.frames)
    assert {refusal.reason for refusal in check.refusals} == {"over_op_cap"}


@pytest.mark.parametrize("kind", schema.PROVENANCE_KINDS)
@pytest.mark.parametrize("failure", ["declared_steps", "frames", "action"])
def test_fixed_tape_still_checks_execution_bounds_and_actions(kind, failure, tmp_path, monkeypatch):
    route = segment(kind, declared_steps=235 if failure == "declared_steps" else 240)
    if failure == "frames":
        route = replace(route, authored_solution={
            "ops": [{"op": "wait", "frames": 20}] * 236,
        })
    elif failure == "action":
        route = replace(route, authored_solution={
            "ops": [{"op": "tap", "action": "undeclared_action", "frames": 1}],
        })
    session, dispatched = replay_session(tmp_path, monkeypatch)
    measured = measured_budget(route)
    replay = runner.replay_authored_route(route, session, measured)
    gold = runner.validate_route_on_gold(route, session, measured)

    expected = {"declared_steps": "over_op_cap", "frames": "over_frame_cap",
                "action": "unknown_action"}[failure]
    assert replay.stop_reason == "plan_refused"
    assert expected in {refusal["reason"] for refusal in replay.refusals}
    assert not gold.ok
    assert not dispatched


@pytest.mark.parametrize("kind", schema.PROVENANCE_KINDS)
@pytest.mark.parametrize("tier,declared_steps", [(2, 0), (5, 200)])
def test_tapes_without_a_segment_step_limit_remain_frame_bounded(
    kind, tier, declared_steps, tmp_path, monkeypatch,
):
    route = replace(segment(kind, declared_steps=declared_steps), tier=tier)
    session, dispatched = replay_session(tmp_path, monkeypatch)
    reading = runner.replay_authored_route(route, session, measured_budget(route))
    assert reading.stop_reason != "plan_refused", reading.detail
    assert dispatched == [(route.route_id, 236)]


_REGISTERED_GAMES = sorted(json.loads(registry_path().read_text())["tasks"])


@pytest.mark.parametrize("game_id", _REGISTERED_GAMES)
def test_every_distributed_certified_tape_reaches_execution_preflight(game_id, tmp_path, monkeypatch):
    suite = registry.load_registered_routes(game_id)
    assert suite is not None
    assert suite.certified_ids
    session, dispatched = replay_session(tmp_path, monkeypatch, suite=suite)

    for route in suite.routes:
        if route.route_id not in suite.certified_ids:
            continue
        measured = measured_budget(route)
        replay = runner.replay_authored_route(route, session, measured)
        gold = runner.validate_route_on_gold(route, session, measured)
        assert replay.stop_reason != "plan_refused", (route.route_id, replay.detail)
        assert gold.ok, (route.route_id, gold.detail)

    assert len(dispatched) == 2 * len(suite.certified_ids)
