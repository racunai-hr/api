"""Provider-neutral inbound eRačun rejection via intermediary only (no legacy fallback)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction

from expenses.models import Expense
from fiscal_gateway.client.gateway_v1_client import GatewayV1Client, GatewayV1Error
from fiscal_gateway.models import As4DocumentLink
from integrations.models import InboundEracunRejectionAttempt, InboundGatewayDocumentLink
from settings.models import CompanySettings
from super_integration.models import SuperDocumentLink

REASON_CODES = (
    'REJECTED_BY_RECIPIENT',
    'TAX_NEUTRAL_MISMATCH',
    'TAX_AFFECTING_MISMATCH',
    'OTHER',
)

UNAVAILABLE_ALREADY_REJECTED = 'already_rejected'
UNAVAILABLE_REJECTION_PENDING = 'rejection_pending'
UNAVAILABLE_INVALID_STATUS = 'invalid_status'
UNAVAILABLE_NOT_ON_GATEWAY = 'not_on_gateway'
UNAVAILABLE_CAPABILITY = 'capability_not_supported'


class InboundRejectionError(Exception):
    def __init__(self, code: str, detail: str, *, http_status: int = 409):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


@dataclass(frozen=True)
class RejectAction:
    available: bool
    reason_codes: tuple[str, ...]
    unavailable_code: str | None


def _taxpayer_oib(tenant) -> str | None:
    company = CompanySettings.all_objects.filter(tenant=tenant).first()
    if company is None:
        return None
    raw = (company.tax_number or company.vat_number or '').strip().upper()
    if raw.startswith('HR'):
        raw = raw[2:]
    digits = ''.join(ch for ch in raw if ch.isdigit())
    return digits if len(digits) == 11 else None


def _provider_ref_for_expense(expense: Expense) -> tuple[str | None, str | None]:
    """Return (provider_ref, hint_provider) from Super/AS4 links — for gateway resolve only."""
    ct = ContentType.objects.get_for_model(Expense)
    super_link = SuperDocumentLink.all_objects.filter(
        tenant=expense.tenant,
        direction=SuperDocumentLink.DIRECTION_INBOUND,
        content_type=ct,
        object_id=expense.pk,
    ).first()
    if super_link and super_link.super_guid:
        return super_link.super_guid, 'super'
    as4_link = As4DocumentLink.all_objects.filter(
        tenant=expense.tenant,
        direction=As4DocumentLink.DIRECTION_INBOUND,
        content_type=ct,
        object_id=expense.pk,
    ).first()
    if as4_link and as4_link.message_id:
        return as4_link.message_id, 'as4'
    return None, None


def get_pending_rejection(expense: Expense) -> InboundEracunRejectionAttempt | None:
    return (
        InboundEracunRejectionAttempt.all_objects.filter(
            tenant=expense.tenant,
            expense=expense,
            status=InboundEracunRejectionAttempt.STATUS_PENDING,
        )
        .order_by('-created_at')
        .first()
    )


def _match_provider_ref(item: dict, provider_ref: str) -> bool:
    refs = item.get('provider_refs') or {}
    if not isinstance(refs, dict):
        return False
    candidates = [
        refs.get('invoice_guid'),
        refs.get('provider_invoice_guid'),
        refs.get('message_id'),
        refs.get('as4_message_id'),
    ]
    return any(str(c) == provider_ref for c in candidates if c)


def _provider_supports_rejection(client: GatewayV1Client, provider: str, taxpayer_oib: str) -> bool:
    if not provider:
        return False
    try:
        caps = client.provider_capabilities(provider, taxpayer_oib=taxpayer_oib)
    except GatewayV1Error:
        return False
    supports = caps.get('supports') if isinstance(caps, dict) else None
    if isinstance(supports, dict):
        return bool(supports.get('inbound_e_reporting_rejection'))
    return False


def resolve_gateway_document(
    expense: Expense,
    *,
    client: GatewayV1Client | None = None,
) -> InboundGatewayDocumentLink | None:
    """Read-only resolve + cache. No writes to Super/Domibus; no rejection side effects."""
    existing = InboundGatewayDocumentLink.all_objects.filter(
        tenant=expense.tenant, expense=expense
    ).first()
    if existing:
        return existing

    provider_ref, _hint = _provider_ref_for_expense(expense)
    if not provider_ref:
        return None

    taxpayer_oib = _taxpayer_oib(expense.tenant)
    if not taxpayer_oib:
        return None

    gw = client or GatewayV1Client(taxpayer_oib=taxpayer_oib, timeout=5)
    cursor = None
    matched: dict | None = None
    try:
        for _ in range(20):
            page = gw.list_inbound_documents(taxpayer_oib, cursor=cursor, limit=100)
            for item in page.get('items') or []:
                if isinstance(item, dict) and _match_provider_ref(item, provider_ref):
                    matched = item
                    break
            if matched:
                break
            if not page.get('has_more') or not page.get('next_cursor'):
                break
            cursor = page.get('next_cursor')
    except GatewayV1Error:
        return None

    if not matched:
        return None

    document_id = matched.get('document_id')
    if not document_id:
        return None
    bound_provider = str(matched.get('bound_provider') or '')
    capable = _provider_supports_rejection(gw, bound_provider, taxpayer_oib)

    link, _created = InboundGatewayDocumentLink.all_objects.update_or_create(
        tenant=expense.tenant,
        expense=expense,
        defaults={
            'gateway_document_id': document_id,
            'bound_provider': bound_provider,
            'provider_ref': provider_ref,
            'taxpayer_oib': taxpayer_oib,
            'rejection_capable': capable,
        },
    )
    return link


def resolve_reject_action(expense: Expense, *, client: GatewayV1Client | None = None) -> RejectAction:
    """Read-only Integration capability resolver for DocumentDetail.actions.reject."""
    if expense.status == 'rejected':
        return RejectAction(False, (), UNAVAILABLE_ALREADY_REJECTED)
    if get_pending_rejection(expense) is not None:
        return RejectAction(False, (), UNAVAILABLE_REJECTION_PENDING)
    if expense.status in ('paid', 'cancelled'):
        return RejectAction(False, (), UNAVAILABLE_INVALID_STATUS)

    link = resolve_gateway_document(expense, client=client)
    if link is None:
        return RejectAction(False, (), UNAVAILABLE_NOT_ON_GATEWAY)
    if not link.rejection_capable:
        return RejectAction(False, (), UNAVAILABLE_CAPABILITY)
    return RejectAction(True, REASON_CODES, None)


def build_actions_reject_block(expense: Expense) -> dict[str, Any]:
    action = resolve_reject_action(expense)
    return {
        'available': action.available,
        'reason_codes': list(action.reason_codes) if action.available else [],
        'unavailable_code': action.unavailable_code,
    }


def submit_eracun_rejection(
    expense: Expense,
    *,
    reason_code: str,
    reason_text: str = '',
    idempotency_key: str,
    client: GatewayV1Client | None = None,
) -> dict[str, Any]:
    """POST rejection to intermediary only. Does not set Expense.status=rejected."""
    reason_code = (reason_code or '').strip()
    if reason_code not in REASON_CODES:
        raise InboundRejectionError(
            'invalid_reason_code',
            f'Nepoznat reason_code: {reason_code}',
            http_status=400,
        )
    idempotency_key = (idempotency_key or '').strip()
    if not idempotency_key:
        raise InboundRejectionError(
            'invalid_request',
            'Idempotency-Key je obavezan.',
            http_status=400,
        )

    if expense.status == 'rejected':
        raise InboundRejectionError(
            UNAVAILABLE_ALREADY_REJECTED,
            'Dokument je već odbijen.',
        )
    if expense.status in ('paid', 'cancelled'):
        raise InboundRejectionError(
            UNAVAILABLE_INVALID_STATUS,
            'Dokument nije u stanju za odbijanje.',
        )

    existing_pending = get_pending_rejection(expense)
    if existing_pending is not None:
        raise InboundRejectionError(
            UNAVAILABLE_REJECTION_PENDING,
            'Odbijanje je već u tijeku.',
        )

    prior = InboundEracunRejectionAttempt.all_objects.filter(
        tenant=expense.tenant,
        idempotency_key=idempotency_key,
    ).first()
    if prior is not None:
        body_hash = _request_hash(reason_code, reason_text)
        prior_hash = _request_hash(prior.reason_code, prior.reason_text)
        if body_hash != prior_hash:
            raise InboundRejectionError(
                'idempotency_conflict',
                'Idempotency-Key je već iskorišten s drugim tijelom zahtjeva.',
            )
        return _success_payload(expense, prior)

    link = resolve_gateway_document(expense, client=client)
    if link is None:
        raise InboundRejectionError(
            UNAVAILABLE_NOT_ON_GATEWAY,
            'Dokument nije na fiscal gateway putu.',
        )
    if not link.rejection_capable:
        raise InboundRejectionError(
            UNAVAILABLE_CAPABILITY,
            'Provider ne podržava e-reporting odbijanje.',
        )

    taxpayer_oib = link.taxpayer_oib or _taxpayer_oib(expense.tenant)
    gw = client or GatewayV1Client(taxpayer_oib=taxpayer_oib)
    try:
        _status_code, body = gw.reject_e_reporting(
            str(link.gateway_document_id),
            reason_code=reason_code,
            reason_text=reason_text or '',
            idempotency_key=idempotency_key,
        )
    except GatewayV1Error as exc:
        if exc.status_code == 409 and (exc.code or '').upper() in {
            'IDEMPOTENCY_CONFLICT',
            'idempotency_conflict',
        }:
            raise InboundRejectionError(
                'idempotency_conflict',
                'Idempotency-Key je u konfliktu na gatewayu.',
            ) from exc
        if exc.status_code in (404, 422) and (exc.code or '') in {
            'CAPABILITY_NOT_SUPPORTED',
            'capability_not_supported',
        }:
            raise InboundRejectionError(
                UNAVAILABLE_CAPABILITY,
                'Provider ne podržava e-reporting odbijanje.',
            ) from exc
        raise InboundRejectionError(
            'gateway_unavailable',
            str(exc),
            http_status=502,
        ) from exc

    attempt_id = body.get('attempt_id')
    e_reporting = body.get('e_reporting_status') or 'PENDING'

    try:
        with transaction.atomic():
            attempt = InboundEracunRejectionAttempt.all_objects.create(
                tenant=expense.tenant,
                expense=expense,
                gateway_document_id=link.gateway_document_id,
                attempt_id=attempt_id,
                idempotency_key=idempotency_key,
                reason_code=reason_code,
                reason_text=reason_text or '',
                status=InboundEracunRejectionAttempt.STATUS_PENDING,
                e_reporting_status=e_reporting,
                provider_ref=link.provider_ref,
            )
    except IntegrityError as exc:
        # Concurrent pending or idempotency race — re-read.
        raced = InboundEracunRejectionAttempt.all_objects.filter(
            tenant=expense.tenant,
            idempotency_key=idempotency_key,
        ).first()
        if raced:
            return _success_payload(expense, raced)
        pending = get_pending_rejection(expense)
        if pending:
            raise InboundRejectionError(
                UNAVAILABLE_REJECTION_PENDING,
                'Odbijanje je već u tijeku.',
            ) from exc
        raise

    expense.refresh_from_db(fields=['status'])
    return _success_payload(expense, attempt)


def _request_hash(reason_code: str, reason_text: str) -> str:
    raw = f'{reason_code}\n{reason_text or ""}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _success_payload(expense: Expense, attempt: InboundEracunRejectionAttempt) -> dict[str, Any]:
    document_status = {
        'draft': 'received',
        'approved': 'received',
        'paid': 'received',
        'cancelled': 'cancelled',
        'rejected': 'rejected',
    }.get(expense.status, expense.status or None)
    return {
        'id': expense.pk,
        'status': expense.status,
        'e_reporting': {
            'status': attempt.e_reporting_status or 'PENDING',
            'attempt_id': str(attempt.attempt_id) if attempt.attempt_id else None,
        },
        'lifecycle': {
            'document': document_status,
            'workflow': 'rejection_pending',
        },
    }
