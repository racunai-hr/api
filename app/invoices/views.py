from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.conf import settings
from weasyprint import HTML, CSS
import os
import base64
from io import BytesIO
from pathlib import Path
from barcode import Code128
from barcode.writer import ImageWriter

from .models import Invoice
from settings.models import CompanySettings


def _company_logo_uri(company) -> str | None:
    if not company or not company.company_logo or not company.company_logo.name:
        return None
    logo_path = company.company_logo.path
    if os.path.exists(logo_path):
        return Path(logo_path).as_uri()
    return None


def generate_barcode_base64(text):
    """Generiraj barcode kao base64 string za umetanje u HTML"""
    try:
        # Stvori Code128 barcode
        code = Code128(text, writer=ImageWriter())
        
        # Generiraj sliku u memoriji
        buffer = BytesIO()
        code.write(buffer)
        buffer.seek(0)
        
        # Konvertiraj u base64
        barcode_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        return f"data:image/png;base64,{barcode_base64}"
    except Exception as e:
        print(f"Greška kod generiranja barcoda: {e}")
        return None


def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('responsible_person'), pk=pk)
    company = CompanySettings.objects.filter(tenant=invoice.tenant).first()
    
    # Generiraj barcode
    barcode_data = generate_barcode_base64(invoice.invoice_number)
    
    context = {
        "invoice": invoice, 
        "company": company, 
        "responsible_person": invoice.responsible_person,
        "barcode_data": barcode_data,
        "company_logo_uri": _company_logo_uri(company),
        "request": request
    }
    
    html = render_to_string("invoices/invoice_detail.html", context)
    return HttpResponse(html)


def render_invoice_pdf_bytes(request, invoice) -> bytes:
    company = CompanySettings.objects.filter(tenant=invoice.tenant).first()
    barcode_data = generate_barcode_base64(invoice.invoice_number)
    context = {
        "invoice": invoice,
        "company": company,
        "responsible_person": invoice.responsible_person,
        "barcode_data": barcode_data,
        "company_logo_uri": _company_logo_uri(company),
        "request": request,
    }
    html_string = render_to_string("invoices/invoice_detail.html", context)
    base_url = request.build_absolute_uri("/")
    bootstrap_path = os.path.join(settings.STATIC_ROOT, "vendor/bootstrap/bootstrap.min.css")
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


def invoice_pdf(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('responsible_person'), pk=pk)
    pdf_bytes = render_invoice_pdf_bytes(request, invoice)
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="R-{invoice.invoice_number}.pdf"'
    return resp
