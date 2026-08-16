"""PDV knjige i izvještaji."""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from accounting.models import JournalEntry, JournalEntryLine, VATLedgerEntry, VATPeriod
from accounting.services.tax_forms.pdv.mapping import (
    PDV_MAPPING,
    DOMESTIC_RC_OBVEZA_ACCOUNTS,
    DOMESTIC_RC_PRETPOREZ_ACCOUNTS,
    EU_GOODS_ASSET_ACCOUNTS,
    EU_GOODS_OBVEZA_ACCOUNTS,
    EU_GOODS_PRETPOREZ_ACCOUNTS,
    EU_SERVICES_OBVEZA_ACCOUNTS,
    EU_SERVICES_PRETPOREZ_ACCOUNTS,
    IOSS_PRETPOREZ_ACCOUNTS,
    RC_INPUT_BOXES,
    RC_OUTPUT_BOXES,
    VIII1_SUB_BOXES,
    VIII1_VAT_AGGREGATE_BOXES,
    _JOURNAL_INPUT_ACCOUNTS,
    _JOURNAL_OUTPUT_ACCOUNT_TO_BOX,
    invoice_eu_outbound_box,
    invoice_rate_to_box,
    is_eu_outbound_invoice_line,
    is_eu_supplier,
    journal_line_to_box,
    journal_line_to_viii1_box,
    journal_line_vat_rate,
    normalize_vat_rate,
    partner_eu_vat_id,
    pretporez_base_from_vat,
    rc_output_box_for_input,
    rc_vat_from_base,
    viii1_box_amounts,
)
from accounting.services.tax_forms.pdv.supply_procedure import VatSupplyProcedure, invoice_procedure_to_box

_LEDGER_OUTPUT = VATLedgerEntry.LEDGER_I_RA
_LEDGER_INPUT = VATLedgerEntry.LEDGER_U_RA
_VIII1_TOTAL_DOCUMENT = 'VIII1-TOTAL'
_RC_SKIP_ACCOUNTS = (
    EU_GOODS_ASSET_ACCOUNTS
    | EU_GOODS_PRETPOREZ_ACCOUNTS
    | EU_GOODS_OBVEZA_ACCOUNTS
    | EU_SERVICES_PRETPOREZ_ACCOUNTS
    | EU_SERVICES_OBVEZA_ACCOUNTS
    | DOMESTIC_RC_PRETPOREZ_ACCOUNTS
    | DOMESTIC_RC_OBVEZA_ACCOUNTS
    | IOSS_PRETPOREZ_ACCOUNTS
)


def _invoice_item_box(*, item, partner, has_service_date: bool) -> str | None:
    procedure = getattr(item, 'vat_procedure', VatSupplyProcedure.STANDARD) or VatSupplyProcedure.STANDARD
    oss_box = invoice_procedure_to_box(procedure)
    if oss_box is not None:
        return oss_box
    rate = item.tax_rate or Decimal('0')
    if is_eu_outbound_invoice_line(partner=partner, tax_rate=rate):
        return invoice_eu_outbound_box(has_service_date=has_service_date)
    return invoice_rate_to_box(rate)


def get_or_create_vat_period(tenant, year: int, month: int) -> VATPeriod:
    period, _ = VATPeriod.all_objects.get_or_create(
        tenant=tenant,
        year=year,
        month=month,
    )
    return period


def _create_ledger_entry(
    *,
    tenant,
    period,
    rule,
    entry_date,
    document_number,
    partner_name,
    partner_oib,
    base_amount,
    vat_rate,
    vat_amount,
    source_content_type,
    source_object_id,
) -> VATLedgerEntry:
    return VATLedgerEntry.all_objects.create(
        tenant=tenant,
        vat_period=period,
        ledger_type=rule.ledger_type,
        entry_date=entry_date,
        document_number=document_number,
        partner_name=partner_name,
        partner_oib=partner_oib,
        base_amount=base_amount,
        vat_rate=vat_rate,
        vat_amount=vat_amount,
        rrif_vat_account=rule.rrif_vat_account,
        vat_box=rule.vat_box,
        entry_category=rule.entry_category,
        is_manual=False,
        source_content_type=source_content_type,
        source_object_id=source_object_id,
    )


