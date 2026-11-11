

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


WITHDRAWN_CHANNELS = frozenset({"O10"})


@dataclass(frozen=True)
class ChannelRequirement:
    required: bool = False
    fields: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"required": self.required, "fields": list(self.fields), "reason": self.reason}


@dataclass(frozen=True)
class TaskInterfaceRequirements:
    channels: Mapping[str, ChannelRequirement]

    @classmethod
    def empty(cls) -> "TaskInterfaceRequirements":
        return cls(MappingProxyType({}))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "TaskInterfaceRequirements":
        channels = {
            str(channel): ChannelRequirement(
                required=bool(value.get("required", False)),
                fields=tuple(str(field) for field in value.get("fields") or ()),
                reason=str(value.get("reason", "")),
            )
            for channel, value in (raw or {}).items()
            if isinstance(value, Mapping) and str(channel) not in WITHDRAWN_CHANNELS
        }
        return cls(MappingProxyType(channels))

    def for_channel(self, channel: str) -> ChannelRequirement:
        return self.channels.get(channel, ChannelRequirement())

    def required_fields(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            field
            for requirement in self.channels.values() if requirement.required
            for field in requirement.fields
        ))

    def to_dict(self) -> dict[str, Any]:
        return {key: value.to_dict() for key, value in sorted(self.channels.items())}


def derive_requirements(*, anchor_registered: bool) -> TaskInterfaceRequirements:


    return TaskInterfaceRequirements.from_dict({
        "O9": {
            "required": anchor_registered,
            "fields": ["anchor_camera"] if anchor_registered else [],
            "reason": (
                "reference has registered anchor composition expectations"
                if anchor_registered else "reference has no registered anchor expectation"
            ),
        },
    })
