"""Knjiženje plaće u glavnu knjigu (JournalEntry) iz obračunatog PayrollPeriod-a.

Za razliku od `_BASE_POSTING_RULES` u `accounting.services.posting` (koji
knjiži JEDAN dokument s fiksnim poljima iznosa — račun, trošak...), plaća
agregira proizvoljan broj Payslip-ova s proizvoljnim brojem stavki doprinosa,
pa se temeljnica gradi izravno iz `payroll.services.reports.build_payroll_journal_lines`,
koji već koristi ispravna RRIF-RP2025 konta (vidi taj modul za objašnjenje).

Konto šifre koje koristimo (2300/2410/2420/2423/4200/4210/4220/4230) postoje
u svakom tenantovom kontnom planu od osnivanja (RRIF-RP2025 predložak, vidi
`accounting.services.chart.provision_tenant_chart`), pa ih nije potrebno
posebno sjemeniti.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.services.journal_lines import persist_journal_entry_line
from accounting.services.posting import _next_entry_number, get_or_create_fiscal_period, resolve_account
from accounting.models import JournalEntry
from payroll.models import PayrollPeriod, Payslip
from payroll.services.lifecycle import mark_payslips_posted, mark_period_posted
from payroll.services.reports import build_payroll_journal_lines


class PayrollPostingError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


@transaction.atomic
def post_payroll_period(period: PayrollPeriod, user) -> JournalEntry:
    if period.status == PayrollPeriod.POSTED and period.journal_entry_id:
        return period.journal_entry

    if period.status != PayrollPeriod.CALCULATED:
        raise PayrollPostingError(
            'Period mora biti u statusu "Obračunato" prije knjiženja (sve obračunske '
            'liste moraju biti izračunate).'
        )

    unposted_draft = period.payslips.filter(status=Payslip.DRAFT).exists()
    if unposted_draft:
        raise PayrollPostingError('Postoje obračunske liste koje nisu izračunate.')

    lines = build_payroll_journal_lines(period)
    if not lines:
        raise PayrollPostingError('Nema stavki za knjiženje (period nema obračunatih plaća).')

    entry_date = period.calculation_date
    entry = JournalEntry.all_objects.create(
        tenant=period.tenant,
        entry_number=_next_entry_number(period.tenant, entry_date),
        entry_date=entry_date,
        status='draft',
        description=f'Knjiženje plaće za {period.month}/{period.year}',
        reference=f'PAYROLL-{period.year}{period.month:02d}',
        is_auto=True,
        fiscal_period=get_or_create_fiscal_period(period.tenant, entry_date),
        created_by=user,
    )

    for line in lines:
        account = resolve_account(period.tenant, line.account_code)
        persist_journal_entry_line(
            journal_entry=entry,
            account=account,
            description=line.description,
            debit_amount=line.debit,
            credit_amount=line.credit,
        )

    try:
        entry.post(user)
    except ValidationError as exc:
        raise PayrollPostingError('; '.join(exc.messages)) from exc

    period.journal_entry = entry
    mark_period_posted(period)
    from django.utils import timezone

    period.posted_at = timezone.now()
    period.save(update_fields=['journal_entry', 'status', 'posted_at'])
    mark_payslips_posted(period)
    _create_payroll_payments(period, entry)
    from payroll.services.records import audit
    audit(period.tenant, user, 'post', period, {'journal_entry': entry.pk})

    return entry


def _create_payroll_payments(period: PayrollPeriod, entry) -> None:
    from payroll.models import PayrollPayment

    for payslip in period.payslips.all():
        rows = [
            (PayrollPayment.NET, payslip.amount_to_pay or payslip.net_salary),
            (PayrollPayment.TAX, payslip.income_tax_amount),
            (PayrollPayment.MIO1, payslip.mio1_amount),
            (PayrollPayment.MIO2, payslip.mio2_amount),
            (PayrollPayment.ZO, payslip.zo_amount),
            (PayrollPayment.DEDUCTION, payslip.deductions_total),
        ]
        for kind, amount in rows:
            if not amount:
                continue
            PayrollPayment.all_objects.create(
                tenant=period.tenant, payslip=payslip, kind=kind, amount=amount, journal_entry=entry,
            )


@transaction.atomic
def reverse_payroll_period(period: PayrollPeriod, user):
    if period.status != PayrollPeriod.POSTED or period.journal_entry_id is None:
        raise PayrollPostingError('Storno je moguć samo za knjiženi period.')
    reversal = period.journal_entry.reverse(user)
    period.reversal_entry = reversal
    period.save(update_fields=['reversal_entry'])
    from payroll.services.records import audit
    audit(period.tenant, user, 'reverse', period, {'reversal_entry': reversal.pk})
    return reversal
