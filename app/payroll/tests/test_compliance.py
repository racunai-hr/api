"""Sliceovi evidencije, ovrhe, isplata, NP1, prestanka i storna."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import PostingRule
from payroll.models import (
    AnnualLeaveEntitlement,
    Employee,
    EmployeeEnforcement,
    EmploymentContract,
    MunicipalityIncomeTaxRate,
    PayrollDocument,
    PayrollPeriod,
    PayrollTaxYear,
    Payslip,
    PayslipEarningLine,
    WorkTimeType,
)
from payroll.services.calculate import calculate_payslip
from payroll.services.posting import post_payroll_period, reverse_payroll_period
from payroll.services.records import apply_enforcements, issue_np1, record_payment
from payroll.services.records import build_termination_payslip
from payroll.services.worktime import ensure_default_work_time_types, lock_month, rate_pct_for, reopen_month, upsert_entry
from payroll.models import WorkTimeMonth
from payroll.services.worktime import _month
from tenants.models import Tenant, TenantMembership

HOST = 'compliance.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
    JOPPD_SUBMIT_ENABLED=False,
)
class ComplianceSliceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from accounting.models import RRIFChartEntry
        from accounting.services.rrif_import import import_rrif_chart

        if not RRIFChartEntry.objects.exists():
            import_rrif_chart()
        cls.tenant = Tenant.objects.create(name='Compliance d.o.o.', slug='compliance')
        cls.user = User.objects.create_user(username='compliance-user', password='x')
        TenantMembership.objects.create(user=cls.user, tenant=cls.tenant, role='accountant')
        PayrollTaxYear.objects.get_or_create(year=2026, defaults={})
        MunicipalityIncomeTaxRate.objects.get_or_create(
            year=2026, code='04448',
            defaults={'name': 'ŠIBENIK', 'lower_rate': Decimal('20'), 'higher_rate': Decimal('30')},
        )
        ensure_default_work_time_types()
        cls.employee = Employee.objects.create(
            tenant=cls.tenant, first_name='Iva', last_name='Ivić', oib='33333333333',
            municipality_code='04448', iban='HR1210010051863000160',
        )
        cls.contract = EmploymentContract.objects.create(
            tenant=cls.tenant, employee=cls.employee, start_date=date(2026, 1, 1),
            base_gross_salary=Decimal('2000.00'),
        )

    def _payslip(self, gross='2000.00', month=1):
        period = PayrollPeriod.objects.create(
            tenant=self.tenant, year=2026, month=month,
            calculation_date=date(2026, month, 28), payment_date=date(2026, month, 28),
        )
        payslip = Payslip.objects.create(
            tenant=self.tenant, period=period, employee=self.employee, contract=self.contract,
            date_from=date(2026, month, 1), date_to=date(2026, month, 28),
        )
        PayslipEarningLine.objects.create(
            tenant=self.tenant, payslip=payslip, name='REDOVNI RAD', gross_amount=Decimal(gross),
        )
        return calculate_payslip(payslip)

    def test_worktime_lock_and_reopen_after_calculation(self):
        regular = WorkTimeType.objects.get(code='REGULAR')
        upsert_entry(
            tenant=self.tenant, employee=self.employee, on=date(2026, 5, 4),
            work_time_type=regular, hours=Decimal('8'), contract=self.contract, user=self.user,
        )
        month = _month(self.tenant, self.employee, 2026, 5)
        lock_month(month, self.user)
        month.refresh_from_db()
        self.assertEqual(month.status, WorkTimeMonth.LOCKED)
        self.assertTrue(month.content_hash)
        with self.assertRaises(ValidationError):
            upsert_entry(
                tenant=self.tenant, employee=self.employee, on=date(2026, 5, 5),
                work_time_type=regular, hours=Decimal('8'), contract=self.contract,
            )
        payslip = self._payslip(month=5)
        self.assertEqual(payslip.status, Payslip.CALCULATED)
        reopen_month(month, self.user)
        payslip.refresh_from_db()
        month.refresh_from_db()
        self.assertEqual(month.status, WorkTimeMonth.REOPENED)
        self.assertEqual(payslip.status, Payslip.DRAFT)

    def test_sunday_rate_respects_legal_minimum(self):
        sunday = WorkTimeType.objects.get(code='SUNDAY')
        self.assertGreaterEqual(rate_pct_for(self.contract, sunday, date(2026, 5, 3)), sunday.legal_min_pct)

    def test_foreign_worker_without_permit_is_flagged_and_dates_stay_on_contract(self):
        self.employee.citizenship = 'UA'
        self.employee.save(update_fields=['citizenship'])
        from payroll.services.records import legal_evidence_gaps
        self.assertEqual(legal_evidence_gaps(self.employee), ['work_permit'])
        self.assertFalse(hasattr(Employee, 'start_date'))
        self.assertEqual(self.contract.start_date, date(2026, 1, 1))

    def test_enforcement_priority_and_protected_split(self):
        payslip = self._payslip(month=6)
        EmployeeEnforcement.objects.create(
            tenant=self.tenant, employee=self.employee, kind=EmployeeEnforcement.ORDINARY,
            priority=2, creditor_name='Banka', amount_total=Decimal('500'), amount_remaining=Decimal('500'),
            valid_from=date(2026, 1, 1),
        )
        EmployeeEnforcement.objects.create(
            tenant=self.tenant, employee=self.employee, kind=EmployeeEnforcement.MAINTENANCE,
            priority=1, creditor_name='Dijete', amount_total=Decimal('100'), amount_remaining=Decimal('100'),
            valid_from=date(2026, 1, 1),
        )
        payslip = apply_enforcements(payslip, self.user)
        lines = list(payslip.adjustment_lines.order_by('id'))
        self.assertEqual(lines[0].enforcement.kind, EmployeeEnforcement.MAINTENANCE)
        self.assertEqual(lines[0].amount, Decimal('100.00'))
        self.assertEqual(lines[1].amount, Decimal('350.00'))
        self.assertEqual(payslip.protected_total, payslip.amount_to_pay)
        self.assertEqual(payslip.regular_account_amount, Decimal('0.00'))

    def test_partial_payment_then_np1(self):
        payslip = self._payslip(month=7)
        record_payment(payslip, Decimal('100.00'), self.user)
        payslip.refresh_from_db()
        self.assertEqual(payslip.payment_status, 'partially_paid')
        document = issue_np1(payslip, self.user, today=date(2026, 8, 20))
        self.assertEqual(document.kind, PayrollDocument.NP1)
        with self.assertRaises(ValidationError):
            document.payload = {}
            document.save()

    def test_termination_writes_no1(self):
        AnnualLeaveEntitlement.objects.create(
            tenant=self.tenant, employee=self.employee, year=2026,
            days_entitled=Decimal('20'), days_used=Decimal('10'), payout_amount=Decimal('400'),
        )
        payslip = self._payslip(month=8)
        payslip = build_termination_payslip(payslip, severance=Decimal('1000'), user=self.user)
        self.assertEqual(payslip.kind, 'termination')
        self.assertTrue(PayrollDocument.all_objects.filter(payslip=payslip, kind=PayrollDocument.NO1).exists())

    def test_post_split_mio_and_reversal(self):
        PostingRule.all_objects.create(
            tenant=self.tenant, document_type='payroll_posted', amount_field='payroll_mio1',
            name='MIO I', debit_account_code='4220', credit_account_code='2420', is_active=True,
        )
        PostingRule.all_objects.create(
            tenant=self.tenant, document_type='payroll_posted', amount_field='payroll_mio2',
            name='MIO II', debit_account_code='4220', credit_account_code='2421', is_active=True,
        )
        payslip = self._payslip(month=9)
        payslip.period.status = PayrollPeriod.CALCULATED
        payslip.period.save(update_fields=['status'])
        entry = post_payroll_period(payslip.period, self.user)
        credits = {line.account.account_code for line in entry.lines.all() if line.credit_amount}
        self.assertIn('2420', credits)
        self.assertIn('2421', credits)
        reversal = reverse_payroll_period(payslip.period, self.user)
        self.assertEqual(reversal.reversed_entry_id, entry.id)
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'reversed')

    def test_joppd_submit_blocked_without_official_schema(self):
        from settings.models import CompanySettings
        CompanySettings.all_objects.create(
            tenant=self.tenant, company_name='Compliance', street='A', house_number='1',
            postal_code='10000', city='Zagreb', vat_number='12345678901',
        )
        payslip = self._payslip(month=10)
        payslip.period.status = PayrollPeriod.CALCULATED
        payslip.period.save(update_fields=['status'])
        client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = HOST
        response = client.post(
            '/api/tax/joppd/periods/2026-10/submit/',
            {'eporezna_identifier': '11111111-1111-1111-1111-111111111111', 'submitted_at': '2026-11-02T10:00:00Z'},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_compliance_walk_worktime_to_reversal(self):
        from settings.models import CompanySettings
        from domains.tax.write.service import joppd_xml_bytes
        from payroll.services.generate import generate_payslips_for_period
        from payroll.services.lifecycle import mark_period_calculated

        CompanySettings.all_objects.get_or_create(
            tenant=self.tenant,
            defaults={
                'company_name': 'Compliance', 'street': 'A', 'house_number': '1',
                'postal_code': '10000', 'city': 'Zagreb', 'vat_number': '12345678901',
            },
        )
        walker = Employee.objects.create(
            tenant=self.tenant, first_name='Ena', last_name='Enić', oib='44444444444',
            municipality_code='04448', citizenship='HR', occupation='Knjigovođa',
        )
        EmploymentContract.objects.create(
            tenant=self.tenant, employee=walker, start_date=date(2026, 1, 1),
            base_gross_salary=Decimal('2000.00'), job_title='Referent',
            probation_months=3, notice_rule='15 dana', collective_agreement_ref='KU-1',
            signed_at=date(2025, 12, 15),
        )
        regular = WorkTimeType.objects.get(code='REGULAR')
        contract = walker.contracts.get()
        upsert_entry(
            tenant=self.tenant, employee=walker, on=date(2026, 11, 3),
            work_time_type=regular, hours=Decimal('8'), contract=contract, user=self.user,
        )
        month = _month(self.tenant, walker, 2026, 11)
        lock_month(month, self.user)
        period = PayrollPeriod.objects.create(
            tenant=self.tenant, year=2026, month=11,
            calculation_date=date(2026, 11, 28), payment_date=date(2026, 11, 28),
        )
        created = generate_payslips_for_period(period)
        payslip = next(row for row in created if row.employee_id == walker.id)
        self.assertEqual(payslip.earning_lines.exclude(source='contract').count(), 1)
        for row in period.payslips.all():
            calculate_payslip(row)
        mark_period_calculated(period)
        entry = post_payroll_period(period, self.user)
        record_payment(payslip, Decimal('50.00'), self.user)
        document = issue_np1(payslip, self.user, today=date(2026, 12, 20))
        self.assertEqual(document.kind, PayrollDocument.NP1)
        xml_bytes, _filename = joppd_xml_bytes(self.tenant, 2026, 11)
        self.assertIn(b'ObrazacJOPPD', xml_bytes)
        self.assertIn(b'OznakaIzvjesca', xml_bytes)
        reversal = reverse_payroll_period(period, self.user)
        self.assertEqual(reversal.reversed_entry_id, entry.id)
