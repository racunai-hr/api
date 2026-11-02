"""Klasifikacija poslovnog događaja. Ne bira konto i ne knjiži."""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

import requests
from django.conf import settings

CLASSIFIER_PROMPT_VERSION = 1
CLASSIFIER_SCHEMA_VERSION = 1

NATURES = frozenset({'fuel', 'service', 'goods', 'other'})
PROFILES = frozenset({
    'opex',
    'asset_purchase',
    'goods_inward_cost',
    'company_passenger_vehicle',
    'full_vat_vehicle',
    'vehicle_inventory_cost',
})

CLASSIFY_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['purchase_nature', 'suggested_profile', 'required_context', 'reason', 'confidence'],
    'properties': {
        'purchase_nature': {'type': 'string'},
        'suggested_profile': {'type': 'string'},
        'required_context': {'type': 'array', 'items': {'type': 'string'}},
        'reason': {'type': 'string'},
        'confidence': {'type': 'string'},
    },
}

PROFILE_CATALOG = (
    ('goods_inward_cost', 'Zavisni trošak dovođenja robe na prodajnu lokaciju.'),
    ('company_passenger_vehicle', 'Gorivo službenog osobnog vozila s ograničenim odbitkom.'),
    ('full_vat_vehicle', 'Vozilo u poslovnoj uporabi s punim odbitkom pretporeza.'),
    ('opex', 'Običan operativni trošak.'),
    ('asset_purchase', 'Nabava dugotrajne imovine.'),
)


def empty_classification() -> dict[str, Any]:
    return {
        'purchase_nature': None,
        'suggested_profile': None,
        'required_context': [],
        'reason': '',
        'confidence': 'low',
        'accepted': False,
    }


def document_facts(facts: dict) -> dict[str, Any]:
    """Činjenice dokumenta. Bez vozila, odgovora, povijesti i zapamćenog profila."""
    lines = []
    for item in facts.get('lines') or []:
        if not isinstance(item, dict):
            continue
        lines.append({
            'description': str(item.get('description') or ''),
            'amount': str(item.get('amount') or ''),
        })
    return {
        'number': str(facts.get('number') or ''),
        'document_date': str(facts.get('document_date') or ''),
        'net_amount': str(facts.get('net_amount') or ''),
        'tax_amount': str(facts.get('tax_amount') or ''),
        'gross_amount': str(facts.get('gross_amount') or ''),
        'currency': str(facts.get('currency') or ''),
        'tax_category': str(facts.get('tax_category') or ''),
        'lines': lines,
    }


def facts_digest(facts: dict) -> str:
    payload = json.dumps(document_facts(facts), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def classify_event(facts: dict, tenant=None) -> dict[str, Any]:
    model = _model_identifier()
    cached = _read_cache(tenant, facts, model)
    if cached is not None:
        return accept_classification(cached)
    try:
        raw = _invoke(document_facts(facts))
    except Exception:
        return empty_classification()
    _write_cache(tenant, facts, model, raw if isinstance(raw, dict) else {})
    return accept_classification(raw if isinstance(raw, dict) else {})


def accept_classification(raw: dict | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return empty_classification()
    nature = raw.get('purchase_nature')
    if nature not in NATURES:
        return empty_classification()
    profile = raw.get('suggested_profile') or None
    if profile == '':
        profile = None
    if profile is not None and profile not in PROFILES:
        profile = None
    confidence = raw.get('confidence') if raw.get('confidence') in {'high', 'low'} else 'low'
    if confidence != 'high':
        profile = None
    context = raw.get('required_context')
    if not isinstance(context, list):
        context = []
    context = [str(item) for item in context if isinstance(item, str)]
    return {
        'purchase_nature': nature,
        'suggested_profile': profile,
        'required_context': context,
        'reason': str(raw.get('reason') or ''),
        'confidence': confidence,
        'accepted': True,
    }


def build_prompt(facts: dict) -> str:
    document = document_facts(facts)
    lines = document['lines']
    line_text = '\n'.join(
        f"- {item.get('description') or ''} | {item.get('amount') or ''}"
        for item in lines
    ) or '-'
    catalog = '\n'.join(f'- {code}: {label}' for code, label in PROFILE_CATALOG)
    return (
        'Klasificiraj poslovni događaj ulaznog računa. '
        'Ne biraj konto i ne računaj porez.\n'
        f"Broj: {document['number']}\n"
        f"Datum: {document['document_date']}\n"
        f"Neto: {document['net_amount']}\n"
        f"Porez: {document['tax_amount']}\n"
        f"Bruto: {document['gross_amount']}\n"
        f"Valuta: {document['currency']}\n"
        f"Kategorija: {document['tax_category']}\n"
        f"Stavke:\n{line_text}\n"
        f"Profili:\n{catalog}"
    )


def _provider() -> str:
    configured = (getattr(settings, 'ACCOUNTING_CLASSIFY_PROVIDER', '') or '').strip().lower()
    if configured:
        return configured
    if any(arg == 'test' for arg in sys.argv):
        return 'fake'
    return 'openai'


def _model_identifier() -> str:
    return (getattr(settings, 'OPENAI_ACCOUNT_SUGGESTION_MODEL', '') or '').strip()


def _cache_filter(tenant, facts: dict, model: str):
    from expenses.models import AccountingClassification

    return AccountingClassification.all_objects.filter(
        tenant=tenant,
        facts_hash=facts_digest(facts),
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        schema_version=CLASSIFIER_SCHEMA_VERSION,
        model=model,
    )


def _read_cache(tenant, facts: dict, model: str):
    if tenant is None or not model:
        return None
    row = _cache_filter(tenant, facts, model).first()
    if row is None or not isinstance(row.payload, dict):
        return None
    return row.payload


def _write_cache(tenant, facts: dict, model: str, payload: dict) -> None:
    if tenant is None or not model:
        return
    from expenses.models import AccountingClassification

    AccountingClassification.all_objects.get_or_create(
        tenant=tenant,
        facts_hash=facts_digest(facts),
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        schema_version=CLASSIFIER_SCHEMA_VERSION,
        model=model,
        defaults={'payload': payload},
    )


def _invoke(facts: dict) -> dict:
    provider = _provider()
    if provider == 'fake':
        return dict(getattr(settings, 'ACCOUNTING_CLASSIFY_FAKE', None) or {})
    if provider == 'off':
        return {}
    return _call_openai(facts)


def _call_openai(facts: dict) -> dict:
    api_key = (getattr(settings, 'OPENAI_API_KEY', '') or '').strip()
    if not api_key:
        raise RuntimeError('OPENAI_API_KEY nije postavljen.')
    model = settings.OPENAI_ACCOUNT_SUGGESTION_MODEL
    timeout = int(getattr(settings, 'OPENAI_OCR_TIMEOUT_SECONDS', 60))
    body = {
        'model': model,
        'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': build_prompt(facts)}]}],
        'text': {
            'format': {
                'type': 'json_schema',
                'name': 'accounting_event_classification',
                'strict': True,
                'schema': CLASSIFY_SCHEMA,
            }
        },
    }
    response = requests.post(
        'https://api.openai.com/v1/responses',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    return _payload(response.json())


def _payload(body: dict) -> dict:
    for item in body.get('output') or []:
        for content in item.get('content') or []:
            if content.get('type') == 'output_text' and content.get('text'):
                parsed = json.loads(content['text'])
                if isinstance(parsed, dict):
                    return parsed
    text = body.get('output_text')
    if isinstance(text, str) and text.strip():
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError('Klasifikacija nema JSON odgovor.')
