"""Testovi obračunskog motora.

Prva skupina testova reproducira brojke iz uzorka 'plaća obračun.pdf'
(FINE STAR d.o.o., veljača 2022.) kao regresijski test ARITMETIKE motora —
napomena: taj uzorak je u HRK po tad važećim (2022.) stopama i NE smije se
koristiti kao izvor stvarnih 2026. stopa (vidi payroll/services/legal_data.py).
Druga skupina koristi stvarne 2026. parametre (iz import_payroll_legal_data).
"""

from decimal import Decimal

from django.test import TestCase

from django.core.exceptions import ValidationError

from payroll.models import (
    Employee,
    EmploymentContract,
    MunicipalityIncomeTaxRate,
    PayrollPeriod,
    PayrollTaxYear,
    Payslip,
    PayslipAdjustmentLine,
    PayslipEarningLine,
)
from payroll.services.calculate import calculate_payslip
from payroll.services.generate import generate_payslips_for_period
from payroll.services.legal_data import ensure_payroll_tax_year, import_municipality_rates
from domains.tax.forms.joppd.aggregate import aggregate_joppd_rows
from tenants.models import Tenant


class SamplePdfRegressionTests(TestCase):
    """Reproducira knjiženje iz plaća obračun.pdf (FINE STAR, veljača 2022., HRK)."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name='Fine Star d.o.o.', slug='finestar-test-payroll')
        cls.tax_year_2022 = PayrollTaxYear.objects.create(
            year=2022,
            basic_personal_allowance=Decimal('4000.00'),
            mio1_rate=Decimal('15.00'),
            mio2_rate=Decimal('5.00'),
            mio1_only_rate=Decimal('20.00'),
            zo_rate=Decimal('16.50'),
            mio1_base_reduction_threshold=Decimal('0.00'),  # nema umanjenja u ovom primjeru
            minimum_wage_gross=Decimal('0.00'),
            protected_account_reference_net=Decimal('0.00'),
            income_tax_annual_bracket_threshold=Decimal('60000.00'),
        )
        cls.rate_vodice = MunicipalityIncomeTaxRate.objects.create(
            year=2022, code='05002', name='VODICE', lower_rate=Decimal('0.00'), higher_rate=Decimal('0.00'),
        )
        cls.employee = Employee.objects.create(
            tenant=cls.tenant,
            first_name='CAROLINA PLAZA',
            last_name='RODRIGUEZ',
            oib='07155680871',
            city='SIBENIK',
            municipality_code='05002',
            mio_pillar=Employee.MIO_FIRST_ONLY,
            personal_allowance_coefficient=Decimal('0.0000'),
        )
        cls.contract = EmploymentContract.objects.create(
            tenant=cls.tenant,
            employee=cls.employee,
            start_date='2022-02-15',
            base_gross_salary=Decimal('2500.00'),
        )
        cls.period = PayrollPeriod.objects.create(
            tenant=cls.tenant, year=2022, month=2,
            calculation_date='2022-02-28', payment_date='2022-03-31',
        )
        cls.payslip = Payslip.objects.create(
            tenant=cls.tenant, period=cls.period, employee=cls.employee, contract=cls.contract,
            date_from='2022-02-15', date_to='2022-02-28', is_first_month=True,
        )
        PayslipEarningLine.objects.create(
            tenant=cls.tenant, payslip=cls.payslip, name='REDOVNI RAD',
            hours=Decimal('80.00'), factor=Decimal('1.0000'), gross_amount=Decimal('2500.00'),
        )

    def test_matches_sample_pdf_figures(self):
        payslip = calculate_payslip(self.payslip)

        self.assertEqual(payslip.gross_salary, Decimal('2500.00'))
        self.assertEqual(payslip.mio1_amount, Decimal('500.00'))  # DOP. ZA MIO 500,00
        self.assertEqual(payslip.mio2_amount, Decimal('0.00'))
        self.assertEqual(payslip.income_base, Decimal('2000.00'))  # dohodak
        self.assertEqual(payslip.personal_allowance_amount, Decimal('2000.00'))  # kapiran na dohodak
        self.assertEqual(payslip.tax_base, Decimal('0.00'))
        self.assertEqual(payslip.income_tax_amount, Decimal('0.00'))
        self.assertEqual(payslip.net_salary, Decimal('2000.00'))
        self.assertEqual(payslip.zo_amount, Decimal('412.50'))  # DOP. ZA ZDRAVSTVO 412,50
        self.assertEqual(payslip.total_labor_cost, Decimal('2912.50'))  # ukupni troškovi rada


class PayrollTaxYear2026Tests(TestCase):
    """Sanity testovi uz stvarne (javno objavljene) 2026. parametre."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name='Test d.o.o.', slug='test-payroll-2026')
        cls.tax_year = ensure_payroll_tax_year(2026)
        import_municipality_rates(2026)
        cls.employee_both = Employee.objects.create(
            tenant=cls.tenant, first_name='Ana', last_name='Anić', oib='11111111111',
            municipality_code='04448',  # Šibenik, 20%/30%
            mio_pillar=Employee.MIO_BOTH_PILLARS,
        )
        cls.contract = EmploymentContract.objects.create(
            tenant=cls.tenant, employee=cls.employee_both, start_date='2026-01-01',
            base_gross_salary=Decimal('1050.00'),
        )

    def _make_payslip(self, employee, contract, gross):
        period = PayrollPeriod.objects.create(
            tenant=self.tenant, year=2026, month=1,
            calculation_date='2026-01-31', payment_date='2026-02-05',
        )
        payslip = Payslip.objects.create(
            tenant=self.tenant, period=period, employee=employee, contract=contract,
            date_from='2026-01-01', date_to='2026-01-31',
        )
        PayslipEarningLine.objects.create(
            tenant=self.tenant, payslip=payslip, name='REDOVNI RAD',
            hours=Decimal('176.00'), factor=Decimal('1.0000'), gross_amount=gross,
        )
        return payslip

    def test_minimum_wage_matches_published_example(self):
        """Primjer iz izvora: bruto 1.050 € → MIO I 138,75; MIO II 52,50; ZO 173,25."""
        payslip = self._make_payslip(self.employee_both, self.contract, Decimal('1050.00'))
        payslip = calculate_payslip(payslip)

        self.assertEqual(payslip.mio1_base, Decimal('925.00'))
        self.assertEqual(payslip.mio1_amount, Decimal('138.75'))
        self.assertEqual(payslip.mio2_amount, Decimal('52.50'))
        self.assertEqual(payslip.zo_amount, Decimal('173.25'))
        self.assertEqual(payslip.total_labor_cost, Decimal('1223.25'))
        # Dohodak = 1050 - 138.75 - 52.50 = 858.75; osnovica = 858.75 - 600 = 258.75
        self.assertEqual(payslip.income_base, Decimal('858.75'))
        self.assertEqual(payslip.tax_base, Decimal('258.75'))
        self.assertEqual(payslip.income_tax_amount, Decimal('51.75'))  # 258.75 * 20%
        self.assertEqual(payslip.net_salary, Decimal('807.00'))

    def test_higher_salary_pays_income_tax_at_municipality_rate(self):
        employee = Employee.objects.create(
            tenant=self.tenant, first_name='Marko', last_name='Marić', oib='22222222222',
            municipality_code='04448', mio_pillar=Employee.MIO_BOTH_PILLARS,
        )
        contract = EmploymentContract.objects.create(
            tenant=self.tenant, employee=employee, start_date='2026-01-01',
            base_gross_salary=Decimal('2000.00'),
        )
        payslip = self._make_payslip(employee, contract, Decimal('2000.00'))
        payslip = calculate_payslip(payslip)

        # MIO: bruto >= 1300 pa nema umanjenja. 20% od 2000 = 400 (15%=300 + 5%=100)
        self.assertEqual(payslip.mio1_amount, Decimal('300.00'))
        self.assertEqual(payslip.mio2_amount, Decimal('100.00'))
        self.assertEqual(payslip.income_base, Decimal('1600.00'))
        self.assertEqual(payslip.personal_allowance_amount, Decimal('600.00'))
        self.assertEqual(payslip.tax_base, Decimal('1000.00'))
        self.assertEqual(payslip.income_tax_amount, Decimal('200.00'))  # 1000 * 20%
        self.assertEqual(payslip.income_tax_rate_used, Decimal('20.00'))
        self.assertEqual(payslip.net_salary, Decimal('1400.00'))
        self.assertEqual(payslip.amount_to_pay, Decimal('1400.00'))


