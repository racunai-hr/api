"""Deprecated shim — koristiti ubl paket."""

from ubl.builder.invoice import build_invoice_ubl as _build_invoice_ubl
from ubl.domain.document import UblDocument
from ubl.serializers.from_invoice import invoice_to_ubl_document


def build_invoice_ubl(invoice, company, partner) -> str:
    """Backward-compatible wrapper oko HR CIUS buildera."""
    document = invoice_to_ubl_document(invoice, company, partner)
    return _build_invoice_ubl(document)
