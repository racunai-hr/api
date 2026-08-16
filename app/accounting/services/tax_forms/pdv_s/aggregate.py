"""Aggregate EU stjecanje rows for Obrazac PDV-S from VAT ledger."""

from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType

from accounting.models import JournalEntryLine, VATLedgerEntry, VATPeriod
from accounting.services.tax_forms.pdv.build import _taxpayer_for_period, accountant_for_tenant
from accounting.services.tax_forms.pdv.mapping import _EU_VAT_NUMBER_PREFIXES
from accounting.services.tax_forms.pdv_s.payload import PdvSPayload, PdvSPreparedBy, PdvSRow, PdvSTaxpayer
from expenses.data.manual_supplier_map import MANUAL_SUPPLIER_MAP
from expenses.models import Expense
from settings.models import CompanySettings

_INVOICE_NUMBER_RE = re.compile(r'Račun:\s*(\S+)', re.IGNORECASE)


@dataclass(frozen=True)
class _SupplierKey:
    country_code: str
    pdv_id: str


def _period_bounds(period: VATPeriod) -> tuple[date, date]:
    last_day = calendar.monthrange(period.year, period.month)[1]
    return date(period.year, period.month, 1), date(period.year, period.month, last_day)


def split_eu_vat_id(tax_number: str) -> tuple[str, str]:
    normalized = (tax_number or '').strip().replace(' ', '')
    if len(normalized) < 3 or not normalized[:2].isalpha():
        raise ValueError(f'Neispravan EU PDV ID: {tax_number!r}')
    country_code = normalized[:2].upper()
    if country_code == 'GR':
        country_code = 'EL'
    if country_code not in _EU_VAT_NUMBER_PREFIXES and country_code != 'EL':
        raise ValueError(f'Nepoznat EU prefix u PDV ID-u: {tax_number!r}')
    return country_code, normalized[2:]


def _resolve_supplier_key_from_journal_line(line: JournalEntryLine) -> _SupplierKey | None:
    entry = line.journal_entry
    description = entry.description or ''
    match = _INVOICE_NUMBER_RE.search(description)
    if match:
        invoice_ref = match.group(1).strip()
        expense = Expense.all_objects.filter(
            tenant=entry.tenant,
            receipt_number=invoice_ref,
        ).select_related('supplier').first()
        if expense is None:
            expense = Expense.all_objects.filter(
                tenant=entry.tenant,
                expense_number=invoice_ref,
            ).select_related('supplier').first()
        supplier = expense.supplier if expense else None
        if supplier is not None and supplier.tax_number:
            country_code, pdv_id = split_eu_vat_id(supplier.tax_number)
            return _SupplierKey(country_code=country_code, pdv_id=pdv_id)

        for vat_id, info in MANUAL_SUPPLIER_MAP.items():
            if not vat_id[:2].isalpha():
                continue
            notes = info.get('notes', '')
            if invoice_ref in notes:
                country_code, pdv_id = split_eu_vat_id(vat_id)
                return _SupplierKey(country_code=country_code, pdv_id=pdv_id)

    return None


def _supplier_key_for_ledger_entry(entry: VATLedgerEntry) -> _SupplierKey:
    partner_oib = (entry.partner_oib or '').strip().replace(' ', '')
    if partner_oib:
        country_code, pdv_id = split_eu_vat_id(partner_oib)
        return _SupplierKey(country_code=country_code, pdv_id=pdv_id)

    if entry.source_content_type_id and entry.source_object_id:
        ct = ContentType.objects.get_for_id(entry.source_content_type_id)
        if ct.model == 'journalentryline':
            line = JournalEntryLine.objects.filter(pk=entry.source_object_id).select_related(
                'journal_entry',
            ).first()
            if line is not None:
                key = _resolve_supplier_key_from_journal_line(line)
                if key is not None:
                    return key

    raise ValueError(
        f'PDV-S: nije moguće odrediti EU PDV ID dobavljača za stavku '
        f'{entry.document_number} ({entry.base_amount}).'
    )


def aggregate_pdv_s_rows(period: VATPeriod) -> PdvSPayload:
    """Build PDV-S payload from box 207 (EU goods) and 612 (services) ledger entries."""
    period_from, period_to = _period_bounds(period)
    taxpayer_info = _taxpayer_for_period(period)
    settings = CompanySettings.all_objects.filter(tenant=period.tenant).first()
    if settings is None:
        raise ValueError(f'CompanySettings missing for tenant {period.tenant_id}')

    person = accountant_for_tenant(period.tenant)
    if person is not None:
        prepared_by = PdvSPreparedBy(
            first_name=person.first_name,
            last_name=person.last_name,
            email=person.email or '',
            phone=person.phone or '',
        )
    else:
        prepared_by = PdvSPreparedBy(first_name='NA', last_name='NA')

    goods: dict[_SupplierKey, Decimal] = defaultdict(lambda: Decimal('0.00'))
    services: dict[_SupplierKey, Decimal] = defaultdict(lambda: Decimal('0.00'))

    ledger_qs = VATLedgerEntry.all_objects.filter(
        vat_period=period,
        vat_box__in=('207', '612'),
    ).order_by('entry_date', 'pk')

    for entry in ledger_qs:
        if entry.base_amount <= 0:
            continue
        key = _supplier_key_for_ledger_entry(entry)
        if entry.vat_box == '207':
            goods[key] += entry.base_amount
        else:
            services[key] += entry.base_amount

    all_keys = sorted(goods.keys() | services.keys(), key=lambda k: (k.country_code, k.pdv_id))
    rows = tuple(
        PdvSRow(
            country_code=key.country_code,
            pdv_id=key.pdv_id,
            goods_value=goods.get(key, Decimal('0.00')),
            services_value=services.get(key, Decimal('0.00')),
        )
        for key in all_keys
        if goods.get(key, Decimal('0.00')) or services.get(key, Decimal('0.00'))
    )

    return PdvSPayload(
        period_from=period_from,
        period_to=period_to,
        taxpayer=PdvSTaxpayer(
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
