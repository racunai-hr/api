"""Persist OCR line_items as ExpenseLine facts. Does not pick GL accounts."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from domains.purchasing.services.matching import parse_money
from expenses.models import Expense, ExpenseLine

TWOPLACES = Decimal('0.01')
ZERO = Decimal('0.00')
LINE_ALLOCATION_MISMATCH = 'Zbroj stavki ne odgovara iznosima računa.'
MIXED_LINE_ACCOUNTS = 'Sve stavke moraju imati konto, ili nijedna.'


class LineAllocationMismatch(ValueError):
    code = 'line_allocation_mismatch'
    detail = LINE_ALLOCATION_MISMATCH


def _q(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _line_amount(item: dict) -> Decimal | None:
    amount = parse_money(item.get('amount'))
    if amount is not None:
        return _q(amount)
    quantity = parse_money(item.get('quantity'))
    unit_price = parse_money(item.get('unit_price'))
    if quantity is None or unit_price is None:
        return None
    return _q(quantity * unit_price)


def _header_net_and_tax(expense: Expense) -> tuple[Decimal, Decimal]:
    tax = _q(Decimal(expense.tax_amount or 0))
    net = _q(Decimal(expense.amount) - tax)
    if net < ZERO:
        net = ZERO
    return net, tax


def header_net_and_tax_from_payload(payload: dict | None) -> tuple[Decimal, Decimal] | None:
    data = payload if isinstance(payload, dict) else {}
    tax = _q(parse_money(data.get('tax_amount')) or ZERO)
    net = parse_money(data.get('net_amount'))
    total = parse_money(data.get('total_amount'))
    if net is None and total is not None:
        net = total - tax
    if net is None:
        return None
    net = _q(net)
    if net < ZERO:
        net = ZERO
    return net, tax


def _scale_to_total(parts: list[Decimal], total: Decimal) -> list[Decimal]:
    raw = sum(parts, ZERO)
    if total == ZERO or not parts:
        return [ZERO] * len(parts)
    if raw == ZERO:
        zeros = [ZERO] * (len(parts) - 1)
        return zeros + [total]
    scaled = [_q(part * total / raw) for part in parts]
    drift = total - sum(scaled, ZERO)
    scaled[-1] = _q(scaled[-1] + drift)
    return scaled


def parsed_ocr_lines_from_amounts(
    payload: dict | None,
    *,
    header_net: Decimal,
    header_tax: Decimal,
) -> list[dict]:
    """Turn OCR line_items into net/vat/gross rows that sum to the header."""
    items = payload.get('line_items') if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []

    extracted: list[tuple[str, Decimal]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        description = str(item.get('description') or '').strip()
        if not description:
            continue
        amount = _line_amount(item)
        if amount is None or amount < ZERO:
            continue
        extracted.append((description, amount))
    if not extracted:
        return []

    header_net = _q(header_net)
    header_tax = _q(header_tax)
    amounts = [row[1] for row in extracted]
    nets = _scale_to_total(amounts, header_net)
    vats = _scale_to_total(nets, header_tax)

    rows = []
    for position, ((description, _), net, vat) in enumerate(zip(extracted, nets, vats), start=1):
        net = _q(net)
        vat = _q(vat)
        if net < ZERO:
            net = ZERO
        if vat < ZERO:
            vat = ZERO
        rows.append(
            {
                'position': position,
                'description': description,
                'net_amount': net,
                'vat_amount': vat,
                'gross_amount': _q(net + vat),
            }
        )
    return rows


def parsed_ocr_lines(expense: Expense, payload: dict | None) -> list[dict]:
    header_net, header_tax = _header_net_and_tax(expense)
    return parsed_ocr_lines_from_amounts(payload, header_net=header_net, header_tax=header_tax)


def allocated_ocr_line_views(payload: dict | None) -> list[dict]:
    amounts = header_net_and_tax_from_payload(payload)
    if amounts is None:
        return []
    rows = parsed_ocr_lines_from_amounts(payload, header_net=amounts[0], header_tax=amounts[1])
    return [
        {
            'position': row['position'],
            'description': row['description'],
            'net_amount': f"{row['net_amount']:.2f}",
            'vat_amount': f"{row['vat_amount']:.2f}",
            'gross_amount': f"{row['gross_amount']:.2f}",
        }
        for row in rows
    ]


def allocated_rows_match_header(
    rows: list[dict],
    *,
    header_net: Decimal,
    header_tax: Decimal,
) -> bool:
    if not rows:
        return True
    line_net = sum((row['net_amount'] for row in rows), ZERO)
    line_vat = sum((row['vat_amount'] for row in rows), ZERO)
    line_gross = sum((row['gross_amount'] for row in rows), ZERO)
    header_net = _q(header_net)
    header_tax = _q(header_tax)
    header_gross = _q(header_net + header_tax)
    return line_net == header_net and line_vat == header_tax and line_gross == header_gross


def persist_extracted_lines(
    *,
    expense: Expense,
    payload: dict | None,
    posting_accounts: dict | None = None,
) -> list[ExpenseLine]:
    """Write OCR line_items onto a new expense. No-op when there are no usable items."""
    rows = parsed_ocr_lines(expense, payload)
    header_net, header_tax = _header_net_and_tax(expense)
    if rows and not allocated_rows_match_header(rows, header_net=header_net, header_tax=header_tax):
        raise LineAllocationMismatch()
    accounts = posting_accounts or {}
    created: list[ExpenseLine] = []
    for row in rows:
        created.append(
            ExpenseLine.all_objects.create(
                expense=expense,
                position=row['position'],
                description=row['description'],
                net_amount=row['net_amount'],
                vat_amount=row['vat_amount'],
                gross_amount=row['gross_amount'],
                posting_account=accounts.get(row['position']),
            )
        )
    return created
