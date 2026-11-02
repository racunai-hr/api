"""HR/payroll JWT API: employees, contracts, periods, calculate/post, PDFs."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import ChartOfAccounts, RRIFChartEntry
from accounting.services.posting import ensure_default_posting_rules
from accounting.services.rrif_import import import_rrif_chart
from payroll.models import MunicipalityIncomeTaxRate, PayrollTaxYear
from settings.models import CompanySettings
from tenants.models import Tenant, TenantMembership

HOST = 'hrapi.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class HrApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        if not RRIFChartEntry.objects.exists():
            import_rrif_chart()

        cls.tenant = Tenant.objects.create(slug='hrapi', name='HR API Co')
        User = get_user_model()
        cls.accountant = User.objects.create_user(username='hrapi-accountant', password='test')
        cls.viewer = User.objects.create_user(username='hrapi-viewer', password='test')
        TenantMembership.objects.create(user=cls.accountant, tenant=cls.tenant, role='accountant')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')

        CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='HR API CO D.O.O.',
            street='Test',
            house_number='1',
            postal_code='10000',
            city='Zagreb',
            vat_number='12345678901',
        )
        ensure_default_posting_rules(cls.tenant)

        PayrollTaxYear.objects.get_or_create(year=2026, defaults={})
        MunicipalityIncomeTaxRate.objects.get_or_create(
            year=2026, code='00001',
            defaults={'name': 'ZAGREB', 'lower_rate': Decimal('20.00'), 'higher_rate': Decimal('30.00')},
        )
        # ChartOfAccounts fixture not strictly needed unless we call /post/,
        # which this test does exercise below.
        assert ChartOfAccounts  # keep import used for clarity in future edits

    def _client(self, user):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = HOST
        return client

    def test_employee_crud_and_contract(self):
        client = self._client(self.accountant)

        create = client.post(
            '/api/hr/employees/',
            {'first_name': 'Iva', 'last_name': 'Ivić', 'oib': '11111111111', 'municipality_code': '00001'},
            format='json',
        )
        self.assertEqual(create.status_code, 201, create.content)
        employee_id = create.json()['id']
        self.assertIsNone(create.json()['active_contract'])

        contract = client.post(
            f'/api/hr/employees/{employee_id}/contracts/',
            {'start_date': '2026-01-01', 'base_gross_salary': '1500.00'},
            format='json',
        )
        self.assertEqual(contract.status_code, 201, contract.content)

        detail = client.get(f'/api/hr/employees/{employee_id}/')
        self.assertEqual(detail.status_code, 200)
        self.assertIsNotNone(detail.json()['active_contract'])
        self.assertEqual(len(detail.json()['contracts']), 1)

        patch = client.patch(f'/api/hr/employees/{employee_id}/', {'city': 'Zagreb'}, format='json')
        self.assertEqual(patch.status_code, 200)
        self.assertEqual(patch.json()['city'], 'Zagreb')

    def test_viewer_cannot_create_employee(self):
        response = self._client(self.viewer).post(
            '/api/hr/employees/',
            {'first_name': 'X', 'last_name': 'Y', 'oib': '22222222222'},
            format='json',
        )
        self.assertEqual(response.status_code, 404)  # 404 by design for authenticated-but-forbidden

    def test_full_period_lifecycle_generate_calculate_post(self):
        client = self._client(self.accountant)
        emp = client.post(
            '/api/hr/employees/',
            {'first_name': 'Marko', 'last_name': 'Marić', 'oib': '33333333333', 'municipality_code': '00001'},
            format='json',
        ).json()
        client.post(
            f'/api/hr/employees/{emp["id"]}/contracts/',
            {'start_date': '2026-01-01', 'base_gross_salary': '1500.00'},
            format='json',
        )

        period = client.post(
            '/api/hr/payroll-periods/',
            {'year': 2026, 'month': 1, 'calculation_date': '2026-01-31', 'payment_date': '2026-02-05'},
            format='json',
        )
        self.assertEqual(period.status_code, 201, period.content)
        period_id = period.json()['id']

        generated = client.post(f'/api/hr/payroll-periods/{period_id}/generate/')
        self.assertEqual(generated.status_code, 200, generated.content)
        self.assertEqual(generated.json()['employee_count'], 1)

        calculated = client.post(f'/api/hr/payroll-periods/{period_id}/calculate/')
        self.assertEqual(calculated.status_code, 200, calculated.content)
        self.assertEqual(calculated.json()['status'], 'calculated')
        self.assertNotEqual(calculated.json()['net_total'], '0.00')

        payslip_id = calculated.json()['payslips'][0]['id']
        payslip_detail = client.get(f'/api/hr/payslips/{payslip_id}/')
        self.assertEqual(payslip_detail.status_code, 200)
        self.assertEqual(payslip_detail.json()['earning_lines'][0]['name'], 'REDOVNI RAD')

        payslip_pdf = client.get(f'/api/hr/payslips/{payslip_id}/pdf/')
        self.assertEqual(payslip_pdf.status_code, 200)
        self.assertTrue(payslip_pdf.content.startswith(b'%PDF'))

        posted = client.post(f'/api/hr/payroll-periods/{period_id}/post/')
        self.assertEqual(posted.status_code, 200, posted.content)
        self.assertEqual(posted.json()['status'], 'posted')

        journal_pdf = client.get(f'/api/hr/payroll-periods/{period_id}/journal-pdf/')
        self.assertEqual(journal_pdf.status_code, 200)
        self.assertTrue(journal_pdf.content.startswith(b'%PDF'))

        recap_pdf = client.get(f'/api/hr/payroll-periods/{period_id}/payment-recap-pdf/')
        self.assertEqual(recap_pdf.status_code, 200)
        self.assertTrue(recap_pdf.content.startswith(b'%PDF'))

        # Re-posting an already-posted period is idempotent, not an error.
        posted_again = client.post(f'/api/hr/payroll-periods/{period_id}/post/')
        self.assertEqual(posted_again.status_code, 200, posted_again.content)

    def test_calculate_without_payslips_is_400(self):
        client = self._client(self.accountant)
        period = client.post(
            '/api/hr/payroll-periods/',
            {'year': 2026, 'month': 2, 'calculation_date': '2026-02-28', 'payment_date': '2026-03-05'},
            format='json',
        ).json()
        response = client.post(f'/api/hr/payroll-periods/{period["id"]}/calculate/')
        self.assertEqual(response.status_code, 400)
