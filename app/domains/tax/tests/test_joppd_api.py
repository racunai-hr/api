"""JOPPD period API: aggregate from payroll (via domains.hr.facade), XML, manual submit."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import JOPPDReturn
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
from settings.models import CompanySettings, ResponsiblePerson
from tenants.models import Tenant, TenantMembership

HOST = 'joppdapi.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class JoppdPeriodApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='joppdapi', name='JOPPD API Co')
        User = get_user_model()
        cls.accountant = User.objects.create_user(username='joppdapi-accountant', password='test')
        TenantMembership.objects.create(user=cls.accountant, tenant=cls.tenant, role='accountant')

        settings = CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='FINE STAR D.O.O.',
            street='Bana Josipa Jelačića',
            house_number='58',
            postal_code='22000',
            city='Šibenik',
            vat_number='36619131370',
        )
        ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Toni',
            last_name='Šupe',
        )

        PayrollTaxYear.objects.get_or_create(year=2026, defaults={})
        MunicipalityIncomeTaxRate.objects.get_or_create(
            year=2026, code='04448',
            defaults={'name': 'ŠIBENIK', 'lower_rate': Decimal('20.00'), 'higher_rate': Decimal('30.00')},
        )

        cls.employee = Employee.objects.create(
            tenant=cls.tenant, first_name='Iva', last_name='Ivić', oib='33333333333',
            municipality_code='04448', city='Šibenik', street='Trg 1',
        )
        cls.contract = EmploymentContract.objects.create(
            tenant=cls.tenant, employee=cls.employee, start_date=date(2026, 1, 1),
            base_gross_salary=Decimal('1500.00'),
            recipient_code='0001', income_type_code='0001',
        )
        cls.period = PayrollPeriod.objects.create(
            tenant=cls.tenant, year=2026, month=1,
            calculation_date=date(2026, 1, 31), payment_date=date(2026, 2, 5),
        )
        cls.payslip = Payslip.objects.create(
            tenant=cls.tenant, period=cls.period, employee=cls.employee, contract=cls.contract,
            date_from=date(2026, 1, 1), date_to=date(2026, 1, 31), is_first_month=True,
        )
        PayslipEarningLine.objects.create(
            tenant=cls.tenant, payslip=cls.payslip, name='REDOVNI RAD',
            hours=Decimal('176.00'), gross_amount=Decimal('1500.00'),
        )
        calculate_payslip(cls.payslip)
        cls.period.status = PayrollPeriod.CALCULATED
        cls.period.save(update_fields=['status'])

    def _client(self):
        client = APIClient()
        token = RefreshToken.for_user(self.accountant).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = HOST
        return client

    def test_period_detail_aggregates_from_payroll(self):
        response = self._client().get('/api/tax/joppd/periods/2026-01/')
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['row_count'], 1)
        self.payslip.refresh_from_db()
        self.assertEqual(body['total_net'], str(self.payslip.net_salary))
        self.assertEqual(body['rows'][0]['recipient_oib'], '33333333333')
        self.assertIsNone(body['current_submission'])

    def test_unknown_period_is_404(self):
        response = self._client().get('/api/tax/joppd/periods/2026-02/')
        self.assertEqual(response.status_code, 404)

    def test_xml_contains_employee_and_totals(self):
        client = self._client()
        xml = client.get('/api/tax/joppd/periods/2026-01/xml/')
        self.assertEqual(xml.status_code, 200)
        self.assertIn(b'OIB>33333333333<', xml.content)
        self.assertIn('Prezime>Ivić<'.encode('utf-8'), xml.content)
        self.assertIn(b'UkupnoNeto>1080.00<', xml.content)
        self.assertIn(b'OznakaIzvjesca>26036<', xml.content)
        self.assertEqual(JOPPDReturn.all_objects.filter(tenant=self.tenant).count(), 0)

    @override_settings(JOPPD_SUBMIT_ENABLED=True)
    def test_draft_then_submit_records_submission(self):
        client = self._client()
        draft = client.post('/api/tax/joppd/periods/2026-01/draft/')
        self.assertEqual(draft.status_code, 200, draft.content)
        self.assertEqual(draft.json()['return_version'], 1)
        self.assertEqual(JOPPDReturn.all_objects.filter(tenant=self.tenant).count(), 1)

        submitted = client.post(
            '/api/tax/joppd/periods/2026-01/submit/',
            {
                'eporezna_identifier': str(uuid4()),
                'submitted_at': timezone.now().isoformat(),
            },
            format='json',
        )
        self.assertEqual(submitted.status_code, 200, submitted.content)

        detail = client.get('/api/tax/joppd/periods/2026-01/').json()
        self.assertEqual(detail['current_submission']['submission_no'], 1)
        self.assertEqual(JOPPDReturn.all_objects.filter(tenant=self.tenant).count(), 1)
