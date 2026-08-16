from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from io import BytesIO

import xlsxwriter

from accounting.models import VATPeriod
from accounting.services.reports import (
    export_bilanca_xlsx,
    export_rdg_xlsx,
    export_trial_balance_xlsx,
    journal_report,
)
from accounting.services.tax_forms.pdv_s.aggregate import aggregate_pdv_s_rows
from accounting.services.tax_forms.pdv_s.render import render_pdv_s_xml
from accounting.services.tax_forms.pdv_s.validation import PdvSSchemaValidationError, validate_pdv_s_xml
from accounting.services.vat import export_pdv_s_xlsx
from tenants.permissions import get_role, role_can_export
from tenants.views import require_tenant_export


@login_required
def vat_export(request, period_id):
    period = get_object_or_404(VATPeriod.all_objects, pk=period_id)
    tenant = getattr(request, 'tenant', None)
    if tenant and period.tenant_id != tenant.id and not request.user.is_superuser:
        return HttpResponseForbidden('Nedozvoljen pristup.')
    role = get_role(request.user, tenant or period.tenant)
    if not request.user.is_superuser and not role_can_export(role):
        return HttpResponseForbidden('Nemate dozvolu za izvoz.')
    content = export_pdv_s_xlsx(period)
    filename = f"PDV-S_{period.year}_{period.month:02d}.xlsx"
    response = HttpResponse(
        content,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def pdv_s_xml_export(request, period_id):
    period = get_object_or_404(VATPeriod.all_objects, pk=period_id)
    tenant = getattr(request, 'tenant', None)
    if tenant and period.tenant_id != tenant.id and not request.user.is_superuser:
        return HttpResponseForbidden('Nedozvoljen pristup.')
    role = get_role(request.user, tenant or period.tenant)
    if not request.user.is_superuser and not role_can_export(role):
        return HttpResponseForbidden('Nemate dozvolu za izvoz.')

    payload = aggregate_pdv_s_rows(period)
    xml_bytes = render_pdv_s_xml(payload)
    try:
        validate_pdv_s_xml(xml_bytes)
    except PdvSSchemaValidationError as exc:
        return HttpResponse(f'PDV-S XSD validacija nije prošla: {exc}', status=500)

    filename = (
        f'PDV-S_{payload.taxpayer.oib}_'
        f'{payload.period_from:%Y%m%d}-{payload.period_to:%Y%m%d}.xml'
    )
    response = HttpResponse(xml_bytes, content_type='application/xml')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def zp_xml_export(request, period_id):
    period = get_object_or_404(VATPeriod.all_objects, pk=period_id)
    tenant = getattr(request, 'tenant', None)
    if tenant and period.tenant_id != tenant.id and not request.user.is_superuser:
        return HttpResponseForbidden('Nedozvoljen pristup.')
    role = get_role(request.user, tenant or period.tenant)
    if not request.user.is_superuser and not role_can_export(role):
        return HttpResponseForbidden('Nemate dozvolu za izvoz.')

    from accounting.services.submission.events import get_latest_zp_return

    zp_return = get_latest_zp_return(period)
    if zp_return is None or not zp_return.xml_unsigned:
        return HttpResponse('Nema ZP drafta za ovo razdoblje.', status=404)

    xml_bytes = default_storage.open(zp_return.xml_unsigned.name).read()
    filename = f'ZP_{period.year}_{period.month:02d}_v{zp_return.version}.xml'
    response = HttpResponse(xml_bytes, content_type='application/xml')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@require_tenant_export
def trial_balance_export(request, year, month):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        return HttpResponseForbidden('Tenant nije određen.')
    content = export_trial_balance_xlsx(tenant, int(year), int(month))
    filename = f"Bruto_bilanca_{year}_{int(month):02d}.xlsx"
    response = HttpResponse(
        content,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@require_tenant_export
def bilanca_export(request, year, month):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        return HttpResponseForbidden('Tenant nije određen.')
    content = export_bilanca_xlsx(tenant, int(year), int(month))
    filename = f"Bilanca_{year}_{int(month):02d}.xlsx"
    response = HttpResponse(
        content,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@require_tenant_export
def rdg_export(request, year, month):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        return HttpResponseForbidden('Tenant nije određen.')
    content = export_rdg_xlsx(tenant, int(year), int(month))
    filename = f"RDG_{year}_{int(month):02d}.xlsx"
    response = HttpResponse(
        content,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@require_tenant_export
def journal_export(request, year, month):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        return HttpResponseForbidden('Tenant nije određen.')

    entries = journal_report(tenant, int(year), int(month))
    buffer = BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {'in_memory': True})
    ws = workbook.add_worksheet('Dnevnik')
    header = workbook.add_format({'bold': True})
    money = workbook.add_format({'num_format': '#,##0.00'})
    ws.write(0, 0, f'Dnevnik knjiženja {int(month):02d}/{year}', header)
    cols = ['Broj', 'Datum', 'Tip', 'Konto', 'Duguje', 'Potražuje', 'Opis']
    for col, title in enumerate(cols):
        ws.write(2, col, title, header)
    row = 3
    for item in entries:
        entry = item.entry
        for line in entry.lines.all():
            ws.write(row, 0, entry.entry_number)
            ws.write(row, 1, entry.entry_date.strftime('%d.%m.%Y'))
            ws.write(row, 2, item.audit_label)
            ws.write(row, 3, line.account.account_code)
            ws.write(row, 4, float(line.debit_amount), money)
            ws.write(row, 5, float(line.credit_amount), money)
            ws.write(row, 6, line.description or entry.description)
            row += 1
    workbook.close()
    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="Dnevnik_{year}_{int(month):02d}.xlsx"'
    return response
