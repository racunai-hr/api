"""Resolver bira datum po effective_basis, ne jedan datum za sva pravila."""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from payroll.models import (
    Employee,
    EmploymentContract,
    MunicipalityIncomeTaxRate,
    PayrollLegalParameter,
    PayrollPeriod,
    PayrollTaxYear,
    Payslip,
    PayslipEarningLine,
)
from payroll.services.calculate import calculate_payslip
from tenants.models import Tenant


class EffectiveBasisTests(TestCase):
    def test_payment_date_and_payroll_month_can_differ_on_one_payslip(self):
        tenant = Tenant.objects.create(name='Basis d.o.o.', slug='basis-payroll')
        PayrollTaxYear.objects.create(year=2025, mio1_rate=Decimal('15.00'), minimum_wage_gross=Decimal('970.00'))
        PayrollTaxYear.objects.create(year=2026, mio1_rate=Decimal('15.00'), minimum_wage_gross=Decimal('1050.00'))
        PayrollLegalParameter.objects.create(
            code='MIO1_RATE', value=Decimal('15.0000'), valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31),
            effective_basis=PayrollLegalParameter.PAYMENT_DATE,
        )
        PayrollLegalParameter.objects.create(
            code='MIO1_RATE', value=Decimal('16.0000'), valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
            effective_basis=PayrollLegalParameter.PAYMENT_DATE,
        )
        PayrollLegalParameter.objects.create(
            code='MIN_WAGE_GROSS_FULLTIME', value=Decimal('970.0000'), valid_from=date(2025, 1, 1),
            valid_to=date(2025, 12, 31), effective_basis=PayrollLegalParameter.PAYROLL_MONTH,
        )
        PayrollLegalParameter.objects.create(
            code='MIN_WAGE_GROSS_FULLTIME', value=Decimal('1050.0000'), valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31), effective_basis=PayrollLegalParameter.PAYROLL_MONTH,
        )
        MunicipalityIncomeTaxRate.objects.create(
            year=2026, code='01333', name='ZAGREB', lower_rate=Decimal('23.60'), higher_rate=Decimal('35.40'),
            valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
            effective_basis=PayrollLegalParameter.PAYMENT_DATE,
        )
        employee = Employee.objects.create(
            tenant=tenant, first_name='Ana', last_name='Anić', oib='12312312312', municipality_code='01333',
        )
        contract = EmploymentContract.objects.create(
            tenant=tenant, employee=employee, start_date=date(2025, 1, 1), base_gross_salary=Decimal('2000.00'),
        )
        period = PayrollPeriod.objects.create(
            tenant=tenant, year=2025, month=12,
            calculation_date=date(2025, 12, 31), payment_date=date(2026, 1, 15),
        )
        payslip = Payslip.objects.create(
            tenant=tenant, period=period, employee=employee, contract=contract,
            date_from=date(2025, 12, 1), date_to=date(2025, 12, 31),
        )
        PayslipEarningLine.objects.create(
            tenant=tenant, payslip=payslip, name='REDOVNI RAD', gross_amount=Decimal('2000.00'),
        )
        payslip = calculate_payslip(payslip)
        self.assertEqual(payslip.parameters_snapshot['MIO1_RATE']['on'], '2026-01-15')
        self.assertEqual(payslip.parameters_snapshot['MIO1_RATE']['value'], '16.0000')
        self.assertEqual(payslip.parameters_snapshot['MIN_WAGE_GROSS_FULLTIME']['on'], '2025-12-01')
        self.assertEqual(payslip.parameters_snapshot['MIN_WAGE_GROSS_FULLTIME']['value'], '970.0000')
        self.assertEqual(payslip.parameters_snapshot['MUNICIPALITY_RATE']['on'], '2026-01-15')
        again = calculate_payslip(payslip)
        self.assertEqual(again.parameters_snapshot, payslip.parameters_snapshot)
