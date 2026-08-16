"""Aggregate EU outbound rows for Obrazac ZP from VAT ledger (I-RA)."""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from accounting.models import VATLedgerEntry, VATPeriod
from accounting.services.tax_forms.pdv.build import _taxpayer_for_period, accountant_for_tenant
from accounting.services.tax_forms.pdv_s.aggregate import split_eu_vat_id
from accounting.services.tax_forms.zp.payload import ZpPayload, ZpPreparedBy, ZpRow, ZpTaxpayer
from settings.models import CompanySettings


@dataclass(frozen=True)
class _RecipientKey:
    country_code: str
    pdv_id: str


def _period_bounds(period: VATPeriod) -> tuple[date, date]:
    last_day = calendar.monthrange(period.year, period.month)[1]
    return date(period.year, period.month, 1), date(period.year, period.month, last_day)


def _split_recipient_vat_id(vat_id: str) -> tuple[str, str]:
    try:
        return split_eu_vat_id(vat_id)
    except ValueError as exc:
        if 'Neispravan EU PDV ID' in str(exc):
            raise
        raise ValueError(f'Neispravan EU PDV ID: {vat_id!r}') from exc


def _recipient_key_for_ledger_entry(entry: VATLedgerEntry) -> _RecipientKey:
    partner_vat = (entry.partner_oib or '').strip().replace(' ', '')
    if not partner_vat:
        raise ValueError(
            f'ZP: nije moguće odrediti EU PDV ID primatelja za stavku '
            f'{entry.document_number} ({entry.base_amount}).'
        )
    country_code, pdv_id = _split_recipient_vat_id(partner_vat)
    return _RecipientKey(country_code=country_code, pdv_id=pdv_id)


def aggregate_zp_rows(period: VATPeriod) -> ZpPayload:
    """Build ZpPayload from VATLedgerEntry (boxes 101, 103)."""
    period_from, period_to = _period_bounds(period)
    taxpayer_info = _taxpayer_for_period(period)
    settings = CompanySettings.all_objects.filter(tenant=period.tenant).first()
    if settings is None:
        raise ValueError(f'CompanySettings missing for tenant {period.tenant_id}')

    person = accountant_for_tenant(period.tenant)
    if person is not None:
        prepared_by = ZpPreparedBy(
            first_name=person.first_name,
            last_name=person.last_name,
            email=person.email or '',
            phone=person.phone or '',
        )
    else:
        prepared_by = ZpPreparedBy(first_name='NA', last_name='NA')

    goods: dict[_RecipientKey, Decimal] = defaultdict(lambda: Decimal('0.00'))
    services: dict[_RecipientKey, Decimal] = defaultdict(lambda: Decimal('0.00'))

    ledger_qs = VATLedgerEntry.all_objects.filter(
        vat_period=period,
        vat_box__in=('101', '103'),
    ).order_by('entry_date', 'pk')

    for entry in ledger_qs:
        if entry.base_amount == 0:
            continue
        key = _recipient_key_for_ledger_entry(entry)
        if entry.vat_box == '101':
            goods[key] += entry.base_amount
        else:
            services[key] += entry.base_amount

    all_keys = sorted(goods.keys() | services.keys(), key=lambda k: (k.country_code, k.pdv_id))
    rows = tuple(
        ZpRow(
            country_code=key.country_code,
            pdv_id=key.pdv_id,
            goods_value=goods.get(key, Decimal('0.00')),
            services_value=services.get(key, Decimal('0.00')),
        )
        for key in all_keys
        if goods.get(key, Decimal('0.00')) or services.get(key, Decimal('0.00'))
    )

    return ZpPayload(
        period_from=period_from,
        period_to=period_to,
        taxpayer=ZpTaxpayer(
            name=taxpayer_info.name,
            oib=taxpayer_info.oib,
            city=taxpayer_info.city,
            street=taxpayer_info.street,
            house_number=taxpayer_info.house_number,
        ),
        prepared_by=prepared_by,
        tax_office_code=settings.tax_office.code if settings.tax_office_id else '',
        rows=rows,
    )