def _period_last_day(period) -> date:
    last_day = calendar.monthrange(period.year, period.month)[1]
    return date(period.year, period.month, last_day)


def _sync_viii1_box_610(*, tenant, period) -> int:
    """Rebuild auto 610 total from VIII.1 sub-box ledger entries."""
    VATLedgerEntry.all_objects.filter(
        tenant=tenant,
        vat_period=period,
        vat_box='610',
        is_manual=False,
        document_number=_VIII1_TOTAL_DOCUMENT,
    ).delete()

    total = Decimal('0.00')
    for row in VATLedgerEntry.all_objects.filter(
        tenant=tenant,
        vat_period=period,
        vat_box__in=VIII1_SUB_BOXES,
    ):
        if row.vat_box in VIII1_VAT_AGGREGATE_BOXES:
            total += row.vat_amount
        else:
            total += row.base_amount

    if total == 0:
        return 0

    period_ct = ContentType.objects.get_for_model(VATPeriod)
    rule = PDV_MAPPING['610']
    VATLedgerEntry.all_objects.create(
        tenant=tenant,
        vat_period=period,
        ledger_type=rule.ledger_type,
        entry_date=_period_last_day(period),
        document_number=_VIII1_TOTAL_DOCUMENT,
        partner_name='',
        partner_oib='',
        base_amount=total,
        vat_rate=Decimal('0.00'),
        vat_amount=Decimal('0.00'),
        rrif_vat_account=rule.rrif_vat_account,
        vat_box=rule.vat_box,
        entry_category=rule.entry_category,
        is_manual=False,
        source_content_type=period_ct,
        source_object_id=period.pk,
    )
    return 1


def _matched_rc_base(*, tenant, period, output_box: str, vat_amount: Decimal, rate: Decimal) -> Decimal | None:
    candidate_bases = [
        row['base_amount']
        for row in VATLedgerEntry.all_objects.filter(
            tenant=tenant,
            vat_period=period,
            vat_box=output_box,
        )
        .exclude(base_amount=0)
        .values('base_amount')
    ]
    matched = [
        base
        for base in candidate_bases
        if rc_vat_from_base(base, rate) == vat_amount
    ]
    if len(matched) == 1:
        return matched[0]
    if not matched and candidate_bases:
        total_base = sum(candidate_bases, Decimal('0.00'))
        if rc_vat_from_base(total_base, rate) == vat_amount:
            return total_base
    return None


def _journal_rc_line_amounts(
    *,
    tenant,
    period,
    box_code: str,
    account_code: str,
    debit_amount: Decimal,
    credit_amount: Decimal,
) -> tuple[Decimal, Decimal, Decimal] | None:
    rate = journal_line_vat_rate(account_code)
    if box_code in RC_INPUT_BOXES and debit_amount > 0:
        vat_amount = debit_amount
        base_amount = pretporez_base_from_vat(vat_amount, rate)
        output_box = rc_output_box_for_input(box_code)
        if output_box is not None:
            matched_base = _matched_rc_base(
                tenant=tenant,
                period=period,
                output_box=output_box,
                vat_amount=vat_amount,
                rate=rate,
            )
            if matched_base is not None:
                base_amount = matched_base
        return base_amount, rate, vat_amount
    if box_code in RC_OUTPUT_BOXES and credit_amount > 0:
        vat_amount = credit_amount
        if box_code == '207':
            base_amount = Decimal('0.00')
        else:
            base_amount = pretporez_base_from_vat(vat_amount, rate)
        return base_amount, rate, vat_amount
    return None


