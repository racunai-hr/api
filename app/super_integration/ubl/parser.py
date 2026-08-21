"""Deprecated shim — koristiti ubl.parser."""

from ubl.parser.invoice import ParsedInvoice, UblMonetaryError, parse_invoice_ubl

__all__ = ['ParsedInvoice', 'UblMonetaryError', 'parse_invoice_ubl']
