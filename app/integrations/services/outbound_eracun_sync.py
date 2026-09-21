"""Outbound eRačun import: gateway historical OUTBOUND documents → Invoice."""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction

from fiscal_gateway.client.gateway_v1_client import GatewayV1Client, GatewayV1Error
from fiscal_gateway.services.outbound_invoice import create_outbound_invoice_from_ubl
from integrations.services.inbound_eracun_sync import (
    EracunSyncError,
    _invoice_guid,
    _require_enabled,
    _require_oib,
    _tenant_lock,
)
from invoices.models import Invoice
from super_integration.models import SuperDocumentLink

logger = logging.getLogger(__name__)


def _already_imported(tenant, guid: str) -> bool:
    return SuperDocumentLink.all_objects.filter(
        tenant=tenant,
        direction=SuperDocumentLink.DIRECTION_OUTBOUND,
        super_guid=guid,
    ).exists()


def _list_outbound_candidates(client: GatewayV1Client, oib: str) -> list[dict]:
    items: list[dict] = []
    cursor = None
    for _ in range(50):
        page = client.list_outbound_documents(oib, cursor=cursor, limit=100)
        items.extend(row for row in (page.get('items') or []) if isinstance(row, dict))
        if not page.get('has_more') or not page.get('next_cursor'):
            break
        cursor = page.get('next_cursor')
    return items


def _import_one(tenant, *, item: dict, guid: str, ubl_xml: str) -> Invoice:
    with transaction.atomic():
        draft = create_outbound_invoice_from_ubl(
            tenant=tenant,
            ubl_xml=ubl_xml,
            gateway_item=item,
            unknown_customer_ref=guid,
        )
        SuperDocumentLink.all_objects.create(
            tenant=tenant,
            direction=SuperDocumentLink.DIRECTION_OUTBOUND,
            super_guid=guid,
            super_unique_id=guid,
            ubl_xml=ubl_xml,
            content_type=ContentType.objects.get_for_model(Invoice),
            object_id=draft.invoice.pk,
        )
        return draft.invoice


def import_available_outbound_eracun(tenant, *, limit: int | None = None) -> dict:
    """Import historical gateway OUTBOUND documents that are not in Django yet."""
    _require_enabled()
    oib = _require_oib(tenant)
    cap = limit if limit is not None else getattr(settings, 'ERACUN_INBOUND_IMPORT_LIMIT', 25)
    cap = max(1, int(cap))

    with _tenant_lock(tenant):
        client = GatewayV1Client(taxpayer_oib=oib)
        try:
            items = _list_outbound_candidates(client, oib)
        except GatewayV1Error as exc:
            raise EracunSyncError('gateway_error', str(exc), http_status=502) from exc

        imported = 0
        skipped = 0
        failed = 0
        remaining = 0
        errors: list[dict] = []

        for item in items:
            refs = item.get('provider_refs') if isinstance(item.get('provider_refs'), dict) else {}
            if not refs.get('historical_intake'):
                skipped += 1
                continue
            guid = _invoice_guid(item)
            if not guid:
                skipped += 1
                continue
            if _already_imported(tenant, guid):
                skipped += 1
                continue
            if imported >= cap:
                remaining += 1
                continue
            try:
                ubl_xml = client.get_outbound_ubl(str(item.get('document_id')))
                invoice = _import_one(tenant, item=item, guid=guid, ubl_xml=ubl_xml)
            except IntegrityError:
                skipped += 1
                continue
            except Exception as exc:
                failed += 1
                errors.append({'invoice_guid': guid, 'detail': str(exc)[:300]})
                logger.warning('eRačun outbound import failed guid=%s: %s', guid, exc)
                continue
            imported += 1

    return {
        'scanned': len(items),
        'imported': imported,
        'skipped': skipped,
        'failed': failed,
        'remaining_importable': remaining,
        'errors': errors,
    }
