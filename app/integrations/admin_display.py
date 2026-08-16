from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from fiscal_gateway.models import As4DocumentLink
from integrations.models import IntegrationAuditLog


def _timeline_links_for_correlation_ids(correlation_ids) -> list:
    links = []
    seen = set()
    for correlation_id in correlation_ids:
        cid = str(correlation_id)
        if cid in seen:
            continue
        seen.add(cid)
        url = reverse('admin:integrations_correlation_timeline', args=[cid])
        short = cid[:8]
        links.append(format_html('<a href="{}">Timeline {}…</a>', url, short))
    return links


def invoice_integration_status(invoice) -> str:
    if not invoice.pk:
        return '-'

    parts = []
    outbound = As4DocumentLink.all_objects.filter(
        tenant=invoice.tenant,
        direction=As4DocumentLink.DIRECTION_OUTBOUND,
        content_type=ContentType.objects.get_for_model(invoice),
        object_id=invoice.pk,
    ).order_by('-last_synced_at').first()
    if outbound:
        parts.append(
            format_html(
                'AS4 izlazni: <code>{}</code> ({})',
                outbound.message_id,
                outbound.get_as4_status_display(),
            )
        )

    audit_qs = IntegrationAuditLog.all_objects.filter(invoice=invoice).order_by('-created_at')
    latest = audit_qs.first()
    if latest:
        parts.append(
            format_html(
                'Zadnji korak: {} ({})',
                latest.get_step_display(),
                latest.get_status_display(),
            )
        )

    timeline_links = _timeline_links_for_correlation_ids(
        audit_qs.values_list('correlation_id', flat=True)[:5]
    )
    if timeline_links:
        parts.append(format_html_join(format_html('<br>'), '{}', ((link,) for link in timeline_links)))

    if not parts:
        return format_html('<span style="color:#666;">Nema integracijskog traga</span>')
    return format_html_join(format_html('<br>'), '{}', ((part,) for part in parts))


def expense_integration_status(expense) -> str:
    if not expense.pk:
        return '-'

    parts = []
    inbound = As4DocumentLink.all_objects.filter(
        tenant=expense.tenant,
        direction=As4DocumentLink.DIRECTION_INBOUND,
        content_type=ContentType.objects.get_for_model(expense),
        object_id=expense.pk,
    ).order_by('-last_synced_at').first()
    if inbound:
        ubl_url = reverse('expenses:expense_as4_ubl', args=[expense.pk])
        parts.append(
            format_html(
                'AS4 ulazni: <code>{}</code> — <a href="{}" target="_blank">UBL/XML</a>',
                inbound.message_id,
                ubl_url,
            )
        )

    audit_qs = IntegrationAuditLog.all_objects.filter(
        tenant=expense.tenant,
        as4_link__content_type=ContentType.objects.get_for_model(expense),
        as4_link__object_id=expense.pk,
    ).order_by('-created_at')
    latest = audit_qs.first()
    if latest:
        parts.append(
            format_html(
                'Zadnji korak: {} ({})',
                latest.get_step_display(),
                latest.get_status_display(),
            )
        )

    timeline_links = _timeline_links_for_correlation_ids(
        audit_qs.values_list('correlation_id', flat=True)[:5]
    )
    if timeline_links:
        parts.append(format_html_join(format_html('<br>'), '{}', ((link,) for link in timeline_links)))

    if not parts:
        return format_html('<span style="color:#666;">Nema AS4 traga</span>')
    return format_html_join(format_html('<br>'), '{}', ((part,) for part in parts))
