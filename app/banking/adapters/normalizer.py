from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from banking.adapters.base import NormalizedTransaction


def to_bank_transactions(raw_items: list[dict]) -> list[NormalizedTransaction]:
    normalized: list[NormalizedTransaction] = []
    for item in raw_items:
        tx = normalize_transaction(item)
        if tx is not None:
            normalized.append(tx)
    return normalized


def normalize_transaction(item: dict) -> NormalizedTransaction | None:
    external_id = (
        item.get('transactionId')
        or item.get('entryReference')
        or item.get('endToEndId')
        or ''
    )
    if not external_id:
        return None

    amount_data = item.get('transactionAmount') or item.get('instructedAmount') or {}
    try:
        amount = Decimal(str(amount_data.get('amount', '0')))
    except (InvalidOperation, TypeError):
        return None
    if amount <= 0:
        return None

    currency = amount_data.get('currency', 'EUR')
    credit_debit = (item.get('creditDebitIndicator') or 'CRDT').upper()
    transaction_type = 'credit' if credit_debit in ('CRDT', 'CREDIT') else 'debit'

    booking_date = _parse_date(item.get('bookingDate') or item.get('valueDate'))
    value_date = _parse_date(item.get('valueDate'))
    if booking_date is None:
        return None

    remittance = item.get('remittanceInformationUnstructured')
    if isinstance(remittance, list):
        remittance = ' '.join(str(x) for x in remittance)
    reference = item.get('endToEndId') or item.get('creditorReferenceInformation') or ''
    if isinstance(reference, dict):
        reference = reference.get('reference', '')

    counterparty = item.get('creditorName') or item.get('debtorName') or ''
    counterparty_account = item.get('creditorAccount') or item.get('debtorAccount') or {}
    counterparty_iban = counterparty_account.get('iban', '') if isinstance(counterparty_account, dict) else ''

    return NormalizedTransaction(
        external_id=str(external_id),
        transaction_date=booking_date,
        value_date=value_date,
        amount=amount,
        currency=currency,
        transaction_type=transaction_type,
        description=str(remittance or ''),
        reference=str(reference or ''),
        counterparty_name=str(counterparty or ''),
        counterparty_iban=str(counterparty_iban or ''),
    )


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value)
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
