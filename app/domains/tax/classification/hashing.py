"""Deterministic hash of tax-relevant input fields."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from domains.tax.classification.contracts import OriginTaxEffect, PartnerSnapshot, TaxDocumentInput


def _dec(value: Decimal | None) -> str:
    if value is None:
        return ''
    return format(value, 'f')


def _partner(snapshot: PartnerSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {}
    return {
        'name': snapshot.name,
        'country': snapshot.country,
        'tax_number': snapshot.tax_number,
        'vat_id': snapshot.vat_id,
        'provenance': snapshot.provenance.value,
    }


def _effect(effect: OriginTaxEffect) -> dict[str, Any]:
    return {
        'box': effect.box,
        'base': _dec(effect.base_amount),
        'tax': _dec(effect.tax_amount),
        'direction': effect.direction.value,
        'sign': _dec(effect.sign),
        'source_kind': effect.source_kind,
        'source_document_id': effect.source_document_id,
        'source_line_id': effect.source_line_id,
        'ledger_entry_id': effect.ledger_entry_id,
    }


def hash_tax_input(document: TaxDocumentInput) -> str:
    payload = {
        'tenant_id': document.tenant_id,
        'source_kind': document.source_kind,
        'source_document_id': document.source_document_id,
        'source_line_id': document.source_line_id,
        'lifecycle_status': document.lifecycle_status,
        'event_kind': document.event_kind.value,
        'direction': document.direction.value,
        'document_date': document.document_date.isoformat(),
        'supply_date': document.supply_date.isoformat() if document.supply_date else '',
        'partner': _partner(document.partner),
        'base_amount': _dec(document.base_amount),
        'vat_rate': _dec(document.vat_rate),
        'vat_amount': _dec(document.vat_amount),
        'currency': document.currency,
        'jurisdiction': document.jurisdiction,
        'customer_type': document.customer_type,
        'supply_kind': document.supply_kind,
        'declared_procedure': document.declared_procedure,
        'originates_from': document.originates_from or '',
        'origin_tax_effects': [_effect(item) for item in document.origin_tax_effects],
        'origin_effects_ambiguous': document.origin_effects_ambiguous,
        'tax_relevance': document.tax_relevance.value,
        'origin_tax_owner': document.origin_tax_owner.value,
        'has_linked_journal_entry': document.has_linked_journal_entry,
        'account_code': document.account_code or '',
        'debit_amount': _dec(document.debit_amount),
        'credit_amount': _dec(document.credit_amount),
        'description': document.description,
        'period_year': document.period_year,
        'period_month': document.period_month,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()
