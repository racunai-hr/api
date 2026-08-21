"""Canonical on-demand Invoice PDF renderer (Django Admin + reporting)."""

from __future__ import annotations

import base64
import os
from io import BytesIO
from pathlib import Path

from barcode import Code128
from barcode.writer import ImageWriter
from django.conf import settings
from django.template.loader import render_to_string
from weasyprint import CSS, HTML

from settings.models import CompanySettings


def company_logo_uri(company) -> str | None:
    if not company or not company.company_logo or not company.company_logo.name:
        return None
    logo_path = company.company_logo.path
    if os.path.exists(logo_path):
        return Path(logo_path).as_uri()
    return None


def generate_barcode_base64(text):
    """Generiraj barcode kao base64 string za umetanje u HTML."""
    try:
        code = Code128(text, writer=ImageWriter())
        buffer = BytesIO()
        code.write(buffer)
        buffer.seek(0)
        barcode_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        return f"data:image/png;base64,{barcode_base64}"
    except Exception:
        return None


def render_invoice_pdf_bytes(request, invoice) -> bytes:
    """Render the issued Invoice PDF from stored totals — no recalculation."""
    company = CompanySettings.objects.filter(tenant=invoice.tenant).first()
    barcode_data = generate_barcode_base64(invoice.invoice_number)
    context = {
        'invoice': invoice,
        'company': company,
        'responsible_person': invoice.responsible_person,
        'barcode_data': barcode_data,
        'company_logo_uri': company_logo_uri(company),
        'request': request,
    }
    html_string = render_to_string('invoices/invoice_detail.html', context)
    base_url = request.build_absolute_uri('/')
    bootstrap_path = os.path.join(settings.STATIC_ROOT, 'vendor/bootstrap/bootstrap.min.css')
    custom_css = CSS(string="""
      body { font-family: "DejaVu Sans", Arial, sans-serif; }
      table.items { border-collapse: collapse; }
      table.items th, table.items td { border: 1px solid #000 !important; }
      @page { size: A4; margin: 14mm; }
      .responsible-person { margin-top: 20px; padding: 10px; border: 1px solid #ddd; }
      .responsible-person h5 { margin-bottom: 5px; color: #333; }
      .barcode-section { text-align: center; margin: 1rem 0; }
      .barcode-section img { max-width: 100%; height: auto; }
    """)
    styles = []
    if os.path.exists(bootstrap_path):
        styles.append(CSS(filename=bootstrap_path))
    styles.append(custom_css)
    return HTML(string=html_string, base_url=base_url).write_pdf(stylesheets=styles)
