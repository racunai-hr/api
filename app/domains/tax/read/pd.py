"""PD (porez na dobit) iz glavne knjige.

Knjiga je u eurima. Iznos na obrascu za godinu prije eura je izvorni kunski
iznos povezane bankovne stavke. Prva prijava, godina nakon prve proknjižene
temeljnice, uvlači i prihode i rashode godine osnivanja jer za nju nema
zaseban PD. Bilanca (kapital, pozajmice, podizanje gotovine) ne ulazi.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Prefetch

from accounting.models import JournalEntry, JournalEntryLine
from domains.tax.read.pd_lines import PD_LINES, filed_body
from settings.models import CompanySettings

TWOPLACES = Decimal('0.01')
ZERO = Decimal('0.00')
RDG_TYPES = frozenset({'revenue', 'expense'})

REASON_MANY_LINES = 'Temeljnica ima više stavki prihoda ili rashoda.'
REASON_NOT_HRK = 'Povezana bankovna stavka nije u kunama.'
REASON_NO_BANK = 'Nema povezane bankovne stavke u kunama.'


def _q(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _money(value: Decimal) -> str:
    return f'{_q(value):.2f}'


def profit_tax_rate(year: int) -> Decimal:
    """Stopa poreza na dobit. Do 2016. uključivo 20%, kasnije 18%."""
    if year <= 2016:
        return Decimal('20.00')
    return Decimal('18.00')


def _posted_entries(tenant):
    return JournalEntry.all_objects.filter(
        tenant=tenant,
        status='posted',
        reversed_entry__isnull=True,
    )


def _first_book_year(tenant) -> int | None:
    first = (
        _posted_entries(tenant)
        .order_by('entry_date', 'entry_number')
        .values_list('entry_date', flat=True)
        .first()
    )
    return first.year if first is not None else None


def source_years(tenant, year: int) -> tuple[int, ...]:
    """Kalendarska godina, plus godina osnivanja kad je ovo prva prijava."""
    first = _first_book_year(tenant)
    if first is not None and year == first + 1:
        return (first, year)
    return (year,)


def _header(tenant) -> dict:
    company = (
        CompanySettings.all_objects.filter(tenant=tenant)
        .select_related('tax_office')
        .first()
    )
    if company is None:
        return {
            'company_name': '',
            'oib': '',
            'street': '',
            'house_number': '',
            'city': '',
            'tax_office_code': '',
        }
    office = company.tax_office
    return {
        'company_name': company.company_name,
        'oib': company.vat_number or '',
        'street': company.street or '',
        'house_number': company.house_number or '',
        'city': company.city or '',
        'tax_office_code': office.code if office else '',
    }


def _hrk_transaction(entry: JournalEntry):
    txs = list(entry.matched_bank_transactions.all())
    if len(txs) == 1 and (txs[0].currency or '').upper() == 'HRK':
        return txs[0]
    return None


def _missing_reason(entry: JournalEntry, rdg_count: int) -> str:
    if rdg_count != 1:
        return REASON_MANY_LINES
    txs = list(entry.matched_bank_transactions.all())
    if txs and (txs[0].currency or '').upper() != 'HRK':
        return REASON_NOT_HRK
    return REASON_NO_BANK


def _signed_hrk(kind: str, line: JournalEntryLine, magnitude: Decimal) -> Decimal:
    if kind == 'expense':
        return magnitude if line.debit_amount > 0 else -magnitude
    return magnitude if line.credit_amount > 0 else -magnitude


def _collect(tenant, year: int) -> tuple[list[dict], list[dict], int | None]:
    years = source_years(tenant, year)
    founding_year = years[0] if len(years) == 2 else None
    entries = (
        _posted_entries(tenant)
        .filter(entry_date__year__in=years)
        .prefetch_related(
            Prefetch(
                'lines',
                queryset=JournalEntryLine.objects.select_related('account__account_type'),
            ),
            'matched_bank_transactions',
        )
        .order_by('entry_date', 'entry_number')
    )

    included: list[dict] = []
    missing: list[dict] = []
    for entry in entries:
        rdg_lines = [
            line
            for line in entry.lines.all()
            if line.account.account_type.name in RDG_TYPES
            and (line.debit_amount > 0 or line.credit_amount > 0)
        ]
        if not rdg_lines:
            continue
        use_hrk = entry.entry_date.year < 2023
        bank_tx = _hrk_transaction(entry) if use_hrk and len(rdg_lines) == 1 else None
        if use_hrk and bank_tx is None:
            reason = _missing_reason(entry, len(rdg_lines))
            for line in rdg_lines:
                missing.append({
                    'entry_date': entry.entry_date.isoformat(),
                    'entry_number': entry.entry_number,
                    'account_code': line.account.account_code,
                    'account_name': line.account.account_name,
                    'reason': reason,
                })
            continue
        for line in rdg_lines:
            kind = line.account.account_type.name
            if use_hrk:
                amount = _signed_hrk(kind, line, bank_tx.amount)
            else:
                magnitude = line.debit_amount if line.debit_amount > 0 else line.credit_amount
                amount = _signed_hrk(kind, line, magnitude)
            included.append({
                'entry_date': entry.entry_date.isoformat(),
                'entry_number': entry.entry_number,
                'account_code': line.account.account_code,
                'account_name': line.account.account_name,
                'kind': kind,
                'amount': amount,
                'description': entry.description or '',
                'from_founding_year': (
                    founding_year is not None and entry.entry_date.year == founding_year
                ),
            })
    return included, missing, founding_year


def _amounts(year: int, included: list[dict]) -> dict[str, Decimal]:
    amounts = {code: ZERO for code, _label in PD_LINES}
    revenue = sum((item['amount'] for item in included if item['kind'] == 'revenue'), ZERO)
    expense = sum((item['amount'] for item in included if item['kind'] == 'expense'), ZERO)
    amounts['1'] = _q(revenue)
    amounts['2'] = _q(expense)
    if revenue >= expense:
        amounts['3'] = _q(revenue - expense)
        amounts['4'] = ZERO
    else:
        amounts['3'] = ZERO
        amounts['4'] = _q(expense - revenue)

    amounts['26'] = _q(sum((amounts[str(number)] for number in range(5, 26)), ZERO))
    amounts['34'] = _q(amounts['34.1'] + amounts['34.2'])
    amounts['35'] = _q(sum((amounts[str(number)] for number in range(27, 35)), ZERO))

    signed = _q((amounts['3'] - amounts['4']) + amounts['26'] - amounts['35'])
    if signed > 0:
        amounts['36'] = signed
        base = _q(signed - amounts['37'])
        amounts['38'] = base if base > 0 else ZERO
        amounts['42'] = amounts['38']
    elif signed < 0:
        amounts['39'] = signed
        carry = _q((-signed) - amounts['40'])
        amounts['41'] = carry if carry > 0 else ZERO

    amounts['43'] = profit_tax_rate(year)
    amounts['44'] = _q(amounts['42'] * amounts['43'] / Decimal('100'))
    amounts['45'] = _q(amounts['46'] + amounts['47'])
    amounts['48'] = _q(amounts['48.1'] + amounts['48.2'] + amounts['48.3'])
    amounts['49'] = _q(amounts['49.1'] + amounts['49.2'] + amounts['49.3'] + amounts['49.4'])
    amounts['50'] = _q(amounts['50.1'] + amounts['50.2'] + amounts['50.3'] + amounts['50.4'])
    amounts['51'] = _q(amounts['49'] + amounts['50'])
    amounts['52'] = _q(amounts['45'] + amounts['48'] + amounts['51'])
    amounts['53'] = _q(amounts['44'] - amounts['52'])
    amounts['55'] = _q(amounts['53'] - amounts['54'])
    difference = _q(amounts['55'] - amounts['56'])
    if difference > 0:
        amounts['57'] = difference
    elif difference < 0:
        amounts['58'] = _q(-difference)
    return amounts


def pd_year_dto(tenant, year: int) -> dict:
    included, missing, founding_year = _collect(tenant, year)
    amounts = _amounts(year, included)
    filed = filed_body(year)
    rows = []
    for code, label in PD_LINES:
        amount = _money(amounts[code])
        rows.append({
            'code': code,
            'label': label,
            'amount': amount,
            'filed': None if filed is None else filed[code],
        })
    matches_filed = None
    if filed is not None:
        matches_filed = not missing and all(row['amount'] == row['filed'] for row in rows)
    founding_items = [
        {
            'entry_date': item['entry_date'],
            'entry_number': item['entry_number'],
            'account_code': item['account_code'],
            'account_name': item['account_name'],
            'amount': _money(item['amount']),
            'description': item['description'],
        }
        for item in included
        if item['from_founding_year']
    ]
    return {
        'tax_year': year,
        'currency': 'HRK' if year < 2023 else 'EUR',
        'period_from': date(year, 1, 1).isoformat(),
        'period_to': date(year, 12, 31).isoformat(),
        'tax_rate': _money(profit_tax_rate(year)),
        'founding_year': founding_year,
        'header': _header(tenant),
        'rows': rows,
        'founding_items': founding_items,
        'missing_hrk': missing,
        'matches_filed': matches_filed,
    }
