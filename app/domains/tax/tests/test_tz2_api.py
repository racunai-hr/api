"""TZ2 year API — Alma 2026 draft + unsigned XML contract."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from lxml import etree
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from settings.models import CompanySettings, ResponsiblePerson, TaxOffice
from tenants.models import Tenant, TenantMembership

HOST = 'almatz2api.racunai.hr'
_TZ2_NS = 'http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacTZ2/v1-0'


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class Tz2YearApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='almatz2api', name='Alma Čižmić')
        User = get_user_model()
        cls.accountant = User.objects.create_user(username='tz2-api-accountant', password='test')
        TenantMembership.objects.create(user=cls.accountant, tenant=cls.tenant, role='accountant')
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        settings = CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Alma Čižmić',
            street='Ulica bribirskih knezova',
            house_number='9',
            postal_code='22211',
            city='Vodice',
            vat_number='20501805574',
            tax_office=tax_office,
        )
        ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Ante',
            last_name='Vrcan',
        )

    def _auth_client(self):
        client = APIClient()
        token = RefreshToken.for_user(self.accountant).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = HOST
        return client

    def test_alma_2026_draft_and_unsigned_xml(self):
        client = self._auth_client()
        draft = client.get('/api/tax/tz2/years/2026/')
        self.assertEqual(draft.status_code, 200, draft.content)
        body = draft.json()
        self.assertEqual(body['tax_year'], 2026)
        self.assertFalse(body['persisted'])
        self.assertEqual(body['room_beds'], 10)
        self.assertEqual(body['aux_beds'], 0)
        self.assertEqual(body['ep_receipts'], '17360.00')
        self.assertEqual(body['installment_flag'], '1')
        self.assertEqual(body['installment_amount'], '19.90')
        self.assertEqual(body['amount_after_discount'], '59.70')
        self.assertEqual(body['taxpayer']['first_name'], 'ALMA')
        self.assertEqual(body['taxpayer']['last_name'], 'ČIZMIĆ')
        self.assertEqual(body['taxpayer']['municipality_code'], '500')
        self.assertEqual(body['taxpayer']['house_number'], '0009')
        self.assertEqual(body['rates']['room_bed_rate'], '5.97')

        saved = client.put(
            '/api/tax/tz2/years/2026/',
            {
                'first_name': body['taxpayer']['first_name'],
                'last_name': body['taxpayer']['last_name'],
                'oib': body['taxpayer']['oib'],
                'municipality_code': body['taxpayer']['municipality_code'],
                'city': body['taxpayer']['city'],
                'street': body['taxpayer']['street'],
                'house_number': body['taxpayer']['house_number'],
                'room_beds': body['room_beds'],
                'aux_beds': body['aux_beds'],
                'payment_installments': True,
                'ep_receipts': body['ep_receipts'],
            },
            format='json',
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertTrue(saved.json()['persisted'])

        xml = client.get('/api/tax/tz2/years/2026/xml/')
        self.assertEqual(xml.status_code, 200, xml.content)
        root = etree.fromstring(xml.content)
        self.assertEqual(root.tag, f'{{{_TZ2_NS}}}ObrazacTZ2')
        self.assertEqual(root.get('verzijaSheme'), '1.0')
        self.assertEqual(root.find(f'{{{_TZ2_NS}}}Tijelo/{{{_TZ2_NS}}}Podatak01').text, '10')
        self.assertEqual(root.find(f'{{{_TZ2_NS}}}Tijelo/{{{_TZ2_NS}}}Podatak17').text, '1.99')
        self.assertEqual(root.find(f'{{{_TZ2_NS}}}Tijelo/{{{_TZ2_NS}}}Podatak34').text, '1')
        self.assertEqual(root.find(f'{{{_TZ2_NS}}}Zaglavlje/{{{_TZ2_NS}}}Obveznik/{{{_TZ2_NS}}}Adresa/{{{_TZ2_NS}}}SifraOpcine').text, '500')
        self.assertEqual(root.find(f'{{{_TZ2_NS}}}Zaglavlje/{{{_TZ2_NS}}}Obveznik/{{{_TZ2_NS}}}Adresa/{{{_TZ2_NS}}}Broj').text, '0009')
