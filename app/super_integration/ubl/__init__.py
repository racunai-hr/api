"""Deprecated shim — koristiti ubl.parser."""

from ubl.parser.invoice import ParsedInvoice, parse_invoice_ubl

__all__ = ['ParsedInvoice', 'parse_invoice_ubl', 'build_invoice_ubl']

from super_integration.ubl.builder import build_invoice_ubl  # noqa: E402
