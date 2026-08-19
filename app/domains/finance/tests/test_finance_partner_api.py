"""Finance partner read API tests (ADR-0022)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import JournalEntry, SubledgerItem
from accounting.services.chart import provision_tenant_chart
from partners.models import Partner
from tenants.models import Tenant, TenantMembership

HOST = 'finpart.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class FinancePartnerApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif = __import__(
            'accounting.services.rrif_import',
            fromlist=['import_rrif_chart'],
        ).import_rrif_chart
        import_rrif(clear=True)

        cls.tenant = Tenant.objects.create(slug='finpart', name='Finance Partner Co')
        provision_tenant_chart(cls.tenant)

        User = get_user_model()
        cls.owner = User.objects.create_user(username='fp-owner', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')

        cls.partner = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Fin Partner',
            partner_type='both',
            status='active',
            tax_number='99999999999',
            address='A',
            city='Zagreb',
            postal_code='10000',
        )
        cls.other = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Other',
            partner_type='customer',
            status='active',
            tax_number='88888888888',
            address='B',
            city='Zagreb',
            postal_code='10000',
        )

        je = JournalEntry.all_objects.create(
            tenant=cls.tenant,
            entry_number='JE-FP-1',
            entry_date=date.today(),
            description='test',
            created_by=cls.owner,
        )
        ct = ContentType.objects.get_for_model(JournalEntry)
        today = date.today()
        SubledgerItem.all_objects.create(
            tenant=cls.tenant,
            partner=cls.partner,
            direction='receivable',
            source_content_type=ct,
            source_object_id=je.pk,
            journal_entry=je,
            original_amount=Decimal('100.00'),
            open_amount=Decimal('100.00'),
            due_date=today - timedelta(days=10),
            status='open',
        )
        SubledgerItem.all_objects.create(
            tenant=cls.tenant,
            partner=cls.partner,
            direction='payable',
            source_content_type=ct,
            source_object_id=je.pk + 10_000,
            journal_entry=je,
            original_amount=Decimal('40.00'),
            open_amount=Decimal('40.00'),
            due_date=today + timedelta(days=5),
            status='open',
        )

    def _client(self, user):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = HOST
        return client

    def test_financial_summary(self):
        client = self._client(self.owner)
        response = client.get(f'/api/finance/partners/{self.partner.pk}/financial-summary/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['receivables_open'], '100.00')
        self.assertEqual(response.data['payables_open'], '40.00')
        self.assertEqual(response.data['receivables_overdue'], '100.00')
        self.assertEqual(response.data['payables_overdue'], '0.00')
        self.assertEqual(response.data['net_balance'], '60.00')
        self.assertEqual(response.data['currency'], 'EUR')

    def test_subledger_scoped_to_partner(self):
        client = self._client(self.owner)
        response = client.get(f'/api/finance/partners/{self.partner.pk}/subledger/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)
        empty = client.get(f'/api/finance/partners/{self.other.pk}/subledger/')
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.data['count'], 0)

    def test_missing_partner_404(self):
        client = self._client(self.owner)
        response = client.get('/api/finance/partners/999999/financial-summary/')
        self.assertEqual(response.status_code, 404)
