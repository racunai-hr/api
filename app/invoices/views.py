from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string

from invoices.services.pdf import (
    company_logo_uri,
    generate_barcode_base64,
    render_invoice_pdf_bytes,
)
from settings.models import CompanySettings

from .models import Invoice

# Re-export for callers that imported the renderer from the view module.
_company_logo_uri = company_logo_uri


def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('responsible_person'), pk=pk)
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
    html = render_to_string('invoices/invoice_detail.html', context)
    return HttpResponse(html)


def invoice_pdf(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('responsible_person'), pk=pk)
    pdf_bytes = render_invoice_pdf_bytes(request, invoice)
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    resp['Content-Disposition'] = f'inline; filename="R-{invoice.invoice_number}.pdf"'
    return resp
