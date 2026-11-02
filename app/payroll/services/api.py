"""Dict-returning CRUD/orchestration helpers for the JWT HR API (domains/hr/api).

Kept inside `payroll` (not `domains/hr`) because this *is* the payroll
domain's own app with its own models/migrations -- the `domains.hr` package
is the thin API-facing layer (see domains/hr/api/views.py), matching the
project convention: models+services live in the top-level app, API views in
domains/<domain>/api/.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from payroll.models import (
    Employee,
    EmploymentContract,
    PayrollPeriod,
    Payslip,
    PayslipEarningLine,
)
from payroll.services.calculate import calculate_payslip
from payroll.services.generate import generate_payslips_for_period
from payroll.services.posting import PayrollPostingError, post_payroll_period
from payroll.services.records import legal_evidence_gaps
from payroll.services.reports import aggregate_period_totals


class PayrollApiError(Exception):
    """Raised for 400-level business errors in the HR/payroll API."""


def _money(value) -> str:
    if value is None:
        return '0.00'
    return str(Decimal(value).quantize(Decimal('0.01')))


def _parse_date(value, field: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise PayrollApiError(f'Neispravan datum za polje "{field}": {value!r}') from exc


def _parse_decimal(value, field: str, *, default=None) -> Decimal:
    if value is None or value == '':
        if default is not None:
            return default
        raise PayrollApiError(f'Polje "{field}" je obavezno.')
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise PayrollApiError(f'Neispravan broj za polje "{field}": {value!r}') from exc


# ---------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------


def _contract_dto(contract: EmploymentContract) -> dict:
    return {
        'id': contract.id,
        'start_date': contract.start_date.isoformat(),
        'end_date': contract.end_date.isoformat() if contract.end_date else None,
        'contract_type': contract.contract_type,
        'work_time_percentage': _money(contract.work_time_percentage),
        'base_gross_salary': _money(contract.base_gross_salary),
        'recipient_code': contract.recipient_code,
        'income_type_code': contract.income_type_code,
        'is_active': contract.is_active,
        'signed_at': contract.signed_at.isoformat() if contract.signed_at else None,
        'weekly_hours': _money(contract.weekly_hours) if contract.weekly_hours is not None else None,
        'job_title': contract.job_title,
        'probation_months': contract.probation_months,
        'notice_rule': contract.notice_rule,
        'annual_leave_rule': contract.annual_leave_rule,
        'collective_agreement_ref': contract.collective_agreement_ref,
        'termination_reason': contract.termination_reason,
    }


def _employee_dto(employee: Employee, *, with_contracts: bool = False) -> dict:
    data = {
        'id': employee.id,
        'code': employee.code,
        'first_name': employee.first_name,
        'last_name': employee.last_name,
        'full_name': employee.full_name,
        'oib': employee.oib,
        'date_of_birth': employee.date_of_birth.isoformat() if employee.date_of_birth else None,
        'street': employee.street,
        'city': employee.city,
        'municipality_code': employee.municipality_code,
        'work_location': employee.work_location,
        'iban': employee.iban,
        'bank_name': employee.bank_name,
        'mio_pillar': employee.mio_pillar,
        'personal_allowance_coefficient': str(employee.personal_allowance_coefficient),
        'is_active': employee.is_active,
        'notes': employee.notes,
        'sex': employee.sex,
        'citizenship': employee.citizenship,
        'residence_street': employee.residence_street,
        'residence_city': employee.residence_city,
        'residence_municipality_code': employee.residence_municipality_code,
        'occupation': employee.occupation,
        'qualification': employee.qualification,
        'licence_note': employee.licence_note,
        'employment_status': employee.employment_status,
        'work_permit_number': employee.work_permit_number,
        'work_permit_valid_to': employee.work_permit_valid_to.isoformat() if employee.work_permit_valid_to else None,
        'hzzo_registered_at': employee.hzzo_registered_at.isoformat() if employee.hzzo_registered_at else None,
        'hzmo_registered_at': employee.hzmo_registered_at.isoformat() if employee.hzmo_registered_at else None,
        'insurance_changed_at': employee.insurance_changed_at.isoformat() if employee.insurance_changed_at else None,
        'deregistered_at': employee.deregistered_at.isoformat() if employee.deregistered_at else None,
        'legal_evidence_gaps': legal_evidence_gaps(employee),
    }
    active_contract = employee.contracts.filter(is_active=True).order_by('-start_date').first()
    data['active_contract'] = _contract_dto(active_contract) if active_contract else None
    if with_contracts:
        data['contracts'] = [_contract_dto(c) for c in employee.contracts.order_by('-start_date')]
    return data


def list_employees(tenant) -> dict:
    employees = Employee.objects.filter(tenant=tenant).order_by('last_name', 'first_name')
    return {'results': [_employee_dto(e) for e in employees]}


def get_employee(tenant, employee_id: int) -> dict:
    employee = Employee.objects.filter(tenant=tenant, pk=employee_id).first()
    if employee is None:
        return None
    return _employee_dto(employee, with_contracts=True)


_EMPLOYEE_FIELDS = (
    'code', 'first_name', 'last_name', 'oib', 'street', 'city',
    'municipality_code', 'work_location', 'iban', 'bank_name',
    'mio_pillar', 'is_active', 'notes', 'sex', 'citizenship',
    'residence_street', 'residence_city', 'residence_municipality_code',
    'occupation', 'qualification', 'licence_note', 'employment_status',
    'work_permit_number',
)
_EMPLOYEE_DATES = (
    'date_of_birth', 'work_permit_valid_to', 'hzzo_registered_at',
    'hzmo_registered_at', 'insurance_changed_at', 'deregistered_at',
)


def _apply_employee_fields(employee: Employee, data: dict) -> None:
    for field in _EMPLOYEE_FIELDS:
        if field in data:
            setattr(employee, field, data[field] or '' if field != 'is_active' else bool(data[field]))
    for field in _EMPLOYEE_DATES:
        if field in data:
            setattr(employee, field, _parse_date(data[field], field) if data[field] else None)
    if 'personal_allowance_coefficient' in data:
        employee.personal_allowance_coefficient = _parse_decimal(
            data['personal_allowance_coefficient'], 'personal_allowance_coefficient', default=Decimal('0.0000'),
        )


def create_employee(tenant, data: dict) -> dict:
    if not (data.get('first_name') or '').strip() or not (data.get('last_name') or '').strip():
        raise PayrollApiError('Ime i prezime zaposlenika su obavezni.')
    if not (data.get('oib') or '').strip():
        raise PayrollApiError('OIB zaposlenika je obavezan.')
    employee = Employee(tenant=tenant)
    _apply_employee_fields(employee, data)
    try:
        employee.full_clean(exclude=['tenant'])
    except Exception as exc:  # DjangoValidationError or similar
        raise PayrollApiError(str(exc)) from exc
    employee.save()
    return _employee_dto(employee, with_contracts=True)


def update_employee(tenant, employee_id: int, data: dict) -> dict:
    employee = Employee.objects.filter(tenant=tenant, pk=employee_id).first()
    if employee is None:
        return None
    _apply_employee_fields(employee, data)
    employee.save()
    return _employee_dto(employee, with_contracts=True)


def list_contracts(tenant, employee_id: int) -> dict | None:
    employee = Employee.objects.filter(tenant=tenant, pk=employee_id).first()
    if employee is None:
        return None
    return {'results': [_contract_dto(c) for c in employee.contracts.order_by('-start_date')]}


_CONTRACT_FIELDS = (
    'contract_type', 'recipient_code', 'income_type_code', 'is_active',
    'job_title', 'notice_rule', 'annual_leave_rule', 'collective_agreement_ref', 'termination_reason',
)


def create_contract(tenant, employee_id: int, data: dict) -> dict | None:
    employee = Employee.objects.filter(tenant=tenant, pk=employee_id).first()
    if employee is None:
        return None
    contract = EmploymentContract(tenant=tenant, employee=employee)
    contract.start_date = _parse_date(data.get('start_date'), 'start_date')
    contract.end_date = _parse_date(data['end_date'], 'end_date') if data.get('end_date') else None
    contract.base_gross_salary = _parse_decimal(data.get('base_gross_salary'), 'base_gross_salary')
    contract.work_time_percentage = _parse_decimal(
        data.get('work_time_percentage'), 'work_time_percentage', default=Decimal('100.00'),
    )
    for field in _CONTRACT_FIELDS:
        if field in data and data[field] not in (None, ''):
            setattr(contract, field, data[field])
    _apply_contract_extras(contract, data)
    contract.save()
    return _contract_dto(contract)


def _apply_contract_extras(contract: EmploymentContract, data: dict) -> None:
    if 'signed_at' in data:
        contract.signed_at = _parse_date(data['signed_at'], 'signed_at') if data['signed_at'] else None
    if 'weekly_hours' in data and data['weekly_hours'] not in (None, ''):
        contract.weekly_hours = _parse_decimal(data['weekly_hours'], 'weekly_hours')
    if 'probation_months' in data and data['probation_months'] not in (None, ''):
        contract.probation_months = int(data['probation_months'])


def update_contract(tenant, employee_id: int, contract_id: int, data: dict) -> dict | None:
    contract = EmploymentContract.objects.filter(
        tenant=tenant, employee_id=employee_id, pk=contract_id,
    ).first()
    if contract is None:
        return None
    if 'start_date' in data:
        contract.start_date = _parse_date(data['start_date'], 'start_date')
    if 'end_date' in data:
        contract.end_date = _parse_date(data['end_date'], 'end_date') if data['end_date'] else None
    if 'base_gross_salary' in data:
        contract.base_gross_salary = _parse_decimal(data['base_gross_salary'], 'base_gross_salary')
    if 'work_time_percentage' in data:
        contract.work_time_percentage = _parse_decimal(data['work_time_percentage'], 'work_time_percentage')
    for field in _CONTRACT_FIELDS:
        if field in data and data[field] not in (None, ''):
            setattr(contract, field, data[field])
    _apply_contract_extras(contract, data)
    contract.save()
    return _contract_dto(contract)


# ---------------------------------------------------------------------------
# Payroll periods / payslips
# ---------------------------------------------------------------------------


def _period_summary_dto(period: PayrollPeriod) -> dict:
    totals = aggregate_period_totals(period)
    return {
        'id': period.id,
        'year': period.year,
        'month': period.month,
        'calculation_date': period.calculation_date.isoformat(),
        'payment_date': period.payment_date.isoformat(),
        'status': period.status,
        'posted_at': period.posted_at.isoformat() if period.posted_at else None,
        'employee_count': period.payslips.count(),
        'gross_total': _money(totals.gross_total),
        'net_total': _money(totals.net_total),
        'income_tax_total': _money(totals.income_tax_total),
        'zo_total': _money(totals.zo_total),
    }


def _payslip_summary_dto(payslip: Payslip) -> dict:
    return {
        'id': payslip.id,
        'employee_id': payslip.employee_id,
        'employee_name': payslip.employee.full_name,
        'status': payslip.status,
        'gross_salary': _money(payslip.gross_salary),
        'net_salary': _money(payslip.net_salary),
        'amount_to_pay': _money(payslip.amount_to_pay),
        'non_taxable_total': _money(payslip.non_taxable_total),
        'deductions_total': _money(payslip.deductions_total),
        'compliance_flags': payslip.compliance_flags or [],
        'income_tax_amount': _money(payslip.income_tax_amount),
        'zo_amount': _money(payslip.zo_amount),
        'total_labor_cost': _money(payslip.total_labor_cost),
    }


def list_periods(tenant) -> dict:
    periods = PayrollPeriod.objects.filter(tenant=tenant).order_by('-year', '-month')
    return {'results': [_period_summary_dto(p) for p in periods]}


def get_period(tenant, period_id: int) -> dict | None:
    period = PayrollPeriod.objects.filter(tenant=tenant, pk=period_id).first()
    if period is None:
        return None
    data = _period_summary_dto(period)
    data['payslips'] = [
        _payslip_summary_dto(p)
        for p in period.payslips.select_related('employee').order_by('employee__last_name', 'employee__first_name')
    ]
    return data


def create_period(tenant, data: dict) -> dict:
    try:
        year = int(data['year'])
        month = int(data['month'])
    except (KeyError, TypeError, ValueError) as exc:
        raise PayrollApiError('Godina i mjesec su obavezni (brojevi).') from exc
    if PayrollPeriod.objects.filter(tenant=tenant, year=year, month=month).exists():
        raise PayrollApiError(f'Period {month:02d}/{year} već postoji.')
    period = PayrollPeriod.objects.create(
        tenant=tenant,
        year=year,
        month=month,
        calculation_date=_parse_date(data.get('calculation_date'), 'calculation_date'),
        payment_date=_parse_date(data.get('payment_date'), 'payment_date'),
    )
    return _period_summary_dto(period)


def generate_period(tenant, period_id: int) -> dict | None:
    period = PayrollPeriod.objects.filter(tenant=tenant, pk=period_id).first()
    if period is None:
        return None
    generate_payslips_for_period(period)
    return get_period(tenant, period_id)


def calculate_period(tenant, period_id: int) -> dict | None:
    period = PayrollPeriod.objects.filter(tenant=tenant, pk=period_id).first()
    if period is None:
        return None
    if period.status == PayrollPeriod.POSTED:
        raise PayrollApiError('Period je već knjižen — nije moguće ponovno obračunati.')
    payslips = list(period.payslips.all())
    if not payslips:
        raise PayrollApiError('Period nema obračunskih listi — prvo generirajte listu zaposlenika.')
    for payslip in payslips:
        calculate_payslip(payslip)
    from payroll.services.lifecycle import mark_period_calculated
    mark_period_calculated(period)
    return get_period(tenant, period_id)


def post_period(tenant, period_id: int, user) -> dict | None:
    period = PayrollPeriod.objects.filter(tenant=tenant, pk=period_id).first()
    if period is None:
        return None
    try:
        post_payroll_period(period, user)
    except PayrollPostingError as exc:
        raise PayrollApiError(str(exc)) from exc
    return get_period(tenant, period_id)


def get_payslip(tenant, payslip_id: int) -> dict | None:
    payslip = Payslip.objects.filter(tenant=tenant, pk=payslip_id).select_related(
        'employee', 'period', 'contract',
    ).first()
    if payslip is None:
        return None
    data = _payslip_summary_dto(payslip)
    data.update({
        'period': {'year': payslip.period.year, 'month': payslip.period.month},
        'date_from': payslip.date_from.isoformat(),
        'date_to': payslip.date_to.isoformat(),
        'mio1_base': _money(payslip.mio1_base),
        'mio1_amount': _money(payslip.mio1_amount),
        'mio2_amount': _money(payslip.mio2_amount),
        'income_base': _money(payslip.income_base),
        'personal_allowance_amount': _money(payslip.personal_allowance_amount),
        'tax_base': _money(payslip.tax_base),
        'income_tax_rate_used': str(payslip.income_tax_rate_used),
        'earning_lines': [
            {
                'name': line.name,
                'hours': str(line.hours),
                'gross_amount': _money(line.gross_amount),
            }
            for line in payslip.earning_lines.all()
        ],
    })
    return data


def get_payslip_for_pdf(tenant, payslip_id: int) -> Payslip | None:
    return Payslip.objects.filter(tenant=tenant, pk=payslip_id).first()


def get_period_for_pdf(tenant, period_id: int) -> PayrollPeriod | None:
    return PayrollPeriod.objects.filter(tenant=tenant, pk=period_id).first()


def list_worktime(tenant, employee_id: int, year: int, month: int) -> dict | None:
    from payroll.models import WorkTimeEntry, WorkTimeMonth
    employee = Employee.objects.filter(tenant=tenant, pk=employee_id).first()
    if employee is None:
        return None
    month_row = WorkTimeMonth.all_objects.filter(
        tenant=tenant, employee=employee, year=year, month=month,
    ).first()
    entries = WorkTimeEntry.all_objects.filter(
        tenant=tenant, employee=employee, date__year=year, date__month=month,
    ).select_related('work_time_type')
    return {
        'employee_id': employee_id,
        'year': year,
        'month': month,
        'status': month_row.status if month_row else 'open',
        'content_hash': month_row.content_hash if month_row else '',
        'entries': [
            {
                'id': entry.id,
                'date': entry.date.isoformat(),
                'type_code': entry.work_time_type.code,
                'type_name': entry.work_time_type.name,
                'hours': str(entry.hours),
            }
            for entry in entries
        ],
    }


def save_worktime_entry(tenant, data: dict, user=None) -> dict:
    from django.core.exceptions import ValidationError
    from payroll.models import WorkTimeType
    from payroll.services.worktime import ensure_default_work_time_types, upsert_entry
    ensure_default_work_time_types()
    employee = Employee.objects.filter(tenant=tenant, pk=data.get('employee_id')).first()
    if employee is None:
        raise PayrollApiError('Zaposlenik nije pronađen.')
    work_type = WorkTimeType.objects.filter(code=data.get('type_code')).first()
    if work_type is None:
        raise PayrollApiError('Nepoznata vrsta rada.')
    contract = employee.contracts.filter(is_active=True).order_by('-start_date').first()
    try:
        entry = upsert_entry(
            tenant=tenant, employee=employee, on=_parse_date(data.get('date'), 'date'),
            work_time_type=work_type, hours=_parse_decimal(data.get('hours'), 'hours'),
            contract=contract, user=user,
        )
    except ValidationError as exc:
        raise PayrollApiError('; '.join(exc.messages)) from exc
    return {'id': entry.id, 'hours': str(entry.hours)}


def lock_worktime(tenant, employee_id: int, year: int, month: int, user) -> dict | None:
    from payroll.models import WorkTimeMonth
    from payroll.services.worktime import _month, lock_month
    employee = Employee.objects.filter(tenant=tenant, pk=employee_id).first()
    if employee is None:
        return None
    row = _month(tenant, employee, year, month)
    lock_month(row, user)
    return list_worktime(tenant, employee_id, year, month)


def add_payslip_earning(tenant, payslip_id: int, data: dict) -> dict | None:
    from payroll.models import WorkTimeType
    payslip = Payslip.objects.filter(tenant=tenant, pk=payslip_id).first()
    if payslip is None:
        return None
    if payslip.status == Payslip.POSTED:
        raise PayrollApiError('Knjižena lista se ne može mijenjati.')
    work_type = None
    if data.get('type_code'):
        work_type = WorkTimeType.objects.filter(code=data['type_code']).first()
    line = PayslipEarningLine.objects.create(
        tenant=tenant, payslip=payslip,
        name=data.get('name') or (work_type.name if work_type else 'Naknada'),
        hours=_parse_decimal(data.get('hours'), 'hours', default=Decimal('0')),
        gross_amount=_parse_decimal(data.get('gross_amount'), 'gross_amount', default=Decimal('0')),
        work_time_type=work_type,
        source='manual',
    )
    return {'id': line.id, 'name': line.name}


def add_payslip_adjustment(tenant, payslip_id: int, data: dict) -> dict | None:
    from payroll.models import PayslipAdjustmentLine
    payslip = Payslip.objects.filter(tenant=tenant, pk=payslip_id).first()
    if payslip is None:
        return None
    if payslip.status == Payslip.POSTED:
        raise PayrollApiError('Knjižena lista se ne može mijenjati.')
    line = PayslipAdjustmentLine.objects.create(
        tenant=tenant, payslip=payslip,
        name=data.get('name') or 'Stavka',
        amount=_parse_decimal(data.get('amount'), 'amount'),
        kind=data.get('kind') or PayslipAdjustmentLine.TAXABLE_ADDITION,
        is_taxable=data.get('kind', 'taxable_addition') == 'taxable_addition',
    )
    return {'id': line.id, 'kind': line.kind}
