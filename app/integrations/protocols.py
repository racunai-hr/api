from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IntegrationProviderConfig(Protocol):
    tenant: object
    is_active: bool


@runtime_checkable
class EracunConnector(Protocol):
    def send_outbound(self, invoice, document, ubl_xml: str) -> object: ...

    def sync_inbound(self) -> int: ...

    def poll_outbound_statuses(self) -> int: ...

    def report_payment(self, invoice) -> object | None: ...


@runtime_checkable
class FiscalizationConnector(Protocol):
    def fiscalize(self, invoice, document, ubl_xml: str) -> object: ...
