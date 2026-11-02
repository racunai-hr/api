"""Generiranje PDF obrazaca modula za plaće (WeasyPrint), po uzoru na
``invoices/services/pdf.py``.

Četiri obrasca iz uzorka "plaća obračun.pdf":
    1. Knjiženje plaće            -> render_payroll_journal_pdf_bytes
    2. Rekapitulacija po isplati  -> render_payment_recap_pdf_bytes
    3. Rekapitulacija obračuna    -> render_calculation_recap_pdf_bytes
    4. Obračunska lista           -> render_payslip_pdf_bytes
"""

from __future__ import annotations

from decimal import Decimal

from django.template.loader import render_to_string
from weasyprint import HTML

from payments.models import BankAccount
from payroll.models import PayrollPeriod, PayrollTaxYear, Payslip
from payroll.services.calculate import get_tax_year
from payroll.services.reports import aggregate_period_totals, build_payroll_journal_lines
from settings.models import CompanySettings, ResponsiblePerson
from shared.iban import format_iban_display


def _company_context(tenant) -> dict:
    company = CompanySettings.objects.filter(tenant=tenant).first()
    payment_account = (
        BankAccount.all_objects.filter(tenant=tenant, is_active=True)
        .exclude(iban__isnull=True).exclude(iban='')
        .order_by('-id').first()
    )
    director = (
        ResponsiblePerson.objects.filter(company_settings=company, title='director', is_active=True).first()
        if company else None
    )
    return {
        'company': company,
        'company_logo_uri': None,
        'payment_iban': format_iban_display(payment_account.iban) if payment_account else '',
        'director_name': f'{director.first_name} {director.last_name}'.strip() if director else '',
    }


def _render(template_name: str, context: dict, base_url: str = '/') -> bytes:
    html_string = render_to_string(template_name, context)
    return HTML(string=html_string, base_url=base_url).write_pdf()


def render_payroll_journal_pdf_bytes(period: PayrollPeriod) -> bytes:
    journal_lines = build_payroll_journal_lines(period)
    total_debit = sum((line.debit for line in journal_lines), Decimal('0.00'))
    total_credit = sum((line.credit for line in journal_lines), Decimal('0.00'))
    context = {
        **_company_context(period.tenant),
        'document_title': 'Knjiženje plaće',
        'date_label': 'Datum',
        'date_value': period.calculation_date,
        'period': period,
        'journal_lines': journal_lines,
        'total_debit': total_debit,
        'total_credit': total_credit,
    }
    return _render('payroll/knjizenje_place.html', context)


def _recap_context(period: PayrollPeriod) -> dict:
    totals = aggregate_period_totals(period)
    total_hours = sum((e.hours for e in totals.earnings), Decimal('0.00'))
    contributions_from_total = sum((c.amount_from_salary for c in totals.contributions), Decimal('0.00'))
    return {
        **_company_context(period.tenant),
        'period': period,
        'totals': totals,
        'total_hours': total_hours,
        'contributions_from_total': contributions_from_total,
        'contributions_grand_total': contributions_from_total + totals.zo_total,
    }


def render_payment_recap_pdf_bytes(period: PayrollPeriod) -> bytes:
    context = {
        **_recap_context(period),
        'document_title': 'Rekapitulacija plaće po datumu isplate',
        'date_label': 'Datum isplate',
        'date_value': period.payment_date,
    }
    return _render('payroll/rekapitulacija_isplata.html', context)


def render_calculation_recap_pdf_bytes(period: PayrollPeriod) -> bytes:
    context = {
        **_recap_context(period),
        'document_title': 'Rekapitulacija obračunskih lista',
        'date_label': 'Datum obračuna plaće',
        'date_value': period.calculation_date,
    }
    return _render('payroll/rekapitulacija_obracuna.html', context)


def render_payslip_pdf_bytes(payslip: Payslip) -> bytes:
    tax_year = get_tax_year(payslip.period.year)
    adjustments_total = sum((line.amount for line in payslip.adjustment_lines.all()), Decimal('0.00'))
    context = {
        **_company_context(payslip.tenant),
        'document_title': 'Obračunska lista',
        'date_label': 'Datum',
        'date_value': payslip.period.calculation_date,
        'payslip': payslip,
        'tax_year': tax_year,
        'adjustments_total': adjustments_total,
        'income_tax_plus_surtax': payslip.income_tax_amount + payslip.surtax_amount,
    }
    return _render('payroll/obracunska_lista.html', context)
