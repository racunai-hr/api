from decimal import Decimal

from django.test import TestCase

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
from payroll.services.pdf import (
    render_calculation_recap_pdf_bytes,
    render_payment_recap_pdf_bytes,
    render_payroll_journal_pdf_bytes,
    render_payslip_pdf_bytes,
)
from settings.models import CompanySettings
from tenants.models import Tenant


class PayrollPdfRenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name='PDF Test d.o.o.', slug='pdf-test-payroll')
        CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='PDF Test d.o.o.',
            company_address='Testna 1\n10000 Zagreb',
            street='Testna', house_number='1', postal_code='10000', city='Zagreb',
            company_phone='+3851', company_email='info@test.hr',
            vat_number='12345678901', registration_number='080000000',
            share_capital=Decimal('20000.00'),
        )
        PayrollTaxYear.objects.create(year=2026)
        MunicipalityIncomeTaxRate.objects.create(
            year=2026, code='04448', name='ŠIBENIK', lower_rate=Decimal('20.00'), higher_rate=Decimal('30.00'),
        )
        cls.employee = Employee.objects.create(
            tenant=cls.tenant, first_name='Ana', last_name='Anić', oib='11111111111',
            municipality_code='04448', city='Šibenik',
        )
        cls.contract = EmploymentContract.objects.create(
            tenant=cls.tenant, employee=cls.employee, start_date='2026-01-01',
            base_gross_salary=Decimal('1200.00'),
        )
        cls.period = PayrollPeriod.objects.create(
            tenant=cls.tenant, year=2026, month=1,
            calculation_date='2026-01-31', payment_date='2026-02-05',
        )
        cls.payslip = Payslip.objects.create(
            tenant=cls.tenant, period=cls.period, employee=cls.employee, contract=cls.contract,
            date_from='2026-01-01', date_to='2026-01-31',
        )
        PayslipEarningLine.objects.create(
            tenant=cls.tenant, payslip=cls.payslip, name='REDOVNI RAD',
            hours=Decimal('176.00'), gross_amount=Decimal('1200.00'),
        )
        calculate_payslip(cls.payslip)
        cls.payslip.refresh_from_db()

    def test_payslip_pdf_renders(self):
        pdf_bytes = render_payslip_pdf_bytes(self.payslip)
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))

    def test_journal_pdf_renders(self):
        pdf_bytes = render_payroll_journal_pdf_bytes(self.period)
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))

    def test_payment_recap_pdf_renders(self):
        pdf_bytes = render_payment_recap_pdf_bytes(self.period)
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))

    def test_calculation_recap_pdf_renders(self):
        pdf_bytes = render_calculation_recap_pdf_bytes(self.period)
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))
