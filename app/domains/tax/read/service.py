"""PDV / PDV-S period read-models."""

from __future__ import annotations

from django.db.models import Exists, OuterRef

from accounting.models import PDVSReturn, VATLedgerEntry, VATPeriod
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.pdv.canonical import payload_to_dict
from accounting.services.tax_forms.pdv.integrity import check_vat_return_integrity
from accounting.services.tax_forms.pdv.build import build_pdv_payload
from accounting.services.tax_forms.pdv_s.aggregate import aggregate_pdv_s_rows
from accounting.services.tax_forms.pdv_s.payload import PDV_S_SCHEMA_VERSION
from domains.tax.read.dto import money2, parse_period_key, pdv_period_dto, period_key


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


def pdv_workspace_dto(period: VATPeriod) -> dict:
    has_ledger = VATLedgerEntry.all_objects.filter(vat_period=period).exists()
    payload = pdv_period_dto(period, has_ledger=has_ledger)
    vat_return = period.current_return
    xml_integrity = None
    event_uuid = None
    if vat_return is not None:
        xml_integrity = check_vat_return_integrity(vat_return).status
        event = SubmissionService.current_submission(vat_return)
        if event is not None:
            event_uuid = str(event.event_uuid)
    payload['xml_integrity'] = xml_integrity
    payload['event_uuid'] = event_uuid
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
    pdv_s_return = (
        PDVSReturn.all_objects.filter(tenant=period.tenant, vat_period=period).first()
    )
    if pdv_s_return is not None:
        event = SubmissionService.current_submission(pdv_s_return)
        if event is not None:
            event_uuid = str(event.event_uuid)
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
    }
