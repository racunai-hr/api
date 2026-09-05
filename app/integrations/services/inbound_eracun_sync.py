"""Inbound eRačun sync: gateway inbox → draft Expense, and inbox refresh toward the provider.

Two operations on purpose. Importing what the gateway already holds must not depend
on a live provider round-trip, so the request path never waits on Super.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, connection, transaction

from expenses.models import Expense, ExpenseImportMetadata, ExpenseSource
from fiscal_gateway.client.gateway_v1_client import GatewayV1Client, GatewayV1Error
from fiscal_gateway.services.inbound_expense import create_inbound_expense_from_ubl
from integrations.audit import log_audit_step, new_correlation_id
from integrations.constants import IntegrationProvider, IntegrationType
from integrations.models import IntegrationAuditLog
from integrations.services.inbound_rejection import taxpayer_oib_for_tenant
from super_integration.models import SuperDocumentLink

logger = logging.getLogger(__name__)

# Provenance namespace for ExpenseImportMetadata.external_id — the provider, not the
# business source. Legacy rows from the removed direct sync share it, so their GUIDs
# keep colliding with new imports.
METADATA_SOURCE = ExpenseSource.SUPER

# pg_try_advisory_lock namespace for inbound eRačun sync (arbitrary stable int).
_LOCK_NAMESPACE = 0x45524143

CODE_DISABLED = 'eracun_sync_disabled'
CODE_NO_OIB = 'taxpayer_oib_missing'
CODE_IN_PROGRESS = 'sync_in_progress'
CODE_GATEWAY = 'gateway_error'


class EracunSyncError(Exception):
    def __init__(self, code: str, detail: str, *, http_status: int = 409):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


def _require_enabled() -> None:
    if not getattr(settings, 'ERACUN_INBOUND_SYNC_ENABLED', False):
        raise EracunSyncError(
            CODE_DISABLED,
            'Sinkronizacija ulaznih eRačuna nije uključena u ovom okruženju.',
            http_status=503,
        )


def _require_oib(tenant) -> str:
    oib = taxpayer_oib_for_tenant(tenant)
    if not oib:
        raise EracunSyncError(
            CODE_NO_OIB,
            'Tvrtka nema ispravan OIB u postavkama.',
        )
    return oib


@contextmanager
def _tenant_lock(tenant):
    """Guard against double-click only. Correctness lives in DB unique constraints."""
    if connection.vendor != 'postgresql':
        yield
        return
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s, %s)', [_LOCK_NAMESPACE, int(tenant.pk)])
        acquired = cursor.fetchone()[0]
    if not acquired:
        raise EracunSyncError(CODE_IN_PROGRESS, 'Sinkronizacija je već u tijeku.')
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(%s, %s)', [_LOCK_NAMESPACE, int(tenant.pk)])


def _invoice_guid(item: dict) -> str:
    refs = item.get('provider_refs') if isinstance(item.get('provider_refs'), dict) else {}
    return str(refs.get('invoice_guid') or '').strip()


def _list_inbound_candidates(client: GatewayV1Client, oib: str) -> list[dict]:
    """Walk the whole gateway inbox — it is a local DB read on the gateway side."""
    items: list[dict] = []
    cursor = None
    for _ in range(50):
        page = client.list_inbound_documents(oib, cursor=cursor, limit=100)
        items.extend(row for row in (page.get('items') or []) if isinstance(row, dict))
        if not page.get('has_more') or not page.get('next_cursor'):
            break
        cursor = page.get('next_cursor')
    return items


def _already_imported(tenant, guid: str) -> bool:
    return SuperDocumentLink.all_objects.filter(
        tenant=tenant,
        direction=SuperDocumentLink.DIRECTION_INBOUND,
        super_guid=guid,
    ).exists()


def _import_one(tenant, *, item: dict, guid: str, ubl_xml: str) -> Expense:
    with transaction.atomic():
        draft = create_inbound_expense_from_ubl(
            tenant=tenant,
            ubl_xml=ubl_xml,
            number_prefix='ERAC',
            source=ExpenseSource.ERACUN,
            unknown_supplier_ref=guid,
        )
        SuperDocumentLink.all_objects.create(
            tenant=tenant,
            direction=SuperDocumentLink.DIRECTION_INBOUND,
            super_guid=guid,
            ubl_xml=ubl_xml,
            content_type=ContentType.objects.get_for_model(Expense),
            object_id=draft.expense.pk,
        )
        ExpenseImportMetadata.all_objects.create(
            tenant=tenant,
            expense=draft.expense,
            source=METADATA_SOURCE,
            external_id=guid,
            super_guid=guid,
            raw_payload=item,
        )
        return draft.expense


def import_available_inbound_eracun(tenant, *, limit: int | None = None) -> dict:
    """Import gateway inbox documents that are not in Django yet. No provider calls."""
    _require_enabled()
    oib = _require_oib(tenant)
    cap = limit if limit is not None else getattr(settings, 'ERACUN_INBOUND_IMPORT_LIMIT', 25)
    cap = max(1, int(cap))
    correlation_id = new_correlation_id()

    with _tenant_lock(tenant):
        client = GatewayV1Client(taxpayer_oib=oib)
        try:
            items = _list_inbound_candidates(client, oib)
        except GatewayV1Error as exc:
            raise EracunSyncError(CODE_GATEWAY, str(exc), http_status=502) from exc

        imported = 0
        skipped = 0
        unlinked = 0
        failed = 0
        remaining = 0
        errors: list[dict] = []

        for item in items:
            guid = _invoice_guid(item)
            if not guid:
                # No provider invoice identity — nothing to dedupe or attribute against.
                # Real provider pulls always carry one, so these are not import failures.
                unlinked += 1
                skipped += 1
                continue
            if _already_imported(tenant, guid):
                skipped += 1
                continue
            if imported >= cap:
                remaining += 1
                continue

            try:
                ubl_xml = client.get_inbound_ubl(str(item.get('document_id')))
                expense = _import_one(tenant, item=item, guid=guid, ubl_xml=ubl_xml)
            except IntegrityError:
                # Another writer got the same document first — the unique constraints,
                # not the advisory lock, are what keep this to exactly one Expense.
                skipped += 1
                continue
            except Exception as exc:  # one bad document must not kill the run
                failed += 1
                errors.append({'invoice_guid': guid, 'detail': str(exc)[:300]})
                logger.warning('eRačun import failed guid=%s: %s', guid, exc)
                continue

            imported += 1
            log_audit_step(
                tenant=tenant,
                step=IntegrationAuditLog.STEP_EXPENSE_CREATED,
                status=IntegrationAuditLog.STATUS_SUCCESS,
                correlation_id=correlation_id,
                integration_type=IntegrationType.ERACUN,
                provider=IntegrationProvider.SUPER,
                detail={
                    'invoice_guid': guid,
                    'gateway_document_id': str(item.get('document_id') or ''),
                    'expense_id': expense.pk,
                },
            )

    if unlinked:
        logger.info(
            'eRačun import skipped %s gateway documents without invoice_guid (tenant=%s)',
            unlinked,
            tenant.slug,
        )

    log_audit_step(
        tenant=tenant,
        step=IntegrationAuditLog.STEP_INBOUND_RECEIVED,
        status=(
            IntegrationAuditLog.STATUS_FAILED
            if failed and not imported
            else IntegrationAuditLog.STATUS_SUCCESS
        ),
        correlation_id=correlation_id,
        integration_type=IntegrationType.ERACUN,
        provider=IntegrationProvider.SUPER,
        detail={
            'scanned': len(items),
            'imported': imported,
            'skipped': skipped,
            'unlinked': unlinked,
            'failed': failed,
            'remaining_importable': remaining,
        },
    )

    return {
        'scanned': len(items),
        'imported': imported,
        'skipped': skipped,
        'failed': failed,
        'remaining_importable': remaining,
        'errors': errors,
    }


def refresh_inbound_inbox(tenant, *, idempotency_key: str) -> dict:
    """Ask the gateway to reconcile against the provider. Imports nothing."""
    _require_enabled()
    oib = _require_oib(tenant)
    client = GatewayV1Client(taxpayer_oib=oib, timeout=90)
    try:
        body = client.start_reconciliation(oib, idempotency_key=idempotency_key)
    except GatewayV1Error as exc:
        raise EracunSyncError(CODE_GATEWAY, str(exc), http_status=502) from exc

    status = str(body.get('status') or '')
    error = body.get('error') if isinstance(body.get('error'), dict) else {}
    if status == 'FAILED':
        raise EracunSyncError(
            str(error.get('code') or CODE_GATEWAY),
            str(error.get('message') or 'Osvježavanje nije uspjelo.'),
            http_status=502,
        )
    return {
        'reconciliation_id': str(body.get('reconciliation_id') or ''),
        'status': status,
        'detail': str(error.get('message') or ''),
    }
