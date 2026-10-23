"""PDV / PDV-S period read-models."""

from __future__ import annotations

from datetime import date

from django.db.models import Exists, OuterRef

from accounting.models import PDVSReturn, VATLedgerEntry, VATPeriod, VATReturnStatus
from accounting.services.submission.events import get_latest_zp_return, get_submission_events
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.pdv.canonical import payload_to_dict
from accounting.services.tax_forms.pdv.correction import working_return
from accounting.services.tax_forms.pdv.integrity import check_vat_return_integrity
from accounting.services.tax_forms.pdv.build import build_pdv_payload
from accounting.services.tax_forms.pdv_s.aggregate import aggregate_pdv_s_rows
from accounting.services.tax_forms.pdv_s.payload import PDV_S_SCHEMA_VERSION
from accounting.services.tax_forms.zp.aggregate import aggregate_zp_rows
from accounting.services.tax_forms.zp.payload import ZP_SCHEMA_VERSION
from domains.tax.read.dto import (
    money2,
    parse_period_key,
    pdv_period_dto,
    pdv_s_submission_dto,
    period_key,
)


def list_pdv_periods(tenant) -> dict:
    qs = (
        VATPeriod.all_objects.filter(tenant=tenant)
        .annotate(
            has_ledger=Exists(
                VATLedgerEntry.all_objects.filter(vat_period_id=OuterRef('pk')),
            ),
        )
        .order_by('-year', '-month')
    )
    results = [pdv_period_dto(period, has_ledger=period.has_ledger) for period in qs]
    return {'count': len(results), 'results': results}


def get_vat_period(tenant, raw_period: str) -> VATPeriod | None:
    parsed = parse_period_key(raw_period)
    if parsed is None:
        return None
    year, month = parsed
    return VATPeriod.all_objects.filter(tenant=tenant, year=year, month=month).first()


def pdv_form_rows(period: VATPeriod) -> list[dict]:
    """Forms ePorezna shows for one month: received or booked PDV returns, newest first."""
    rows = []
    returns = period.returns.filter(
        status__in=(
            VATReturnStatus.IN_PROCESSING,
            VATReturnStatus.SUBMITTED,
            VATReturnStatus.SUPERSEDED,
        ),
    ).order_by('-version')
    for vat_return in returns:
        event = SubmissionService.current_submission(vat_return)
        received_at = event.submitted_at if event is not None else None
        if received_at is None and vat_return.status == VATReturnStatus.SUBMITTED:
            received_at = period.submitted_at
        snapshot = vat_return.payload_snapshot or {}
        rows.append({
            'form': 'PDV',
            'version': vat_return.version,
            'status': vat_return.status,
            'received_at': received_at.isoformat() if received_at is not None else None,
            'period_from': snapshot.get('period_from') or date(period.year, period.month, 1).isoformat(),
            'period_to': snapshot.get('period_to') or '',
            'external_identifier': str(event.external_identifier) if event is not None else None,
        })
    return rows


def pdv_workspace_dto(period: VATPeriod) -> dict:
    has_ledger = VATLedgerEntry.all_objects.filter(vat_period=period).exists()
    payload = pdv_period_dto(period, has_ledger=has_ledger)
    vat_return = working_return(period)
    xml_integrity = None
    event_uuid = None
    has_confirmation = False
    if vat_return is not None:
        xml_integrity = check_vat_return_integrity(vat_return).status
        event = SubmissionService.current_submission(vat_return)
        if event is not None:
            event_uuid = str(event.event_uuid)
            has_confirmation = bool(event.confirmation_attachment)
    payload['xml_integrity'] = xml_integrity
    payload['event_uuid'] = event_uuid
    payload['has_confirmation'] = has_confirmation
    payload['forms'] = pdv_form_rows(period)
    return payload


def pdv_boxes_dto(period: VATPeriod) -> dict:
    payload = build_pdv_payload(period)
    raw = payload_to_dict(payload)
    return {
        'period': period_key(period),
        'schema_version': raw['schema_version'],
        'mapping_version': raw['mapping_version'],
        'vat_due': money2(payload.field_scalar('400')),
        'fields': raw['fields'],
    }


def pdv_s_period_dto(period: VATPeriod) -> dict:
    payload = aggregate_pdv_s_rows(period)
    event_uuid = None
    current_submission = None
    submissions: list[dict] = []
    pdv_s_return = (
        PDVSReturn.all_objects.filter(tenant=period.tenant, vat_period=period).first()
    )
    if pdv_s_return is not None:
        submissions = [
            pdv_s_submission_dto(event) for event in get_submission_events(pdv_s_return)
        ]
        current = SubmissionService.current_submission(pdv_s_return)
        if current is not None:
            current_submission = pdv_s_submission_dto(current)
            event_uuid = current_submission['event_uuid']
    rows = [
        {
            'country_code': row.country_code,
            'pdv_id': row.pdv_id,
            'goods_value': money2(row.goods_value),
            'services_value': money2(row.services_value),
        }
        for row in payload.rows
    ]
    return {
        'period': period_key(period),
        'schema_version': PDV_S_SCHEMA_VERSION,
        'row_count': len(rows),
        'total_goods': money2(payload.total_goods),
        'total_services': money2(payload.total_services),
        'rows': rows,
        'event_uuid': event_uuid,
        'current_submission': current_submission,
        'submissions': submissions,
    }


def zp_period_dto(period: VATPeriod) -> dict:
    payload = aggregate_zp_rows(period)
    event_uuid = None
    current_submission = None
    submissions: list[dict] = []
    zp_return = get_latest_zp_return(period)
    if zp_return is not None:
        submissions = [
            pdv_s_submission_dto(event) for event in get_submission_events(zp_return)
        ]
        current = SubmissionService.current_submission(zp_return)
        if current is not None:
            current_submission = pdv_s_submission_dto(current)
            event_uuid = current_submission['event_uuid']
    rows = [
        {
            'country_code': row.country_code,
            'pdv_id': row.pdv_id,
            'goods_value': money2(row.goods_value),
            'services_value': money2(row.services_value),
        }
        for row in payload.rows
    ]
    return {
        'period': period_key(period),
        'schema_version': ZP_SCHEMA_VERSION,
        'row_count': len(rows),
        'total_goods': money2(payload.total_goods),
        'total_services': money2(payload.total_services),
        'rows': rows,
        'event_uuid': event_uuid,
        'current_submission': current_submission,
        'submissions': submissions,
    }
