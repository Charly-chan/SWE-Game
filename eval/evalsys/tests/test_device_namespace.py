


from __future__ import annotations

import unittest

from evalsys.routes import schema
from evalsys.routes.runner import reading_from_report
from evalsys.routes.schema import DEVICE_ID


def _l5_route(required: list[str]) -> schema.Route:
    return schema.Route(
        route_id="3d_platformer/L5/whole_game_clear",
        tier=5,
        start=schema.RouteStart(level=0),
        goal=schema.Goal("whole_game_clear()"),
        budget=schema.Budget(steps=8, frames=400),
        required_devices=required,
        authored_solution={"ops": [{"op": "wait", "frames": 60}]},
    )


def _report(contacts: dict[str, int], *, index: dict[str, str] | None = None) -> dict:

    keys = index if index is not None else {k: k for k in contacts}
    return {
        "stop_reason": "goal_reached",
        "steps": 8,
        "deaths": 0,
        "injects": 0,
        "device_contacts": dict(contacts),
        "device_index": dict(keys),
        "rows": [{"f": 0, "g": {}, "wgc": True}],
        "groups": {},
        "errors": [],
    }


class DeviceIdGrammar(unittest.TestCase):
    def test_qualified_and_bare_ids_are_both_legal(self) -> None:
        self.assertTrue(DEVICE_ID.match("beacon#0"))
        self.assertTrue(DEVICE_ID.match("L3/beacon#0"))
        self.assertTrue(DEVICE_ID.match("L12/movplat#7"))

    def test_malformed_qualifiers_are_refused(self) -> None:
        for bad in ("L/beacon#0", "3/beacon#0", "L3/beacon", "L3//beacon#0"):
            self.assertIsNone(DEVICE_ID.match(bad), bad)


class UsedRequiredIsLevelScoped(unittest.TestCase):
    REQUIRED = ["L3/beacon#0", "L2/movplat#0", "L3/spring#0", "L3/door#0"]

    def test_level_one_contacts_do_not_satisfy_level_three(self) -> None:

        reading = reading_from_report(
            _l5_route(self.REQUIRED),
            _report({"L1/beacon#0": 4, "L1/movplat#0": 2, "L1/spring#0": 1}),
        )
        self.assertFalse(
            reading.used_required,
            "contacts recorded in level 1 satisfied a level-3 requirement",
        )
        self.assertEqual(reading.devices_used, [])

    def test_contacts_in_the_named_levels_do_satisfy_it(self) -> None:
        reading = reading_from_report(
            _l5_route(self.REQUIRED),
            _report({d: 1 for d in self.REQUIRED}),
        )
        self.assertTrue(reading.used_required)
        self.assertEqual(sorted(reading.devices_used), sorted(self.REQUIRED))

    def test_one_missing_level_three_device_is_enough_to_fail(self) -> None:
        partial = {d: 1 for d in self.REQUIRED if d != "L3/door#0"}
        reading = reading_from_report(_l5_route(self.REQUIRED), _report(partial))
        self.assertFalse(reading.used_required)
        self.assertNotIn("L3/door#0", reading.devices_used)

    def test_bare_ids_would_have_accepted_the_level_one_contact(self) -> None:


        bare = ["beacon#0", "movplat#0", "spring#0"]
        reading = reading_from_report(
            _l5_route(bare),
            _report({"beacon#0": 4, "movplat#0": 2, "spring#0": 1}),
        )
        self.assertTrue(
            reading.used_required,
            "this is the OLD behaviour and must stay reproducible as evidence",
        )


class RegisteredRouteUsesQualifiedIds(unittest.TestCase):
    def test_the_frozen_l5_requires_level_qualified_devices(self) -> None:

        from pathlib import Path

        from evalsys.routes.schema import load_route_file

        path = Path(__file__).resolve().parents[2] / "tasks" / "3d_platformer" / "route.json"
        if not path.is_file():
            self.skipTest(f"no route file at {path}")
        l5 = [r for r in load_route_file(path) if r.tier == 5]
        self.assertTrue(l5, "no L5 route in the registered suite")
        required = l5[0].required_devices
        self.assertTrue(required, "L5 declares no required devices at all")
        unqualified = [d for d in required if "/" not in d]
        self.assertEqual(
            unqualified,
            [],
            f"L5 still requires bare device ids {unqualified}; a contact in any "
            "level would satisfy them",
        )


if __name__ == "__main__":
    unittest.main()
