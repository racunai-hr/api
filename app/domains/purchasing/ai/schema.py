"""JSON schema ugovor za OCR ulaznih računa (OpenAI Structured Outputs)."""

OCR_SCHEMA_VERSION = 'purchasing.incoming_invoice.v1'

INVOICE_JSON_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'supplier': {
            'type': 'object',
            'additionalProperties': False,
            'properties': {
                'name': {'type': 'string'},
                'oib': {'type': 'string'},
                'vat_number': {'type': 'string'},
                'address': {'type': 'string'},
                'city': {'type': 'string'},
                'postal_code': {'type': 'string'},
                'country': {'type': 'string'},
                'iban': {'type': 'string'},
            },
            'required': [
                'name',
                'oib',
                'vat_number',
                'address',
                'city',
                'postal_code',
                'country',
                'iban',
            ],
        },
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
        'supplier',
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
    'Datume vrati kao YYYY-MM-DD. Iznose kao decimalni string s točkom (npr. 125.00). '
    'OIB je 11 znamenki kad je hrvatski. '
    'supplier.country: ISO 3166-1 alpha-2 (HR, DE, SI) ako je država jasna, inače naziv kako piše na računu. '
    'Ne pretpostavljaj HR samo zato što OIB izgleda valjan. '
    'Ako polje nije vidljivo, vrati prazan string ili null za due_date. '
    'IBAN bez razmaka. Ne izmišljaj podatke.'
)