@transaction.atomic
def generate_vat_ledger(tenant, year: int, month: int, *, replace: bool = False) -> tuple[int, int]:
    from expenses.models import Expense
    from invoices.models import Invoice

    period = get_or_create_vat_period(tenant, year, month)
    if replace:
        VATLedgerEntry.all_objects.filter(
            tenant=tenant,
            vat_period=period,
            is_manual=False,
        ).delete()

    created = 0

    invoice_ct = ContentType.objects.get_for_model(Invoice)
    for invoice in Invoice.all_objects.filter(
        tenant=tenant,
        issue_date__year=year,
        issue_date__month=month,
        status__in=['sent', 'paid', 'overdue'],
    ):
        if VATLedgerEntry.all_objects.filter(
            tenant=tenant,
            vat_period=period,
            source_content_type=invoice_ct,
            source_object_id=invoice.pk,
        ).exists():
            continue

        partner = invoice.company_to
        partner_vat = partner_eu_vat_id(partner) if partner else ''
        items = list(invoice.items.all())
        if items:
            for item in items:
                base = (item.quantity or 0) * (item.unit_price or 0)
                rate = item.tax_rate or Decimal('0')
                vat = base * rate / Decimal('100')
                if base <= 0:
                    continue
                box_code = _invoice_item_box(
                    item=item,
                    partner=partner,
                    has_service_date=bool(invoice.service_date),
                )
                if box_code is None:
                    continue
                rule = PDV_MAPPING[box_code]
                _create_ledger_entry(
                    tenant=tenant,
                    period=period,
                    rule=rule,
                    entry_date=invoice.issue_date,
                    document_number=invoice.invoice_number,
                    partner_name=partner.name if partner else '',
                    partner_oib=partner_vat,
                    base_amount=base,
                    vat_rate=normalize_vat_rate(rate),
                    vat_amount=vat,
                    source_content_type=invoice_ct,
                    source_object_id=invoice.pk,
                )
                created += 1
        elif invoice.subtotal > 0:
            if (
                Decimal(invoice.tax_amount or 0) == 0
                and is_eu_outbound_invoice_line(partner=partner, tax_rate=Decimal('0'))
            ):
                box_code = invoice_eu_outbound_box(has_service_date=bool(invoice.service_date))
                rule = PDV_MAPPING[box_code]
                _create_ledger_entry(
                    tenant=tenant,
                    period=period,
                    rule=rule,
                    entry_date=invoice.issue_date,
                    document_number=invoice.invoice_number,
                    partner_name=partner.name if partner else '',
                    partner_oib=partner_vat,
                    base_amount=invoice.subtotal,
                    vat_rate=Decimal('0.00'),
                    vat_amount=Decimal('0.00'),
                    source_content_type=invoice_ct,
                    source_object_id=invoice.pk,
                )
                created += 1
            else:
                rate = Decimal('25')
                box_code = invoice_rate_to_box(rate)
                if box_code is not None:
                    rule = PDV_MAPPING[box_code]
                    _create_ledger_entry(
                        tenant=tenant,
                        period=period,
                        rule=rule,
                        entry_date=invoice.issue_date,
                        document_number=invoice.invoice_number,
                        partner_name=partner.name if partner else '',
                        partner_oib=getattr(partner, 'tax_number', '') or '',
                        base_amount=invoice.subtotal,
                        vat_rate=rate,
                        vat_amount=invoice.tax_amount,
                        source_content_type=invoice_ct,
                        source_object_id=invoice.pk,
                    )
                    created += 1

    expense_ct = ContentType.objects.get_for_model(Expense)
    expense_rule = PDV_MAPPING['303']
    for expense in Expense.all_objects.filter(
        tenant=tenant,
        expense_date__year=year,
        expense_date__month=month,
        status__in=['approved', 'paid'],
    ):
        if VATLedgerEntry.all_objects.filter(
            tenant=tenant,
            vat_period=period,
            source_content_type=expense_ct,
            source_object_id=expense.pk,
        ).exists():
            continue

        supplier = expense.supplier
        procedure = getattr(expense, 'vat_procedure', VatSupplyProcedure.STANDARD) or VatSupplyProcedure.STANDARD
        if procedure == VatSupplyProcedure.IOSS:
            net = Decimal(expense.amount or 0) - Decimal(expense.tax_amount or 0)
            if net <= 0 and not expense.tax_amount:
                continue
            rate = Decimal('25')
            if net > 0 and expense.tax_amount:
                rate = (Decimal(expense.tax_amount) / net * Decimal('100')).quantize(Decimal('0.01'))
            rule = PDV_MAPPING['308']
            _create_ledger_entry(
                tenant=tenant,
                period=period,
                rule=rule,
                entry_date=expense.expense_date,
                document_number=expense.receipt_number or expense.expense_number,
                partner_name=supplier.name if supplier else '',
                partner_oib=supplier.tax_number if supplier else '',
                base_amount=net,
                vat_rate=rate,
                vat_amount=Decimal(expense.tax_amount or 0),
                source_content_type=expense_ct,
                source_object_id=expense.pk,
            )
            created += 1
            continue

        if is_eu_supplier(supplier) and not Decimal(expense.tax_amount or 0):
            net = Decimal(expense.amount or 0)
            has_linked_je = JournalEntry.all_objects.filter(
                tenant=tenant,
                source_content_type=expense_ct,
                source_object_id=expense.pk,
            ).exists()
            if not has_linked_je and net > 0:
                rule = PDV_MAPPING['614']
                _create_ledger_entry(
                    tenant=tenant,
                    period=period,
                    rule=rule,
                    entry_date=expense.expense_date,
                    document_number=expense.receipt_number or expense.expense_number,
                    partner_name=supplier.name if supplier else '',
                    partner_oib=supplier.tax_number if supplier else '',
                    base_amount=net,
                    vat_rate=Decimal('0.00'),
                    vat_amount=Decimal('0.00'),
                    source_content_type=expense_ct,
                    source_object_id=expense.pk,
                )
                created += 1
            continue

        net = Decimal(expense.amount) - Decimal(expense.tax_amount or 0)
        if net <= 0 and not expense.tax_amount:
            continue
        rate = Decimal('25')
        if net > 0 and expense.tax_amount:
            rate = (Decimal(expense.tax_amount) / net * Decimal('100')).quantize(Decimal('0.01'))

        _create_ledger_entry(
            tenant=tenant,
            period=period,
            rule=expense_rule,
            entry_date=expense.expense_date,
            document_number=expense.receipt_number or expense.expense_number,
            partner_name=supplier.name if supplier else '',
            partner_oib=supplier.tax_number if supplier else '',
            base_amount=net,
            vat_rate=rate,
            vat_amount=Decimal(expense.tax_amount or 0),
            source_content_type=expense_ct,
            source_object_id=expense.pk,
        )
        created += 1

    journal_line_ct = ContentType.objects.get_for_model(JournalEntryLine)
    expense_ct = ContentType.objects.get_for_model(Expense)
    invoice_ct_for_journal = ContentType.objects.get_for_model(Invoice)
    journal_lines = JournalEntryLine.objects.filter(
        journal_entry__tenant=tenant,
        journal_entry__status='posted',
        journal_entry__entry_date__year=year,
        journal_entry__entry_date__month=month,
    ).select_related('journal_entry', 'account')

    def _ledger_exists_for_line(line_pk: int) -> bool:
        return VATLedgerEntry.all_objects.filter(
            tenant=tenant,
            vat_period=period,
            source_content_type=journal_line_ct,
            source_object_id=line_pk,
        ).exists()

    def _post_journal_ledger(line, *, box_code: str, base_amount, vat_rate, vat_amount) -> None:
        nonlocal created
        entry = line.journal_entry
        rule = PDV_MAPPING[box_code]
        _create_ledger_entry(
            tenant=tenant,
            period=period,
            rule=rule,
            entry_date=entry.entry_date,
            document_number=f'{entry.entry_number}#{line.pk}',
            partner_name='',
            partner_oib='',
            base_amount=base_amount,
            vat_rate=vat_rate,
            vat_amount=vat_amount,
            source_content_type=journal_line_ct,
            source_object_id=line.pk,
        )
        created += 1

    for line in journal_lines:
        if _ledger_exists_for_line(line.pk):
            continue

        entry = line.journal_entry
        account_code = line.account.account_code
        source_ct_id = entry.source_content_type_id
        if source_ct_id == expense_ct.id and account_code in _JOURNAL_INPUT_ACCOUNTS:
            continue
        if source_ct_id == expense_ct.id and account_code in IOSS_PRETPOREZ_ACCOUNTS:
            continue
        if source_ct_id == expense_ct.id and account_code in (
            EU_GOODS_ASSET_ACCOUNTS | EU_GOODS_PRETPOREZ_ACCOUNTS | EU_GOODS_OBVEZA_ACCOUNTS
        ):
            continue
        if source_ct_id == expense_ct.id and account_code in _RC_SKIP_ACCOUNTS:
            continue
        if source_ct_id == invoice_ct_for_journal.id and account_code in _JOURNAL_OUTPUT_ACCOUNT_TO_BOX:
            continue

        if account_code in EU_GOODS_ASSET_ACCOUNTS and line.debit_amount > 0:
            # PPMV i drugi tuzemni troškovi na 0373 nisu EU stjecanje (II.7 / PDV-S).
            if 'PPMV' in (entry.description or '').upper():
                continue
            base_amount = line.debit_amount
            _post_journal_ledger(
                line,
                box_code='207',
                base_amount=base_amount,
                vat_rate=Decimal('0.00'),
                vat_amount=Decimal('0.00'),
            )
            continue

        box_code = journal_line_to_box(
            account_code=account_code,
            debit_amount=line.debit_amount,
            credit_amount=line.credit_amount,
        )
        if box_code is None:
            continue

        rc_amounts = _journal_rc_line_amounts(
            tenant=tenant,
            period=period,
            box_code=box_code,
            account_code=account_code,
            debit_amount=line.debit_amount,
            credit_amount=line.credit_amount,
        )
        if rc_amounts is not None:
            base_amount, rate, vat_amount = rc_amounts
        elif line.debit_amount > 0 and account_code in (_JOURNAL_INPUT_ACCOUNTS | IOSS_PRETPOREZ_ACCOUNTS):
            vat_amount = line.debit_amount
            rate = journal_line_vat_rate(account_code) if account_code in IOSS_PRETPOREZ_ACCOUNTS else Decimal('25.00')
            base_amount = pretporez_base_from_vat(vat_amount, rate)
        else:
            rate = journal_line_vat_rate(account_code)
            vat_amount = line.credit_amount
            base_amount = pretporez_base_from_vat(vat_amount, rate)

        _post_journal_ledger(
            line,
            box_code=box_code,
            base_amount=base_amount,
            vat_rate=rate,
            vat_amount=vat_amount,
        )

    for line in journal_lines:
        if _ledger_exists_for_line(line.pk):
            continue

        account_code = line.account.account_code
        viii1_box = journal_line_to_viii1_box(
            account_code=account_code,
            debit_amount=line.debit_amount,
            credit_amount=line.credit_amount,
        )
        if viii1_box is None:
            continue

        amount = line.debit_amount if line.debit_amount > 0 else line.credit_amount
        base_amount, vat_amount = viii1_box_amounts(viii1_box, amount)
        _post_journal_ledger(
            line,
            box_code=viii1_box,
            base_amount=base_amount,
            vat_rate=Decimal('0.00'),
            vat_amount=vat_amount,
        )

    created += _sync_viii1_box_610(tenant=tenant, period=period)

    return created, VATLedgerEntry.all_objects.filter(tenant=tenant, vat_period=period).count()


