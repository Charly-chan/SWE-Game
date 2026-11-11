

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


def contract_path() -> Path:
    return Path(__file__).resolve().parents[3] / "interface" / "contract.v2.json"


@lru_cache(maxsize=1)
def contract() -> Mapping[str, Any]:
    raw = json.loads(contract_path().read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or int(raw.get("interface_version", 0)) != 2:
        raise ValueError(f"invalid submission contract registry: {contract_path()}")
    return MappingProxyType(raw)


_CONTRACT = contract()
INTERFACE_VERSION = int(_CONTRACT["interface_version"])
GROUPS = tuple(_CONTRACT["groups"]["required"] + _CONTRACT["groups"]["optional"])
REQUIRED_GROUPS = tuple(_CONTRACT["groups"]["required"])
ACTIONS = tuple(_CONTRACT["actions"])
SUCCESS_ENDINGS = frozenset(_CONTRACT["endings"]["success"])
FAILURE_ENDINGS = frozenset(_CONTRACT["endings"]["failure"])
NUMERIC_SLOTS = frozenset(_CONTRACT["numeric_slots"])
DEVICE_KINDS = frozenset(_CONTRACT["device_kinds"])
DEVICE_ID_PATTERN = str(_CONTRACT["device_id_pattern"])
CHANNEL_INPUTS = MappingProxyType(dict(_CONTRACT["channel_inputs"]))
EXTENDED_ACTION_ID_PATTERN = str(
    _CONTRACT.get("extended_action_id_pattern", r"^[a-z][a-z0-9_]{1,31}$")
)
EXTENDED_ACTIONS_MAX = int(_CONTRACT.get("extended_actions_max", 16))
EXTENDED_ACTION_WHY_MIN_LENGTH = int(_CONTRACT.get("extended_action_why_min_length", 8))


EXTENDED_ACTION_RESERVED_PREFIXES = tuple(
    str(item) for item in _CONTRACT.get("extended_action_reserved_prefixes", ("gb_", "ui_"))
)
EXTENDED_ACTION_RESERVED_NAMES = tuple(
    str(item)
    for item in _CONTRACT.get("extended_action_reserved_names", ("pause", "reset", "quit", "exit"))
)
ANALOG_AXIS_ID_PATTERN = str(
    _CONTRACT.get("analog_axis_id_pattern", EXTENDED_ACTION_ID_PATTERN)
)
ANALOG_AXES_MAX = int(_CONTRACT.get("analog_axes_max", 4))
ANALOG_AXIS_STEPS_MIN = int(_CONTRACT.get("analog_axis_steps_min", 2))
ANALOG_AXIS_STEPS_MAX = int(_CONTRACT.get("analog_axis_steps_max", 33))
PREDICATE_FUNCTIONS = MappingProxyType(dict(_CONTRACT["predicate_functions"]))
PREDICATE_EXAMPLES = tuple(_CONTRACT["predicate_examples"])
