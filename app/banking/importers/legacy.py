"""DEPRECATED — legacy dict parsers za postojeći admin import po izvodu."""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from io import StringIO

from banking.parsers.camt053 import parse_camt053 as _parse_camt053_statements


def parse_camt053(content: str | bytes) -> dict:
    """Legacy wrapper — vraća prvi izvod kao dict."""
    statements = _parse_camt053_statements(content)
    if not statements:
        raise ValueError('CAMT.053: nema elementa Stmt')
    first = statements[0]
    return {
        'statement_number': first.statement_number,
        'statement_date': first.statement_date,
        'opening_balance': first.opening_balance,
        'closing_balance': first.closing_balance,
        'transactions': [
            {
                'transaction_date': tx.transaction_date,
                'value_date': tx.value_date,
                'amount': tx.amount,
                'transaction_type': tx.transaction_type,
                'description': tx.description,
                'reference': tx.reference,
                'counterparty_name': tx.counterparty_name,
                'counterparty_iban': tx.counterparty_iban,
                'external_id': tx.external_id,
            }
            for tx in first.transactions
        ],
    }


def _parse_date(value: str):
    if not value:
        return None
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(value.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(value: str) -> Decimal:
    if not value:
        return Decimal('0')
    cleaned = value.strip().replace('.', '').replace(',', '.') if ',' in value and value.count(',') == 1 else value
    cleaned = cleaned.replace(' ', '').replace('\xa0', '')
    return Decimal(cleaned)


def parse_bank_csv(content: str | bytes, delimiter: str = ';') -> dict:
    if isinstance(content, bytes):
        content = content.decode('utf-8-sig', errors='replace')

    reader = csv.DictReader(StringIO(content), delimiter=delimiter)
    transactions = []
    for row in reader:
        date_val = row.get('datum') or row.get('Datum') or row.get('date') or row.get('Booking date') or ''
        amount_val = row.get('iznos') or row.get('Iznos') or row.get('amount') or row.get('Amount') or '0'
        tx_type_raw = (row.get('tip') or row.get('Tip') or row.get('type') or 'credit').lower()
        tx_type = 'debit' if tx_type_raw in ('debit', 'd', 'terecenje', 'terćenje', 'out') else 'credit'
        amount = abs(_parse_amount(amount_val))
        if amount <= 0:
            continue
        transactions.append({
            'transaction_date': _parse_date(date_val) or datetime.now().date(),
            'value_date': _parse_date(row.get('datum_valute') or row.get('Value date') or ''),
            'amount': amount,
            'transaction_type': tx_type,
            'description': row.get('opis') or row.get('Opis') or row.get('description') or '',
            'reference': row.get('poziv') or row.get('Poziv na broj') or row.get('reference') or '',
            'counterparty_name': row.get('platitelj') or row.get('Platitelj') or row.get('counterparty') or '',
            'counterparty_iban': row.get('iban') or row.get('IBAN') or '',
        })

    return {
        'statement_number': f"CSV-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        'statement_date': transactions[0]['transaction_date'] if transactions else datetime.now().date(),
        'opening_balance': Decimal('0'),
        'closing_balance': Decimal('0'),
        'transactions': transactions,
    }
