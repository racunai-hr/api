import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404

from fiscal_gateway.models import As4DocumentLink
from super_integration.models import SuperDocumentLink

from .models import Expense, ExpenseAttachment


def _user_can_access_tenant(request, tenant):
    user = request.user
    if user.is_superuser:
        return True
    request_tenant = getattr(request, 'tenant', None)
    if request_tenant is None:
        return False
    return request_tenant.id == tenant.id


@login_required
def attachment_download(request, pk):
    attachment = get_object_or_404(
        ExpenseAttachment.all_objects.select_related('expense', 'tenant'),
        pk=pk,
    )
    if not _user_can_access_tenant(request, attachment.tenant):
        return HttpResponseForbidden('Nedozvoljen pristup.')
    if not attachment.file:
        raise Http404('Prilog nije pronađen.')
    filename = attachment.original_filename or os.path.basename(attachment.file.name)
    response = FileResponse(attachment.file.open('rb'), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


@login_required
def expense_as4_ubl(request, pk):
    expense = get_object_or_404(Expense.all_objects.select_related('tenant'), pk=pk)
    if not _user_can_access_tenant(request, expense.tenant):
        return HttpResponseForbidden('Nedozvoljen pristup.')
    link = As4DocumentLink.all_objects.filter(
        tenant=expense.tenant,
        direction=As4DocumentLink.DIRECTION_INBOUND,
        content_type=ContentType.objects.get_for_model(Expense),
        object_id=expense.pk,
    ).exclude(ubl_xml='').first()
    if not link:
        raise Http404('AS4 UBL nije pronađen.')
    from django.http import HttpResponse

    filename = f'{expense.expense_number or expense.pk}-as4.xml'
    response = HttpResponse(link.ubl_xml, content_type='application/xml')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


@login_required
def expense_super_pdf(request, pk):
    expense = get_object_or_404(Expense.all_objects.select_related('tenant'), pk=pk)
    if not _user_can_access_tenant(request, expense.tenant):
        return HttpResponseForbidden('Nedozvoljen pristup.')
    link = SuperDocumentLink.all_objects.filter(
        tenant=expense.tenant,
        direction=SuperDocumentLink.DIRECTION_INBOUND,
        content_type=ContentType.objects.get_for_model(Expense),
        object_id=expense.pk,
    ).exclude(pdf_path='').first()
    if not link or not link.pdf_path:
        raise Http404('SUPER PDF nije pronađen.')
    abs_path = os.path.join(settings.MEDIA_ROOT, link.pdf_path)
    if not os.path.isfile(abs_path):
        raise Http404('SUPER PDF datoteka ne postoji.')
    filename = os.path.basename(link.pdf_path)
    response = FileResponse(open(abs_path, 'rb'), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response