def aggregate_vat_period(period) -> dict:
    entries = VATLedgerEntry.all_objects.filter(vat_period=period)
    result = {
        'output': defaultdict(lambda: {'base': Decimal('0'), 'vat': Decimal('0')}),
        'input': defaultdict(lambda: {'base': Decimal('0'), 'vat': Decimal('0')}),
        'i_ra_count': 0,
        'u_ra_count': 0,
    }

    for entry in entries:
        bucket = 'output' if entry.ledger_type == _LEDGER_OUTPUT else 'input'
        key = str(entry.vat_rate.quantize(Decimal('0.01')))
        result[bucket][key]['base'] += entry.base_amount
        result[bucket][key]['vat'] += entry.vat_amount
        if entry.ledger_type == _LEDGER_OUTPUT:
            result['i_ra_count'] += 1
        elif entry.ledger_type == _LEDGER_INPUT:
            result['u_ra_count'] += 1

    total_output_vat = sum(v['vat'] for v in result['output'].values())
    total_input_vat = sum(v['vat'] for v in result['input'].values())
    result['vat_due'] = total_output_vat - total_input_vat
    result['total_output_vat'] = total_output_vat
    result['total_input_vat'] = total_input_vat
    return result


def export_pdv_s_xlsx(period) -> bytes:
    import xlsxwriter

    summary = aggregate_vat_period(period)
    buffer = BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {'in_memory': True})
    ws = workbook.add_worksheet('PDV-S')

    header = workbook.add_format({'bold': True})
    money = workbook.add_format({'num_format': '#,##0.00'})

    ws.write(0, 0, f"PDV obrazac — {period.month:02d}/{period.year}", header)
    row = 2
    ws.write(row, 0, 'I. ISPORUKE (I-RA)', header)
    row += 1
    ws.write(row, 0, 'Stopa PDV (%)')
    ws.write(row, 1, 'Osnovica')
    ws.write(row, 2, 'PDV')
    row += 1
    for rate, amounts in sorted(summary['output'].items(), key=lambda x: -Decimal(x[0])):
        ws.write(row, 0, rate)
        ws.write(row, 1, float(amounts['base']), money)
        ws.write(row, 2, float(amounts['vat']), money)
        row += 1

    row += 1
    ws.write(row, 0, 'II. PRETPOREZ (U-RA)', header)
    row += 1
    ws.write(row, 0, 'Stopa PDV (%)')
    ws.write(row, 1, 'Osnovica')
    ws.write(row, 2, 'Pretporez')
    row += 1
    for rate, amounts in sorted(summary['input'].items(), key=lambda x: -Decimal(x[0])):
        ws.write(row, 0, rate)
        ws.write(row, 1, float(amounts['base']), money)
        ws.write(row, 2, float(amounts['vat']), money)
        row += 1

    row += 1
    ws.write(row, 0, 'III. PDV ZA UPLATU / POVRAT', header)
    row += 1
    ws.write(row, 0, 'Ukupna obveza PDV')
    ws.write(row, 1, float(summary['total_output_vat']), money)
    row += 1
    ws.write(row, 0, 'Ukupni pretporez')
    ws.write(row, 1, float(summary['total_input_vat']), money)
    row += 1
    ws.write(row, 0, 'Za uplatu (+) / povrat (-)')
    ws.write(row, 1, float(summary['vat_due']), money)

    detail = workbook.add_worksheet('I-RA')
    cols = ['Datum', 'Broj', 'Partner', 'OIB', 'Osnovica', 'Stopa', 'PDV']
    for col, title in enumerate(cols):
        detail.write(0, col, title, header)
    row = 1
    for entry in VATLedgerEntry.all_objects.filter(vat_period=period, ledger_type=_LEDGER_OUTPUT).order_by('entry_date'):
        detail.write(row, 0, entry.entry_date.strftime('%d.%m.%Y'))
        detail.write(row, 1, entry.document_number)
        detail.write(row, 2, entry.partner_name)
        detail.write(row, 3, entry.partner_oib)
        detail.write(row, 4, float(entry.base_amount), money)
        detail.write(row, 5, float(entry.vat_rate))
        detail.write(row, 6, float(entry.vat_amount), money)
        row += 1

    detail2 = workbook.add_worksheet('U-RA')
    for col, title in enumerate(cols):
        detail2.write(0, col, title, header)
    row = 1
    for entry in VATLedgerEntry.all_objects.filter(vat_period=period, ledger_type=_LEDGER_INPUT).order_by('entry_date'):
        detail2.write(row, 0, entry.entry_date.strftime('%d.%m.%Y'))
        detail2.write(row, 1, entry.document_number)
        detail2.write(row, 2, entry.partner_name)
        detail2.write(row, 3, entry.partner_oib)
        detail2.write(row, 4, float(entry.base_amount), money)
        detail2.write(row, 5, float(entry.vat_rate))
        detail2.write(row, 6, float(entry.vat_amount), money)
        row += 1

    workbook.close()
    buffer.seek(0)
    return buffer.getvalue()
