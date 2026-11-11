


from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ...verdict import Item

if TYPE_CHECKING:
    from ...truth.expectations import Expectations
    from ...truth.snapshot import TruthSnapshot


AuthoredFn = Callable[["TruthSnapshot", "Expectations"], list[Item]]

REGISTRY: dict[str, AuthoredFn] = {}


def register(task_id: str, fn: AuthoredFn) -> AuthoredFn:

    REGISTRY[task_id] = fn
    return fn


def evaluate_authored(
    snapshot: "TruthSnapshot",
    expected: "Expectations | None" = None,
    *,
    task_id: str | None = None,
) -> list[Item]:


    if not task_id:
        return []
    fn = REGISTRY.get(task_id)
    if fn is None:
        return []
    from ...truth.expectations import Expectations

    exp = expected if expected is not None else Expectations(project=task_id)
    return list(fn(snapshot, exp))


def registered_task_ids() -> tuple[str, ...]:
    return tuple(sorted(REGISTRY))
