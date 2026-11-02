from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from accounting.models import ChartOfAccounts, PostingRule
from payroll.models import (
    Employee,
    EmploymentContract,
    MunicipalityIncomeTaxRate,
    PayrollPeriod,
    PayrollTaxYear,
    Payslip,
    PayslipEarningLine,
)
from payroll.services.calculate import calculate_payslip
from payroll.services.posting import PayrollPostingError, post_payroll_period
from tenants.models import Tenant


class PayrollPostingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from accounting.models import RRIFChartEntry
        from accounting.services.rrif_import import import_rrif_chart

        if not RRIFChartEntry.objects.exists():
            import_rrif_chart()

        cls.tenant = Tenant.objects.create(name='Posting Test d.o.o.', slug='posting-test-payroll')
        cls.user = User.objects.create_user(username='posting-tester', password='x')
        PayrollTaxYear.objects.get_or_create(year=2026, defaults={})
        MunicipalityIncomeTaxRate.objects.get_or_create(
            year=2026, code='04448',
            defaults={'name': 'ŠIBENIK', 'lower_rate': Decimal('20.00'), 'higher_rate': Decimal('30.00')},
        )
        cls.employee = Employee.objects.create(
            tenant=cls.tenant, first_name='Iva', last_name='Ivić', oib='33333333333',
            municipality_code='04448',
        )
        cls.contract = EmploymentContract.objects.create(
            tenant=cls.tenant, employee=cls.employee, start_date=date(2026, 1, 1),
            base_gross_salary=Decimal('1500.00'),
        )

    def _calculated_period(self):
        period = PayrollPeriod.objects.create(
            tenant=self.tenant, year=2026, month=1,
            calculation_date=date(2026, 1, 31), payment_date=date(2026, 2, 5),
        )
        payslip = Payslip.objects.create(
            tenant=self.tenant, period=period, employee=self.employee, contract=self.contract,
            date_from=date(2026, 1, 1), date_to=date(2026, 1, 31),
        )
        PayslipEarningLine.objects.create(
            tenant=self.tenant, payslip=payslip, name='REDOVNI RAD', gross_amount=Decimal('1500.00'),
        )
        calculate_payslip(payslip)
        period.status = PayrollPeriod.CALCULATED
        period.save(update_fields=['status'])
        return period

    def test_default_posting_rules_seeded_for_tenant(self):
        codes = set(
            PostingRule.all_objects.filter(tenant=self.tenant, document_type='payroll_posted')
            .values_list('amount_field', flat=True)
        )
        self.assertEqual(
            codes,
            {
                'payroll_net_total', 'payroll_income_tax_total',
                'payroll_mio_from_salary_total', 'payroll_zo_total',
            },
        )

    def test_post_payroll_period_creates_balanced_journal_entry(self):
        period = self._calculated_period()
        entry = post_payroll_period(period, self.user)

        self.assertEqual(entry.status, 'posted')
        self.assertEqual(entry.balance_difference, Decimal('0.00'))
        self.assertGreater(entry.lines.count(), 0)

        period.refresh_from_db()
        self.assertEqual(period.status, PayrollPeriod.POSTED)
        self.assertEqual(period.journal_entry_id, entry.id)

        payslip = period.payslips.first()
        payslip.refresh_from_db()
        self.assertEqual(payslip.status, Payslip.POSTED)

        net_line = entry.lines.filter(account__account_code='4200').first()
        self.assertIsNotNone(net_line)
        self.assertEqual(net_line.debit_amount, payslip.net_salary)

    def test_cannot_post_draft_period(self):
        period = PayrollPeriod.objects.create(
            tenant=self.tenant, year=2026, month=2,
            calculation_date=date(2026, 2, 28), payment_date=date(2026, 3, 5),
        )
        with self.assertRaises(PayrollPostingError):
            post_payroll_period(period, self.user)

    def test_posting_is_idempotent(self):
        period = self._calculated_period()
        entry1 = post_payroll_period(period, self.user)
        period.refresh_from_db()
        entry2 = post_payroll_period(period, self.user)
        self.assertEqual(entry1.id, entry2.id)
