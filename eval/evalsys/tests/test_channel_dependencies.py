from __future__ import annotations

import unittest

from evalsys.interface.contract import CHANNEL_INPUTS
from evalsys.weights import CHANNELS, O2_LADDER


class ChannelDependencyTests(unittest.TestCase):
    def test_every_weighted_channel_declares_inputs(self) -> None:
        self.assertEqual(set(CHANNELS), set(CHANNEL_INPUTS))
        for channel, inputs in CHANNEL_INPUTS.items():
            self.assertEqual({"submission", "evaluator"}, set(inputs), channel)

    def test_o2_ladder_is_only_current_interface_vocabulary(self) -> None:
        ids = [gate.id for gate in O2_LADDER.gates]
        self.assertEqual(
            ["groups", "actions", "levels", "endings", "optional_fields_valid",
             "task_required_observables"],
            ids,
        )
        forbidden = ("bridge", "cf10", "b1", "b2", "b3", "b4")
        self.assertFalse(any(token in item.lower() for item in ids for token in forbidden))
        self.assertAlmostEqual(1.0, sum(gate.credit for gate in O2_LADDER.gates))


if __name__ == "__main__":
    unittest.main()
