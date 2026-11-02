"""Inbound document direction from OCR issuer/buyer vs tenant identity.

Hard own-company is OIB/VAT only. Name-only is a UI suspicion and must not
drive direction cases 2–4 or block create-partner/confirm.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from domains.purchasing.services.matching import normalize_oib, party_from_raw
from shared.vat import normalize_vat_number

DIRECTION_OK = 'ok'
DIRECTION_REVIEW_REQUIRED = 'review_required'
DIRECTION_WRONG = 'suspected_wrong_document_direction'
DIRECTION_TENANT_NOT_ON_DOCUMENT = 'tenant_not_on_document'

SOURCE_ISSUER = 'issuer'
SOURCE_BUYER = 'buyer'
SOURCE_MANUAL = 'manual'

_LEGAL_SUFFIX = re.compile(
    r'\b(d\.?\s*o\.?\s*o\.?|d\.?\s*d\.?|j\.?\s*d\.?\s*o\.?\s*o\.?|doo|dd)\b',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CompanyIdentity:
    name: str = ''
    oib: str = ''
    vat_id: str = ''

    @property
    def tax_known(self) -> bool:
        return bool(_company_tax_ids(self))


@dataclass(frozen=True)
class PartyFlags:
    is_own_company: bool = False
    suspected_own_company: bool = False


@dataclass(frozen=True)
class DirectionResult:
    code: str
    propose_supplier: bool
    issuer_flags: PartyFlags
    buyer_flags: PartyFlags
    warnings: tuple[str, ...]
    identity: CompanyIdentity


def normalize_company_name(value: str) -> str:
    text = unicodedata.normalize('NFKD', value or '')
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = _LEGAL_SUFFIX.sub(' ', text)
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return ' '.join(text.split())


def _tax_ids_from_oib_vat(*, oib: str = '', vat_number: str = '') -> set[str]:
    ids: set[str] = set()
    oib_n = normalize_oib(oib)
    vat_n = normalize_vat_number(vat_number)
    if oib_n:
        ids.add(oib_n)
        ids.add(f'HR{oib_n}')
    if vat_n:
        ids.add(vat_n)
        if vat_n.startswith('HR') and len(vat_n) == 13:
            ids.add(vat_n[2:])
        elif vat_n.isdigit() and len(vat_n) == 11:
            ids.add(f'HR{vat_n}')
    return ids


def _company_tax_ids(identity: CompanyIdentity) -> set[str]:
    return _tax_ids_from_oib_vat(oib=identity.oib, vat_number=identity.vat_id)


def _party_tax_ids(party: dict) -> set[str]:
    return _tax_ids_from_oib_vat(
        oib=str(party.get('oib') or ''),
        vat_number=str(party.get('vat_number') or ''),
    )


def party_is_blank(party: dict | None) -> bool:
    if not isinstance(party, dict):
        return True
    return not any(
        str(party.get(field) or '').strip()
        for field in ('name', 'oib', 'vat_number', 'iban')
    )


def classify_party(party: dict, identity: CompanyIdentity) -> PartyFlags:
    if party_is_blank(party) or not (identity.tax_known or identity.name.strip()):
        return PartyFlags()
    hard = bool(_party_tax_ids(party) & _company_tax_ids(identity))
    if hard:
        return PartyFlags(is_own_company=True)
    if _party_tax_ids(party):
        return PartyFlags()
    party_name = normalize_company_name(str(party.get('name') or ''))
    company_name = normalize_company_name(identity.name)
    suspected = bool(party_name and company_name and party_name == company_name)
    return PartyFlags(suspected_own_company=suspected)


def is_hard_own_company(party: dict, identity: CompanyIdentity) -> bool:
    return classify_party(party, identity).is_own_company


def load_company_identity(tenant) -> CompanyIdentity:
    from settings.models import CompanySettings

    settings = CompanySettings.all_objects.filter(tenant=tenant).first()
    if settings is None:
        return CompanyIdentity()
    return CompanyIdentity(
        name=str(settings.company_name or ''),
        oib=str(settings.vat_number or ''),
        vat_id=str(settings.vat_id or ''),
    )


def _raw_party(payload: dict, key: str) -> dict:
    raw = payload.get(key) if isinstance(payload, dict) else None
    if isinstance(raw, dict):
        return raw
    return {}


def _raw_has_identity(raw: dict) -> bool:
    if not isinstance(raw, dict):
        return False
    return any(str(raw.get(field) or '').strip() for field in ('name', 'oib', 'vat_number', 'tax_number'))


def normalize_parties(payload: dict) -> dict:
    """v1 supplier-only payloads become issuer; buyer stays empty."""
    data = dict(payload or {})
    issuer_raw = _raw_party(data, 'issuer')
    if not _raw_has_identity(issuer_raw):
        issuer_raw = _raw_party(data, 'supplier')
    buyer_raw = _raw_party(data, 'buyer')
    data['issuer'] = issuer_raw
    data['buyer'] = buyer_raw
    return data


def classify_document(payload: dict, identity: CompanyIdentity) -> DirectionResult:
    data = normalize_parties(payload)
    issuer = party_from_raw(data.get('issuer') or {}, fallback_iban=str(data.get('iban') or ''))
    buyer = party_from_raw(data.get('buyer') or {})
    issuer_flags = classify_party(issuer, identity)
    buyer_flags = classify_party(buyer, identity)
    issuer_own = issuer_flags.is_own_company
    buyer_own = buyer_flags.is_own_company
    warnings: list[str] = []

    if issuer_own and buyer_own:
        code = DIRECTION_REVIEW_REQUIRED
        propose = False
        warnings.append(
            'OCR konflikt: i izdavatelj i kupac imaju porezni identifikator vaše tvrtke. '
            'Pregledajte strane prije potvrde.'
        )
    elif issuer_own and not buyer_own:
        code = DIRECTION_WRONG
        propose = False
        warnings.append(
            'Izdavatelj je vaša tvrtka. Ovo može biti izlazni račun, krivo učitan dokument '
            'ili OCR koji je zamijenio strane. Ne knjižimo ulazni račun dok ne odaberete '
            'pravog dobavljača ili ne odbacite nacrt.'
        )
    elif not issuer_own and buyer_own:
        code = DIRECTION_OK
        propose = True
    else:
        code = DIRECTION_TENANT_NOT_ON_DOCUMENT
        propose = True
        if identity.tax_known:
            warnings.append(
                'Kupac nije prepoznat po OIB-u/PDV ID-u vaše tvrtke. '
                'Potvrdite da je izdavatelj stvarni dobavljač prije knjiženja nacrta.'
            )

    if issuer_flags.suspected_own_company:
        warnings.append('Izdavatelj izgleda kao vaša tvrtka (samo naziv).')
    if buyer_flags.suspected_own_company:
        warnings.append('Kupac izgleda kao vaša tvrtka (samo naziv).')

    return DirectionResult(
        code=code,
        propose_supplier=propose,
        issuer_flags=issuer_flags,
        buyer_flags=buyer_flags,
        warnings=tuple(warnings),
        identity=identity,
    )


def apply_direction_to_payload(payload: dict, identity: CompanyIdentity) -> tuple[dict, DirectionResult]:
    """Set issuer/buyer/supplier/direction/supplier_source. Never copies buyer into supplier."""
    data = normalize_parties(payload)
    result = classify_document(data, identity)
    issuer_raw = dict(data.get('issuer') or {})
    buyer_raw = dict(data.get('buyer') or {})
    data['issuer'] = issuer_raw
    data['buyer'] = buyer_raw
    data['direction'] = result.code
    if result.propose_supplier:
        data['supplier'] = dict(issuer_raw)
        data['supplier_source'] = SOURCE_ISSUER
    else:
        data['supplier'] = {}
        data['supplier_source'] = ''
    return data, result


def party_candidate(role: str, raw: dict, flags: PartyFlags, *, fallback_iban: str = '') -> dict:
    party = party_from_raw(raw, fallback_iban=fallback_iban)
    return {
        'role': role,
        'name': party['name'],
        'oib': party['oib'],
        'vat_number': party['vat_number'],
        'address': party['address'],
        'city': party['city'],
        'postal_code': party['postal_code'],
        'country': party['country'],
        'country_code': party['country_code'],
        'iban': party['iban'],
        'is_own_company': flags.is_own_company,
        'suspected_own_company': flags.suspected_own_company,
        'blank': party_is_blank(party),
    }


def supplier_source_of(payload: dict) -> str:
    return str((payload or {}).get('supplier_source') or '')


def direction_unresolved(result: DirectionResult, payload: dict) -> bool:
    if result.code not in {DIRECTION_REVIEW_REQUIRED, DIRECTION_WRONG}:
        return False
    source = supplier_source_of(payload)
    return source not in {SOURCE_BUYER, SOURCE_MANUAL}


def direction_override_required(result: DirectionResult, *, override: bool) -> bool:
    if result.code != DIRECTION_TENANT_NOT_ON_DOCUMENT:
        return False
    if not result.identity.tax_known:
        return False
    return not override
