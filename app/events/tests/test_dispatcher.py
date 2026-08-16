from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

import pytest

from events.dispatcher import DomainEvent, publish, register_handler


@dataclass(frozen=True, kw_only=True)
class SampleEvent(DomainEvent):
    value: str


class TestEventDispatcher:
    def test_publish_calls_registered_handler(self):
        received: list[SampleEvent] = []

        def handler(event: SampleEvent) -> None:
            received.append(event)

        register_handler(SampleEvent, handler)
        event = SampleEvent(value='test')
        publish(event)

        assert len(received) == 1
        assert received[0].value == 'test'

    def test_publish_only_calls_matching_handlers(self):
        other_received: list[str] = []
        sample_received: list[str] = []

        register_handler(SampleEvent, lambda e: sample_received.append(e.value))

        @dataclass(frozen=True, kw_only=True)
        class OtherEvent(DomainEvent):
            x: int

        register_handler(OtherEvent, lambda e: other_received.append(str(e.x)))

        publish(SampleEvent(value='a'))
        publish(OtherEvent(x=1))

        assert sample_received == ['a']
        assert other_received == ['1']
