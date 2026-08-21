"""Reporting facade over the canonical Invoice PDF renderer."""

from __future__ import annotations

from domains.reporting.documents.attachments import safe_download_filename

INVOICE_PDF_UNAVAILABLE = {
    'code': 'invoice_pdf_unavailable',
    'detail': 'Invoice PDF could not be generated.',
}


class InvoicePdfUnavailable(Exception):
    """Renderer missing or generation failed. Do not leak the cause."""


def render_outgoing_invoice_pdf(request, invoice) -> bytes:
    try:
        from invoices.services.pdf import render_invoice_pdf_bytes
    except ImportError as exc:
        raise InvoicePdfUnavailable from exc
    if not callable(render_invoice_pdf_bytes):
        raise InvoicePdfUnavailable
    try:
        body = render_invoice_pdf_bytes(request, invoice)
    except Exception as exc:
        raise InvoicePdfUnavailable from exc
    raw = bytes(body or b'')
    if not raw.startswith(b'%PDF-'):
        raise InvoicePdfUnavailable
    return raw


def outgoing_pdf_filename(invoice) -> str:
    number = safe_download_filename(str(invoice.invoice_number or invoice.pk))
    if number.lower().endswith('.pdf'):
        number = number[:-4]
    return safe_download_filename(f'R-{number}.pdf')
