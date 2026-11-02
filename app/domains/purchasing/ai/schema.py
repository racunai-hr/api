"""JSON schema ugovor za OCR ulaznih računa (OpenAI Structured Outputs)."""

OCR_SCHEMA_VERSION = 'purchasing.incoming_invoice.v2'

_PARTY_PROPERTIES = {
    'name': {'type': 'string'},
    'oib': {'type': 'string'},
    'vat_number': {'type': 'string'},
    'address': {'type': 'string'},
    'city': {'type': 'string'},
    'postal_code': {'type': 'string'},
    'country': {'type': 'string'},
    'iban': {'type': 'string'},
}

_PARTY_REQUIRED = [
    'name',
    'oib',
    'vat_number',
    'address',
    'city',
    'postal_code',
    'country',
    'iban',
]


def _party_schema() -> dict:
    return {
        'type': 'object',
        'additionalProperties': False,
        'properties': dict(_PARTY_PROPERTIES),
        'required': list(_PARTY_REQUIRED),
    }


INVOICE_JSON_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'issuer': _party_schema(),
        'buyer': _party_schema(),
        'invoice_number': {'type': 'string'},
        'issue_date': {'type': 'string'},
        'due_date': {'type': ['string', 'null']},
        'currency': {'type': 'string'},
        'net_amount': {'type': 'string'},
        'tax_amount': {'type': 'string'},
        'total_amount': {'type': 'string'},
        'iban': {'type': 'string'},
        'vat_breakdown': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'rate': {'type': 'string'},
                    'base': {'type': 'string'},
                    'amount': {'type': 'string'},
                },
                'required': ['rate', 'base', 'amount'],
            },
        },
        'line_items': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'description': {'type': 'string'},
                    'quantity': {'type': ['string', 'null']},
                    'unit_price': {'type': ['string', 'null']},
                    'amount': {'type': ['string', 'null']},
                },
                'required': ['description', 'quantity', 'unit_price', 'amount'],
            },
        },
        'warnings': {
            'type': 'array',
            'items': {'type': 'string'},
        },
    },
    'required': [
        'issuer',
        'buyer',
        'invoice_number',
        'issue_date',
        'due_date',
        'currency',
        'net_amount',
        'tax_amount',
        'total_amount',
        'iban',
        'vat_breakdown',
        'line_items',
        'warnings',
    ],
}

EXTRACT_PROMPT = (
    'Izvuci podatke s ulaznog računa. '
    'issuer je izdavatelj (tko izdaje račun) — to je dobavljač. '
    'buyer je kupac/primatelj. Nikad ne stavi kupca u issuer. '
    'Datume vrati kao YYYY-MM-DD. Iznose kao decimalni string s točkom (npr. 125.00). '
    'OIB je 11 znamenki kad je hrvatski. '
    'issuer.country i buyer.country: ISO 3166-1 alpha-2 (HR, DE, SI) ako je država jasna, inače naziv kako piše na računu. '
    'Ne pretpostavljaj HR samo zato što OIB izgleda valjan. '
    'Ako polje nije vidljivo, vrati prazan string ili null za due_date. '
    'IBAN bez razmaka. Ne izmišljaj podatke.'
)


def build_extract_prompt(*, company_name: str = '', oib: str = '', vat_id: str = '') -> str:
    prompt = EXTRACT_PROMPT
    name = (company_name or '').strip()
    oib_s = (oib or '').strip()
    vat_s = (vat_id or '').strip()
    if not (name or oib_s or vat_s):
        return prompt
    parts = [name or 'ova tvrtka']
    if oib_s:
        parts.append(f'OIB {oib_s}')
    if vat_s:
        parts.append(f'PDV ID {vat_s}')
    identity = ', '.join(parts)
    return (
        prompt
        + f' Primatelj/kupac ovog ulaznog računa je {identity}. '
        'To je buyer, NE issuer. Izdavatelj je druga strana na dokumentu.'
    )
