"""Deprecated shim — koristiti ubl.parser."""

from ubl.parser.invoice import ParsedInvoice, parse_invoice_ubl

__all__ = ['ParsedInvoice', 'parse_invoice_ubl']
