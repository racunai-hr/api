"""ZP period API: EU services from box 104, XML and manual submit."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import VATLedgerEntry, VATPeriod, ZPReturn
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice
from tenants.models import Tenant, TenantMembership

HOST = 'zpapi.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class ZpPeriodApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='zpapi', name='ZP API Co')
        User = get_user_model()
        cls.accountant = User.objects.create_user(username='zpapi-accountant', password='test')
        TenantMembership.objects.create(user=cls.accountant, tenant=cls.tenant, role='accountant')
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        settings = CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='FINE STAR D.O.O.',
            street='Bana Josipa Jelačića',
            house_number='58',
            postal_code='22000',
            city='Šibenik',
            vat_number='36619131370',
            tax_office=tax_office,
        )
        ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Toni',
            last_name='Šupe',
        )
        cls.period = VATPeriod.all_objects.create(
            tenant=cls.tenant,
            year=2026,
            month=9,
            status='open',
        )
        VATLedgerEntry.all_objects.create(
            tenant=cls.tenant,
            vat_period=cls.period,
            ledger_type=VATLedgerEntry.LEDGER_I_RA,
            entry_date=date(2026, 9, 21),
            document_number='2026-0003',
            partner_name='PRIMELEGACY S.L.',
            partner_oib='ESB75947309',
            base_amount=Decimal('10000.00'),
            vat_rate=Decimal('0.00'),
            vat_amount=Decimal('0.00'),
            vat_box='104',
        )

    def _client(self):
        client = APIClient()
        token = RefreshToken.for_user(self.accountant).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = HOST
        return client

    def test_spanish_service_is_zp_services_column(self):
        response = self._client().get('/api/tax/zp/periods/2026-09/')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['row_count'], 1)
        self.assertEqual(body['total_goods'], '0.00')
        self.assertEqual(body['total_services'], '10000.00')
        self.assertEqual(body['rows'][0]['country_code'], 'ES')
        self.assertEqual(body['rows'][0]['pdv_id'], 'B75947309')
        self.assertEqual(body['rows'][0]['goods_value'], '0.00')
        self.assertEqual(body['rows'][0]['services_value'], '10000.00')
        self.assertIsNone(body['current_submission'])

    def test_xml_and_submit_record_the_same_services_total(self):
        client = self._client()
        xml = client.get('/api/tax/zp/periods/2026-09/xml/')
        self.assertEqual(xml.status_code, 200)
        self.assertIn(b'PDVID>B75947309<', xml.content)
        self.assertIn(b'KodDrzave>ES<', xml.content)
        self.assertIn(b'I4>10000.00<', xml.content)
        self.assertEqual(ZPReturn.all_objects.filter(vat_period=self.period).count(), 0)

        submitted = client.post(
            '/api/tax/zp/periods/2026-09/submit/',
            {
                'eporezna_identifier': str(uuid4()),
                'submitted_at': timezone.now().isoformat(),
            },
            format='json',
        )
        self.assertEqual(submitted.status_code, 200, submitted.content)
        detail = client.get('/api/tax/zp/periods/2026-09/').json()
        self.assertEqual(detail['current_submission']['submission_no'], 1)
        self.assertEqual(ZPReturn.all_objects.filter(vat_period=self.period).count(), 1)
