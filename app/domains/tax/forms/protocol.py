"""Tax form engine protocol — implementation convention for new forms.

Not a separate architecture (see docs/tax/README.md). PDV implements this
implicitly in accounting/services/tax_forms/pdv/; new forms should implement
TaxFormEngine explicitly.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TaxFormEngine(Protocol):
    """Shared contract for Croatian tax form generators."""

    form_code: str

    def generate(self, period) -> object:
        """Aggregate sources once and produce a versioned form document."""
        ...

    def validate(self, document) -> object:
        """XSD and business rules before submission."""
        ...

    def render_xml(self, document) -> bytes:
        """Derived artifact from canonical payload."""
        ...

    def render_pdf(self, document) -> bytes:
        """Derived artifact from canonical payload."""
        ...

    def parse(self, xml_bytes: bytes) -> object:
        """Parse signed or unsigned XML into form document."""
        ...
