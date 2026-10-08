from __future__ import annotations
import importlib.util
import unittest
from pathlib import Path
from evalsys.interface.contract import ACTIONS, CHANNEL_INPUTS, FAILURE_ENDINGS, GROUPS, SUCCESS_ENDINGS, contract, PREDICATE_FUNCTIONS
from evalsys.routes.schema import PREDICATE_FUNCTIONS as ROUTE_PREDICATE_FUNCTIONS

class InterfaceContractTests(unittest.TestCase):

    def test_python_vocabulary_and_dependencies_are_registry_owned(self) -> None:
        raw = contract()
        self.assertEqual(tuple(raw['actions']), ACTIONS)
        self.assertEqual(tuple(raw['groups']['required'] + raw['groups']['optional']), GROUPS)
        self.assertEqual(frozenset(raw['endings']['success']), SUCCESS_ENDINGS)
        self.assertEqual(frozenset(raw['endings']['failure']), FAILURE_ENDINGS)
        self.assertEqual(dict(raw['channel_inputs']), dict(CHANNEL_INPUTS))
        self.assertEqual(dict(raw['predicate_functions']), dict(PREDICATE_FUNCTIONS))
        self.assertEqual(dict(PREDICATE_FUNCTIONS), ROUTE_PREDICATE_FUNCTIONS)
if __name__ == '__main__':
    unittest.main()
