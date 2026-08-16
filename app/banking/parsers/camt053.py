from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal

from banking.parsers.types import ParsedBankStatement, ParsedBankTransaction


def parse_camt053(content: bytes | str) -> list[ParsedBankStatement]:
    if isinstance(content, bytes):
        content = content.decode('utf-8', errors='replace')

    root = ET.fromstring(content)
    ns_uri = root.tag.split('}')[0].strip('{') if '}' in root.tag else ''
    ns = {'n': ns_uri} if ns_uri else {}

    stmt_elements = root.findall('.//n:Stmt', ns) if ns else root.findall('.//Stmt')
    if not stmt_elements:
        raise ValueError('CAMT.053: nema elementa Stmt')

    return [_parse_statement(stmt_el, ns) for stmt_el in stmt_elements]


def _parse_statement(stmt_el, ns: dict) -> ParsedBankStatement:
    statement_number = _find_text(stmt_el, 'Id', ns) or 'CAMT'
    statement_date = (
        _parse_date(_find_text(stmt_el, 'FrToDt/ToDtTm', ns))
        or datetime.now().date()
    )
    iban = _find_text(stmt_el, 'Acct/Id/IBAN', ns)
    currency = _find_text(stmt_el, 'Acct/Ccy', ns) or 'EUR'

    opening = Decimal('0')
    closing = Decimal('0')
    for bal in _findall(stmt_el, 'Bal', ns):
        bal_type = _find_text(bal, 'Tp/CdOrPrtry/Cd', ns)
        amt_el = _find(bal, 'Amt', ns)
        amount = Decimal(amt_el.text) if amt_el is not None and amt_el.text else Decimal('0')
        if bal_type in ('OPBD', 'PRCD'):
            opening = amount
        if bal_type in ('CLBD', 'CLAV'):
            closing = amount

    transactions = tuple(
        _parse_entry(
            entry,
            ns,
            statement_number=statement_number,
            currency=currency,
            statement_date=statement_date,
        )
        for entry in _findall(stmt_el, 'Ntry', ns)
    )

    return ParsedBankStatement(
        iban=iban,
        currency=currency,
        statement_number=statement_number,
        statement_date=statement_date,
        opening_balance=opening,
        closing_balance=closing,
        transactions=transactions,
    )


def _parse_entry(entry, ns, *, statement_number: str, currency: str, statement_date):
    amt_el = _find(entry, 'Amt', ns)
    amount = Decimal(amt_el.text) if amt_el is not None and amt_el.text else Decimal('0')
    credit_debit = _find_text(entry, 'CdtDbtInd', ns)
    if not credit_debit and amt_el is not None:
        credit_debit = amt_el.get('CdtDbtInd') or 'CRDT'
    tx_type = 'credit' if credit_debit == 'CRDT' else 'debit'

    external_id = _find_text(entry, 'AcctSvcrRef', ns)
    reference = ''
    counterparty_name = ''
    counterparty_iban = ''
    structured_description = ''

    details = _find(entry, 'NtryDtls/TxDtls', ns)
    if details is not None:
        if not external_id:
            external_id = _find_text(details, 'Refs/AcctSvcrRef', ns)
        reference = (
            _find_text(details, 'RmtInf/Strd/CdtrRefInf/Ref', ns)
            or _find_text(details, 'RmtInf/Ustrd', ns)
        )
        structured_description = _find_text(details, 'RmtInf/Strd/AddtlRmtInf', ns)
        counterparty_name = (
            _find_text(details, 'RltdPties/Dbtr/Nm', ns)
            or _find_text(details, 'RltdPties/Cdtr/Nm', ns)
        )
        counterparty_iban = (
            _find_text(details, 'RltdPties/DbtrAcct/Id/IBAN', ns)
            or _find_text(details, 'RltdPties/CdtrAcct/Id/IBAN', ns)
        )

    tx_date = _parse_date(_find_text(entry, 'BookgDt/Dt', ns)) or statement_date
    value_date = _parse_date(_find_text(entry, 'ValDt/Dt', ns))
    description = structured_description or _find_text(entry, 'AddtlNtryInf', ns)

    used_fallback = False
    if not external_id:
        used_fallback = True
        external_id = _fallback_external_id(
            statement_number=statement_number,
            currency=currency,
            tx_date=tx_date,
            amount=abs(amount),
            tx_type=tx_type,
            counterparty_iban=counterparty_iban,
            reference=reference,
        )

    return ParsedBankTransaction(
        external_id=external_id,
        used_fallback_external_id=used_fallback,
        transaction_date=tx_date,
        value_date=value_date,
        amount=abs(amount),
        transaction_type=tx_type,
        description=description,
        reference=reference,
        counterparty_name=counterparty_name,
        counterparty_iban=counterparty_iban,
    )


def _fallback_external_id(
    *,
    statement_number: str,
    currency: str,
    tx_date,
    amount: Decimal,
    tx_type: str,
    counterparty_iban: str,
    reference: str,
) -> str:
    payload = '|'.join([
        statement_number,
        currency,
        tx_date.isoformat(),
        str(amount),
        tx_type,
        counterparty_iban,
        reference,
    ])
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _parse_date(value: str):
    if not value:
        return None
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(value.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def _find(node, path: str, ns: dict):
    return node.find(_ns_path(path, ns), ns if ns else None)


def _findall(node, path: str, ns: dict):
    return node.findall(_ns_path(path, ns), ns if ns else None)


def _ns_path(path: str, ns: dict) -> str:
    if not ns:
        return path
    return '/'.join(f'n:{segment}' for segment in path.split('/'))


def _find_text(node, path: str, ns: dict, default: str = '') -> str:
    el = _find(node, path, ns)
    return el.text.strip() if el is not None and el.text else default
