
from unittest.mock import patch

from evalsys.routes import runner
from evalsys.routes.budget import derive
from evalsys.routes.schema import Budget, Goal, Route, RouteStart


def test_taps_cover_the_exact_control_duration():
    for frames in (1, 6, 7, 420):
        ops = runner._liveness_tap_ops("gb_left", frames)
        assert sum(op.frames for op in ops) == frames
        assert all(op.action == "gb_left" for op in ops if op.op == "hold")
        assert all(op.frames == 1 for op in ops if op.op == "hold")
        assert all(1 <= op.frames <= 5 for op in ops if op.op == "wait")


def test_edge_triggered_response_is_compared_to_idle_not_to_hold():
    route = Route(route_id="timing", tier=0, start=RouteStart(level=0),
                  goal=Goal(""), budget=Budget(steps=0, frames=420))
    calls = []

    class Session:
        def run(self, route, agent, budget, **kwargs):
            calls.append(kwargs)
            return runner.RouteReading(route_id=route.route_id, tier=0,
                                       stop_reason="budget_frames")

    idle_rows, hold_rows, tap_rows = [{"which": "idle"}], [{"which": "hold"}], [{"which": "tap"}]

    def judge(rows, baseline, **kwargs):
        assert baseline is idle_rows
        return runner.Liveness(action=kwargs["action"], live=rows is tap_rows,
                               via="state" if rows is tap_rows else "none")

    with patch.object(runner, "_trace_rows", side_effect=[idle_rows, hold_rows, tap_rows]), \
         patch.object(runner, "liveness_over_trace", side_effect=judge):
        result = runner.run_l0(Session(), route, derive(0, 1.0, 1.0), ["gb_left"])
    assert result.live_actions == ["gb_left"]
    assert not result.dead_actions
    assert len(calls) == 3
    assert all(call["override_frames"] == 420 for call in calls)
    assert len({call["idle_baseline_id"] for call in calls}) == 1
    assert "repeated taps" in result.liveness[0].detail
