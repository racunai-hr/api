from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar('T', bound='DomainEvent')


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    """Bazni tip za domenske događaje."""


_registry: dict[type[DomainEvent], list[Callable[[DomainEvent], None]]] = defaultdict(list)


def register_handler(event_type: type[T], handler: Callable[[T], None]) -> None:
    _registry[event_type].append(handler)  # type: ignore[arg-type]


def publish(event: DomainEvent) -> None:
    for handler in _registry[type(event)]:
        handler(event)
