"""Aggregate JoppdPayload from domains.hr.facade DTOs (no direct payroll.models import)."""

from __future__ import annotations

from accounting.services.tax_forms.pdv.build import accountant_for_tenant
from domains.hr.facade import get_payroll_period_rows, get_payroll_period_summary
from domains.tax.forms.joppd.payload import JoppdEmployer, JoppdPayload, JoppdPreparedBy, JoppdRow
from settings.models import CompanySettings


def _employer_for_tenant(tenant) -> JoppdEmployer:
    settings = CompanySettings.all_objects.filter(tenant=tenant).first()
    if settings is None:
        raise ValueError(f'CompanySettings missing for tenant {tenant.pk}')
    return JoppdEmployer(
        name=settings.company_name,
        oib=settings.vat_number or '',
        street=settings.street,
        house_number=settings.house_number,
        city=settings.city,
        postal_code=settings.postal_code,
    )


def aggregate_joppd_rows(tenant, year: int, month: int) -> JoppdPayload:
    """Build JoppdPayload for tenant/year/month from the payroll HR facade."""
    summary = get_payroll_period_summary(tenant, year, month)
    if summary is None:
        raise ValueError(f'Nema obračunskog perioda plaće za {month:02d}/{year}.')
    if summary.status not in ('calculated', 'posted'):
        raise ValueError(
            'JOPPD se može sastaviti samo za obračunat ili knjižen period plaće.'
        )

    dto_rows = get_payroll_period_rows(tenant, year, month)
    person = accountant_for_tenant(tenant)
    if person is not None:
        prepared_by = JoppdPreparedBy(
            first_name=person.first_name,
            last_name=person.last_name,
            email=person.email or '',
            phone=person.phone or '',
        )
    else:
        prepared_by = JoppdPreparedBy(first_name='NA', last_name='NA')

    rows = tuple(
        JoppdRow(
            recipient_oib=row.employee_oib,
            first_name=row.first_name,
            last_name=row.last_name,
            date_of_birth=row.date_of_birth,
            municipality_code=row.municipality_code,
            city=row.city,
            street=row.street,
            work_location=row.work_location,
            recipient_code=row.recipient_code,
            income_type_code=row.income_type_code,
            mio_pillar_both=row.mio_pillar_both,
            date_from=row.date_from,
            date_to=row.date_to,
            is_first_month=row.is_first_month,
            is_last_month=row.is_last_month,
            hours_total=row.hours_total,
            gross_salary=row.gross_salary,
            mio1_base=row.mio1_base,
            mio1_amount=row.mio1_amount,
            mio2_amount=row.mio2_amount,
            income_base=row.income_base,
            personal_allowance_amount=row.personal_allowance_amount,
            tax_base=row.tax_base,
            income_tax_rate_used=row.income_tax_rate_used,
            income_tax_amount=row.income_tax_amount,
            net_salary=row.net_salary,
            zo_amount=row.zo_amount,
            total_labor_cost=row.total_labor_cost,
        )
        for row in dto_rows
    )

    return JoppdPayload(
        period_year=year,
        period_month=month,
        payment_date=summary.payment_date,
        employer=_employer_for_tenant(tenant),
        prepared_by=prepared_by,
        rows=rows,
    )
