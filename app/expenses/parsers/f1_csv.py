from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from expenses.models import ExpenseSource
from expenses.parsers.base import register_parser
from expenses.parsers.types import NormalizedExpenseRow, ParseResult, ParseRowError

_ZERO_WIDTH_RE = re.compile(r'[\u200b\ufeff\u200c\u200d]')
_DATE_RE = re.compile(r'^(\d{2})\.(\d{2})\.(\d{4})\.')
_PAYMENT_MAP = {
    'K': 'card',
    'G': 'cash',
    'T': 'transfer',
}


def _clean(value: str) -> str:
    return _ZERO_WIDTH_RE.sub('', value or '').strip()


def _parse_decimal(value: str) -> Decimal:
    cleaned = _clean(value).replace('.', '').replace(',', '.')
    if not cleaned:
        return Decimal('0')
    return Decimal(cleaned)


def _parse_date(value: str):
    cleaned = _clean(value)
    match = _DATE_RE.match(cleaned)
    if not match:
        raise ValueError(f'Neispravan datum: {value}')
    day, month, year = match.groups()
    return datetime(int(year), int(month), int(day)).date()


def _parse_metadata_line(line: str) -> tuple[str, str] | None:
    if ':' not in line:
        return None
    key, value = line.split(':', 1)
    return key.strip(), _clean(value)


@register_parser
class F1CsvParser:
    source = ExpenseSource.F1_CSV

    def parse(self, content: bytes | str, *, filename: str = '') -> ParseResult:
        if isinstance(content, bytes):
            text = content.decode('utf-8-sig')
        else:
            text = content

        lines = text.splitlines()
        file_metadata: dict = {'filename': filename}
        parse_errors: list[ParseRowError] = []
        rows: list[NormalizedExpenseRow] = []

        for line in lines[:3]:
            parsed = _parse_metadata_line(line)
            if not parsed:
                continue
            key, value = parsed
            if 'OIB poreznog obveznika' in key:
                file_metadata['taxpayer_oib'] = value
            elif key.startswith('Razdoblje'):
                file_metadata['csv_period'] = value
            elif 'Naziv poreznog obveznika' in key:
                file_metadata['taxpayer_name'] = value

        if len(lines) < 4:
            return ParseResult(rows=tuple(), file_metadata=file_metadata, parse_errors=tuple(parse_errors))

        reader = csv.reader(io.StringIO('\n'.join(lines[3:])), delimiter=';')
        header = next(reader, None)
        if not header:
            return ParseResult(rows=tuple(), file_metadata=file_metadata, parse_errors=tuple(parse_errors))

        for row_number, raw_row in enumerate(reader, start=5):
            if not any(_clean(cell) for cell in raw_row):
                continue
            try:
                rows.append(self._parse_row(raw_row, row_number))
            except (ValueError, InvalidOperation) as exc:
                parse_errors.append(ParseRowError(row_number=row_number, message=str(exc)))

        return ParseResult(rows=tuple(rows), file_metadata=file_metadata, parse_errors=tuple(parse_errors))

    def _parse_row(self, raw_row: list[str], row_number: int) -> NormalizedExpenseRow:
        cells = [_clean(cell) for cell in raw_row]
        while len(cells) < 17:
            cells.append('')

        receipt_no = cells[0]
        business_unit = cells[1]
        device_label = cells[2]
        jir = cells[3]
        supplier_oib = cells[4]
        issue_datetime = cells[5]
        fiscalization_datetime = cells[6]
        tax_0 = _parse_decimal(cells[8])
        tax_5 = _parse_decimal(cells[10])
        tax_13 = _parse_decimal(cells[12])
        tax_25 = _parse_decimal(cells[14])
        amount = _parse_decimal(cells[15])
        payment_code = cells[16].upper()

        if not jir:
            raise ValueError('Nedostaje JIR')
        if amount <= 0:
            raise ValueError('Neispravan iznos')

        receipt_number = f'{receipt_no}-{business_unit}-{device_label}'
        tax_amount = tax_0 + tax_5 + tax_13 + tax_25
        payment_method = _PAYMENT_MAP.get(payment_code, 'other')

        raw_payload = {
            'business_unit': business_unit,
            'device_label': device_label,
            'fiscalization_datetime': fiscalization_datetime,
            'tax_breakdown': {
                'base_0': cells[7],
                'tax_0': cells[8],
                'base_5': cells[9],
                'tax_5': cells[10],
                'base_13': cells[11],
                'tax_13': cells[12],
                'base_25': cells[13],
                'tax_25': cells[14],
            },
            'payment_code': payment_code,
        }

        return NormalizedExpenseRow(
            row_number=row_number,
            source=ExpenseSource.F1_CSV,
            external_id=jir,
            supplier_oib=supplier_oib,
            receipt_number=receipt_number,
            expense_date=_parse_date(issue_datetime),
            amount=amount,
            tax_amount=tax_amount,
            currency='EUR',
            payment_method=payment_method,
            description=f'F1 ulazni račun {receipt_number}',
            raw_payload=raw_payload,
            jir=jir,
            status='approved',
        )
