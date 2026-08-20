from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.utils.dateparse import parse_date
from shared.countries import (
    country_display_name,
    is_valid_country_code,
    map_legacy_country,
    normalize_country_code,
)
from shared.iban import normalize_iban
from shared.vat import normalize_vat_number

from domains.purchasing.services.exceptions import PurchasingBadRequest
from expenses.models import IncomingInvoiceImport
from partners.models import Partner, PartnerBankAccount


def digits_only(value: str) -> str:
    return ''.join(ch for ch in (value or '') if ch.isdigit())


def normalize_oib(value: str) -> str:
    return digits_only(value)[:11]


def _blank(value) -> bool:
    return value is None or str(value).strip() in ('', '—', '-')


def resolve_country_code(*, country_code: str = '', country: str = '') -> str | None:
    """Map OCR/API country text to ISO2. Does not infer HR from OIB (ADR-0023)."""
    raw_code = normalize_country_code(country_code)
    if raw_code:
        return raw_code if is_valid_country_code(raw_code) else None
    return map_legacy_country(country)


def require_country_code(*, country_code: str = '', country: str = '') -> str:
    code = resolve_country_code(country_code=country_code, country=country)
    if not code:
        raise PurchasingBadRequest(
            'partner_country_invalid',
            'Država nije u ISO katalogu. Unesite country_code (npr. HR).',
        )
    return code


def supplier_from_payload(payload: dict) -> dict:
    raw = payload.get('supplier') if isinstance(payload, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    iban = normalize_iban(str(raw.get('iban') or payload.get('iban') or ''))
    country = str(raw.get('country') or '').strip()
    country_code = resolve_country_code(
        country_code=str(raw.get('country_code') or ''),
        country=country,
    ) or ''
    return {
        'name': str(raw.get('name') or '').strip(),
        'oib': normalize_oib(str(raw.get('oib') or '')),
        'vat_number': normalize_vat_number(str(raw.get('vat_number') or '')),
        'address': str(raw.get('address') or '').strip(),
        'city': str(raw.get('city') or '').strip(),
        'postal_code': str(raw.get('postal_code') or '').strip(),
        'country': country_display_name(country_code) if country_code else country,
        'country_code': country_code,
        'iban': iban,
    }


def parse_money(value) -> Decimal | None:
    text = str(value or '').strip().replace(' ', '').replace(',', '.')
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, TypeError):
        return None


def parse_iso_date(value):
    text = str(value or '').strip()
    if not text:
        return None
    return parse_date(text)


def compute_partner_diff(partner: Partner | None, supplier: dict) -> list[dict]:
    if partner is None:
        return []
    diffs: list[dict] = []
    field_map = (
        ('address', partner.address, supplier.get('address') or ''),
        ('city', partner.city, supplier.get('city') or ''),
        ('postal_code', partner.postal_code, supplier.get('postal_code') or ''),
    )
    for field, existing, extracted in field_map:
        existing_s = (existing or '').strip()
        extracted_s = (extracted or '').strip()
        if extracted_s and existing_s != extracted_s and _blank(existing_s):
            diffs.append({'field': field, 'existing': existing_s, 'extracted': extracted_s})
    extracted_iban = supplier.get('iban') or ''
    if extracted_iban:
        existing_ibans = [
            normalize_iban(row.iban)
            for row in PartnerBankAccount.objects.filter(partner=partner)
        ]
        if extracted_iban not in existing_ibans:
            diffs.append({
                'field': 'iban',
                'existing': existing_ibans[0] if existing_ibans else '',
                'extracted': extracted_iban,
            })
    return diffs


def match_partner(*, tenant, payload: dict) -> dict:
    supplier = supplier_from_payload(payload)
    oib = supplier['oib']
    vat = supplier['vat_number']
    iban = supplier['iban']

    partner = None
    kind = IncomingInvoiceImport.MATCH_MISSING
    if oib:
        partner = Partner.all_objects.filter(tenant=tenant, tax_number=oib).first()
        if partner:
            kind = IncomingInvoiceImport.MATCH_EXACT_OIB
    if partner is None and vat:
        partner = Partner.all_objects.filter(tenant=tenant, vat_number=vat).exclude(vat_number='').first()
        if partner:
            kind = IncomingInvoiceImport.MATCH_VAT

    candidate_id = None
    if partner is None and iban:
        account = (
            PartnerBankAccount.objects.filter(iban=iban, partner__tenant=tenant)
            .select_related('partner')
            .first()
        )
        if account:
            kind = IncomingInvoiceImport.MATCH_IBAN_CANDIDATE
            candidate_id = account.partner_id
            partner = account.partner

    return {
        'partner': partner,
        'kind': kind,
        'candidate_id': candidate_id,
        'diff': compute_partner_diff(partner, supplier),
        'supplier': supplier,
    }
