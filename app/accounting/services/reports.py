"""Izvještaji i zatvaranje fiskalnog razdoblja."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.db.models import Sum
from django.utils import timezone

from accounting.reporting.query import ReportMode, journal_report_items, reporting_lines_qs
from accounting.models import ChartOfAccounts, FiscalPeriod
from domains.finance.services.aging import (
    AGING_BUCKET_KEYS,
    AGING_BUCKET_LABELS,
    aging_bucket_for_days,
    get_partner_aging,
    partner_aging,
    subledger_open_items,
)

RRIF_CLASS_LABELS = {
    '0': 'Dugotrajna imovina',
    '1': 'Novac i kratkotrajna imovina',
    '2': 'Obveze',
    '3': 'Imovina (ostalo)',
    '4': 'Poslovni rashodi',
    '5': 'Financijski rashodi',
    '6': 'Vanbilančna evidencija',
    '7': 'Prihodi i troškovi (7)',
    '8': 'Kapital',
    '9': 'Rezultat poslovanja',
}

BILANCA_AKTIVA_CLASSES = frozenset({'0', '1', '3'})
BILANCA_PASIVA_CLASSES = frozenset({'2', '8', '9'})
RDG_EXPENSE_CLASSES = frozenset({'4', '5'})
RDG_CLASS_SEVEN = '7'


def trial_balance(tenant, year: int, month: int) -> list[dict]:
    return _trial_balance_for_period(tenant, year, month, cumulative=False)


def trial_balance_cumulative(tenant, year: int, month: int) -> list[dict]:
    return _trial_balance_for_period(tenant, year, month, cumulative=True)


def _trial_balance_for_period(tenant, year: int, month: int, *, cumulative: bool) -> list[dict]:
    lines = reporting_lines_qs(
        tenant,
        year=year,
        month=month,
        cumulative=cumulative,
        mode=ReportMode.NET,
    ).values('account_id').annotate(
        debit=Sum('debit_amount'),
        credit=Sum('credit_amount'),
    )

    account_ids = [line['account_id'] for line in lines]
    accounts = {
        a.id: a
        for a in ChartOfAccounts.all_objects.filter(tenant=tenant, id__in=account_ids)
    }

    result = []
    for line in lines:
        account = accounts.get(line['account_id'])
        if not account:
            continue
        debit = line['debit'] or Decimal('0')
        credit = line['credit'] or Decimal('0')
        result.append({
            'account_code': account.account_code,
            'account_name': account.account_name,
            'account_class': account.account_class,
            'debit': debit,
            'credit': credit,
            'balance': debit - credit,
        })
    return sorted(result, key=lambda x: x['account_code'])


def _signed_balance(account_class: str, account_code: str, raw_balance: Decimal) -> Decimal:
    """Pretvori knjigovodstveni saldo u iznos za Bilancu/RDG."""
    if account_class in BILANCA_PASIVA_CLASSES:
        return -raw_balance
    if account_class == RDG_CLASS_SEVEN:
        code = account_code.lstrip('0') or account_code
        if code.startswith(('75', '76', '77', '78', '79')):
            return -raw_balance
        return raw_balance
    return raw_balance


def balance_sheet(tenant, year: int, month: int) -> dict:
    """Formalna Bilanca — kumulativno stanje do kraja mjeseca."""
    rows = trial_balance_cumulative(tenant, year, month)
    aktiva = []
    pasiva = []
    vanbilancno = []

    for row in rows:
        ac = row['account_class']
        if not ac or ac == '6':
            if ac == '6':
                vanbilancno.append({**row, 'amount': _signed_balance(ac, row['account_code'], row['balance'])})
            continue
        amount = _signed_balance(ac, row['account_code'], row['balance'])
        if amount == 0:
            continue
        entry = {**row, 'amount': amount}
        if ac in BILANCA_AKTIVA_CLASSES:
            aktiva.append(entry)
        elif ac in BILANCA_PASIVA_CLASSES:
            pasiva.append(entry)

    total_aktiva = sum(r['amount'] for r in aktiva)
    total_pasiva = sum(r['amount'] for r in pasiva)

    aktiva_by_class = _group_by_class(aktiva)
    pasiva_by_class = _group_by_class(pasiva)

    return {
        'year': year,
        'month': month,
        'aktiva': aktiva,
        'pasiva': pasiva,
        'vanbilancno': vanbilancno,
        'aktiva_by_class': aktiva_by_class,
        'pasiva_by_class': pasiva_by_class,
        'total_aktiva': total_aktiva,
        'total_pasiva': total_pasiva,
    }


def income_statement(tenant, year: int, month: int) -> dict:
    """Formalni RDG — promet u razdoblju (siječanj–odabrani mjesec)."""
    rows = trial_balance_cumulative(tenant, year, month)
    prihodi = []
    rashodi = []

    for row in rows:
        ac = row['account_class']
        if ac in RDG_EXPENSE_CLASSES:
            amount = row['balance']
            if amount != 0:
                rashodi.append({**row, 'amount': amount})
        elif ac == RDG_CLASS_SEVEN:
            amount = _signed_balance(ac, row['account_code'], row['balance'])
            if amount == 0:
                continue
            if amount > 0:
                rashodi.append({**row, 'amount': amount})
            else:
                prihodi.append({**row, 'amount': -amount})

    total_prihodi = sum(r['amount'] for r in prihodi)
    total_rashodi = sum(r['amount'] for r in rashodi)
    rezultat = total_prihodi - total_rashodi

    return {
        'year': year,
        'month': month,
        'prihodi': prihodi,
        'rashodi': rashodi,
        'prihodi_by_class': _group_by_class(prihodi),
        'rashodi_by_class': _group_by_class(rashodi),
        'total_prihodi': total_prihodi,
        'total_rashodi': total_rashodi,
        'rezultat': rezultat,
    }


def _group_by_class(rows: list[dict]) -> list[dict]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        ac = row.get('account_class') or '?'
        totals[ac] += row['amount']
    return [
        {
            'account_class': ac,
            'label': RRIF_CLASS_LABELS.get(ac, ac),
            'amount': totals[ac],
        }
        for ac in sorted(totals.keys())
    ]


def journal_report(tenant, year: int, month: int):
    """Audit dnevnik — uključuje aktivne, stornirane originale i storno temeljnice."""
    return journal_report_items(tenant, year, month)


def close_fiscal_period(period, user) -> FiscalPeriod:
    if period.status == 'closed':
        return period
    from django.utils import timezone

    period.status = 'closed'
    period.closed_at = timezone.now()
    period.closed_by = user
    period.save(update_fields=['status', 'closed_at', 'closed_by'])
    return period


def export_trial_balance_xlsx(tenant, year: int, month: int) -> bytes:
    import xlsxwriter

    rows = trial_balance(tenant, year, month)
    buffer = BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {'in_memory': True})
    ws = workbook.add_worksheet('Bruto bilanca')
    header = workbook.add_format({'bold': True})
    money = workbook.add_format({'num_format': '#,##0.00'})

    ws.write(0, 0, f"Bruto bilanca {month:02d}/{year}", header)
    for col, title in enumerate(['Konto', 'Naziv', 'Duguje', 'Potražuje', 'Saldo']):
        ws.write(2, col, title, header)

    row = 3
    totals = defaultdict(Decimal)
    for item in rows:
        ws.write(row, 0, item['account_code'])
        ws.write(row, 1, item['account_name'])
        ws.write(row, 2, float(item['debit']), money)
        ws.write(row, 3, float(item['credit']), money)
        ws.write(row, 4, float(item['balance']), money)
        totals['debit'] += item['debit']
        totals['credit'] += item['credit']
        row += 1

    ws.write(row + 1, 1, 'UKUPNO', header)
    ws.write(row + 1, 2, float(totals['debit']), money)
    ws.write(row + 1, 3, float(totals['credit']), money)

    workbook.close()
    buffer.seek(0)
    return buffer.getvalue()


def export_bilanca_xlsx(tenant, year: int, month: int) -> bytes:
    import xlsxwriter

    data = balance_sheet(tenant, year, month)
    buffer = BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {'in_memory': True})
    header = workbook.add_format({'bold': True})
    money = workbook.add_format({'num_format': '#,##0.00'})

    ws_a = workbook.add_worksheet('Aktiva')
    ws_a.write(0, 0, f"Bilanca — Aktiva (kumulativno do {month:02d}/{year})", header)
    _write_bilanca_side(ws_a, data['aktiva'], data['aktiva_by_class'], data['total_aktiva'], header, money)

    ws_p = workbook.add_worksheet('Pasiva')
    ws_p.write(0, 0, f"Bilanca — Pasiva (kumulativno do {month:02d}/{year})", header)
    _write_bilanca_side(ws_p, data['pasiva'], data['pasiva_by_class'], data['total_pasiva'], header, money)

    if data['vanbilancno']:
        ws_v = workbook.add_worksheet('Vanbilančno')
        ws_v.write(0, 0, 'Vanbilančna evidencija (klasa 6)', header)
        _write_amount_rows(ws_v, data['vanbilancno'], header, money, start_row=2)

    workbook.close()
    buffer.seek(0)
    return buffer.getvalue()


def export_rdg_xlsx(tenant, year: int, month: int) -> bytes:
    import xlsxwriter

    data = income_statement(tenant, year, month)
    buffer = BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {'in_memory': True})
    header = workbook.add_format({'bold': True})
    money = workbook.add_format({'num_format': '#,##0.00'})

    ws = workbook.add_worksheet('RDG')
    ws.write(0, 0, f"Račun dobiti i gubitka (siječanj–{month:02d}/{year})", header)

    row = 2
    ws.write(row, 0, 'PRIHODI', header)
    row += 1
    row = _write_amount_rows(ws, data['prihodi'], header, money, start_row=row)
    ws.write(row + 1, 1, 'Ukupno prihodi', header)
    ws.write(row + 1, 3, float(data['total_prihodi']), money)
    row += 3

    ws.write(row, 0, 'RASHODI', header)
    row += 1
    row = _write_amount_rows(ws, data['rashodi'], header, money, start_row=row)
    ws.write(row + 1, 1, 'Ukupno rashodi', header)
    ws.write(row + 1, 3, float(data['total_rashodi']), money)
    row += 3

    ws.write(row, 0, 'REZULTAT', header)
    ws.write(row, 1, 'Dobit / gubitak', header)
    ws.write(row, 3, float(data['rezultat']), money)

    workbook.close()
    buffer.seek(0)
    return buffer.getvalue()


def _write_bilanca_side(ws, rows, by_class, total, header, money, start_row=2):
    ws.write(start_row, 0, 'Klasa', header)
    ws.write(start_row, 1, 'Opis', header)
    ws.write(start_row, 3, 'Iznos', header)
    row = start_row + 1
    for item in by_class:
        ws.write(row, 0, item['account_class'])
        ws.write(row, 1, item['label'])
        ws.write(row, 3, float(item['amount']), money)
        row += 1
    row += 1
    ws.write(row, 1, 'UKUPNO', header)
    ws.write(row, 3, float(total), money)
    row += 2
    ws.write(row, 0, 'Detalj po kontu', header)
    row += 1
    ws.write(row, 0, 'Konto', header)
    ws.write(row, 1, 'Naziv', header)
    ws.write(row, 3, 'Iznos', header)
    row += 1
    _write_amount_rows(ws, rows, header, money, start_row=row)
    return row


def _write_amount_rows(ws, rows, header, money, start_row=3):
    row = start_row
    for item in rows:
        ws.write(row, 0, item.get('account_code', ''))
        ws.write(row, 1, item.get('account_name', ''))
        ws.write(row, 3, float(item['amount']), money)
        row += 1
    return row


def export_subledger_aging_xlsx(
    tenant,
    *,
    direction: str | None = None,
    as_of_date: date | None = None,
) -> bytes:
    import xlsxwriter

    if as_of_date is None:
        as_of_date = timezone.localdate()

    aging = partner_aging(tenant, direction=direction, as_of_date=as_of_date)
    open_items = subledger_open_items(tenant, direction=direction, as_of_date=as_of_date)

    buffer = BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {'in_memory': True})
    header = workbook.add_format({'bold': True})
    money = workbook.add_format({'num_format': '#,##0.00'})
    date_fmt = workbook.add_format({'num_format': 'dd.mm.yyyy'})

    direction_label = {
        'receivable': 'Potraživanja (AR)',
        'payable': 'Obveze (AP)',
        None: 'AR + AP',
    }[direction]

    ws_summary = workbook.add_worksheet('Aging')
    ws_summary.write(
        0,
        0,
        f'Saldakonti aging — {direction_label} na dan {as_of_date:%d.%m.%Y}',
        header,
    )
    summary_headers = ['Partner', 'Smjer'] + [AGING_BUCKET_LABELS[k] for k in AGING_BUCKET_KEYS] + ['Ukupno']
    for col, title in enumerate(summary_headers):
        ws_summary.write(2, col, title, header)

    row = 3
    for partner_row in aging['partners']:
        ws_summary.write(row, 0, partner_row['partner_name'])
        ws_summary.write(row, 1, partner_row['direction_label'])
        for col, key in enumerate(AGING_BUCKET_KEYS, start=2):
            ws_summary.write(row, col, float(partner_row['buckets'][key]), money)
        ws_summary.write(row, len(AGING_BUCKET_KEYS) + 2, float(partner_row['total']), money)
        row += 1

    ws_summary.write(row + 1, 0, 'UKUPNO', header)
    for col, key in enumerate(AGING_BUCKET_KEYS, start=2):
        ws_summary.write(row + 1, col, float(aging['totals'][key]), money)
    ws_summary.write(row + 1, len(AGING_BUCKET_KEYS) + 2, float(aging['totals']['total']), money)

    ws_detail = workbook.add_worksheet('Otvorene stavke')
    ws_detail.write(0, 0, f'Otvorene stavke — {direction_label}', header)
    detail_headers = [
        'Partner',
        'Smjer',
        'Dokument',
        'Dospijeće',
        'Dana kašnjenja',
        'Bucket',
        'Izvorni iznos',
        'Otvoreni iznos',
        'Status',
    ]
    for col, title in enumerate(detail_headers):
        ws_detail.write(2, col, title, header)

    row = 3
    for item in open_items:
        ws_detail.write(row, 0, item['partner_name'])
        ws_detail.write(row, 1, item['direction_label'])
        ws_detail.write(row, 2, item['source_label'])
        ws_detail.write(row, 3, item['due_date'], date_fmt)
        ws_detail.write(row, 4, item['days_overdue'])
        ws_detail.write(row, 5, AGING_BUCKET_LABELS[item['aging_bucket']])
        ws_detail.write(row, 6, float(item['original_amount']), money)
        ws_detail.write(row, 7, float(item['open_amount']), money)
        ws_detail.write(row, 8, item['status'])
        row += 1

    workbook.close()
    buffer.seek(0)
    return buffer.getvalue()