class PayrollCorrectnessTests(PayrollTaxYear2026Tests):
    def test_taxable_addition_non_taxable_and_deduction(self):
        payslip = self._make_payslip(self.employee_both, self.contract, Decimal('2000.00'))
        PayslipAdjustmentLine.objects.create(
            tenant=self.tenant, payslip=payslip, name='Bonus', amount=Decimal('100.00'),
            kind=PayslipAdjustmentLine.TAXABLE_ADDITION, is_taxable=True,
        )
        PayslipAdjustmentLine.objects.create(
            tenant=self.tenant, payslip=payslip, name='Prijevoz', amount=Decimal('40.00'),
            kind=PayslipAdjustmentLine.NON_TAXABLE_RECEIPT, is_taxable=False,
        )
        PayslipAdjustmentLine.objects.create(
            tenant=self.tenant, payslip=payslip, name='Kredit', amount=Decimal('25.00'),
            kind=PayslipAdjustmentLine.DEDUCTION, is_taxable=False,
        )
        payslip = calculate_payslip(payslip)
        self.assertEqual(payslip.gross_salary, Decimal('2100.00'))
        self.assertEqual(payslip.non_taxable_total, Decimal('40.00'))
        self.assertEqual(payslip.deductions_total, Decimal('25.00'))
        self.assertEqual(payslip.amount_to_pay, payslip.net_salary + Decimal('40.00') - Decimal('25.00'))
        self.assertNotEqual(payslip.net_salary, payslip.amount_to_pay)

    def test_mio_reduction_bands_and_part_time_does_not_change_them(self):
        expected = {
            Decimal('500.00'): Decimal('200.00'),
            Decimal('700.00'): Decimal('400.00'),
            Decimal('1000.00'): Decimal('850.00'),
            Decimal('1300.00'): Decimal('1300.00'),
            Decimal('1300.01'): Decimal('1300.01'),
        }
        month = 2
        serial = 70000000000
        for gross, base in expected.items():
            for percentage in (Decimal('100.00'), Decimal('50.00')):
                serial += 1
                employee = Employee.objects.create(
                    tenant=self.tenant, first_name='M', last_name=f'{gross}-{percentage}',
                    oib=str(serial),
                    municipality_code='04448',
                )
                contract = EmploymentContract.objects.create(
                    tenant=self.tenant, employee=employee, start_date='2026-01-01',
                    base_gross_salary=gross, work_time_percentage=percentage,
                )
                period = PayrollPeriod.objects.create(
                    tenant=self.tenant, year=2026, month=month,
                    calculation_date='2026-02-28', payment_date='2026-03-05',
                )
                month += 1
                payslip = Payslip.objects.create(
                    tenant=self.tenant, period=period, employee=employee, contract=contract,
                    date_from='2026-02-01', date_to='2026-02-28',
                )
                PayslipEarningLine.objects.create(
                    tenant=self.tenant, payslip=payslip, name='REDOVNI RAD', gross_amount=gross,
                )
                payslip = calculate_payslip(payslip)
                self.assertEqual(payslip.mio1_base, base, f'{gross} @ {percentage}')

    def test_second_payout_in_the_same_month_shares_one_reduction(self):
        period = PayrollPeriod.objects.create(
            tenant=self.tenant, year=2026, month=3,
            calculation_date='2026-03-31', payment_date='2026-04-05',
        )
        first = Payslip.objects.create(
            tenant=self.tenant, period=period, employee=self.employee_both, contract=self.contract,
            date_from='2026-03-01', date_to='2026-03-31', payout_index=1,
        )
        PayslipEarningLine.objects.create(
            tenant=self.tenant, payslip=first, name='REDOVNI RAD', gross_amount=Decimal('600.00'),
        )
        first = calculate_payslip(first)
        self.assertEqual(first.mio1_base, Decimal('300.00'))

        second = Payslip.objects.create(
            tenant=self.tenant, period=period, employee=self.employee_both, contract=self.contract,
            date_from='2026-03-01', date_to='2026-03-31', payout_index=2,
        )
        PayslipEarningLine.objects.create(
            tenant=self.tenant, payslip=second, name='REDOVNI RAD', gross_amount=Decimal('600.00'),
        )
        second = calculate_payslip(second)
        first.refresh_from_db()
        self.assertEqual(first.mio1_base + second.mio1_base, Decimal('1150.00'))
        self.assertNotEqual(second.mio1_base, Decimal('300.00'))

    def test_partial_month_and_part_time_proration(self):
        employee = Employee.objects.create(
            tenant=self.tenant, first_name='Petra', last_name='Polovina', oib='44444444444',
            municipality_code='04448',
        )
        contract = EmploymentContract.objects.create(
            tenant=self.tenant, employee=employee, start_date='2026-04-16',
            base_gross_salary=Decimal('3100.00'), work_time_percentage=Decimal('50.00'),
        )
        period = PayrollPeriod.objects.create(
            tenant=self.tenant, year=2026, month=4,
            calculation_date='2026-04-30', payment_date='2026-05-05',
        )
        created = generate_payslips_for_period(period)
        payslip = next(row for row in created if row.employee_id == employee.id)
        # 15 dana od 30 × 50% × 3100 = 775.00
        self.assertEqual(payslip.earning_lines.get().gross_amount, Decimal('775.00'))

    def test_minimum_wage_flag_full_time_and_part_time(self):
        low = self._make_payslip(self.employee_both, self.contract, Decimal('1000.00'))
        low = calculate_payslip(low)
        self.assertEqual(low.compliance_flags[0]['code'], 'below_minimum_wage')

        employee = Employee.objects.create(
            tenant=self.tenant, first_name='Ivo', last_name='Pola', oib='55555555555',
            municipality_code='04448',
        )
        contract = EmploymentContract.objects.create(
            tenant=self.tenant, employee=employee, start_date='2026-01-01',
            base_gross_salary=Decimal('525.00'), work_time_percentage=Decimal('50.00'),
        )
        period = PayrollPeriod.objects.create(
            tenant=self.tenant, year=2026, month=6,
            calculation_date='2026-06-30', payment_date='2026-07-05',
        )
        half = Payslip.objects.create(
            tenant=self.tenant, period=period, employee=employee, contract=contract,
            date_from='2026-06-01', date_to='2026-06-30',
        )
        PayslipEarningLine.objects.create(
            tenant=self.tenant, payslip=half, name='REDOVNI RAD', gross_amount=Decimal('525.00'),
        )
        half = calculate_payslip(half)
        self.assertEqual(half.compliance_flags, [])

    def test_posted_payslip_and_its_lines_are_immutable(self):
        payslip = self._make_payslip(self.employee_both, self.contract, Decimal('2000.00'))
        payslip = calculate_payslip(payslip)
        Payslip.all_objects.filter(pk=payslip.pk).update(status=Payslip.POSTED)
        payslip.refresh_from_db()
        with self.assertRaises(ValidationError):
            calculate_payslip(payslip)
        line = payslip.earning_lines.get()
        line.hours = Decimal('1.00')
        with self.assertRaises(ValidationError):
            line.save()

    def test_joppd_rejects_draft_period(self):
        period = PayrollPeriod.objects.create(
            tenant=self.tenant, year=2026, month=8,
            calculation_date='2026-08-31', payment_date='2026-09-05',
        )
        with self.assertRaises(ValueError):
            aggregate_joppd_rows(self.tenant, period.year, period.month)
