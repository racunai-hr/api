"""Partner statement / kartica API tests (Faza 2)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import JournalEntry, SubledgerAllocation, SubledgerItem
from accounting.services.chart import provision_tenant_chart
from partners.models import Partner
from tenants.models import Tenant, TenantMembership

HOST = 'finstmt.racunai.hr'
CURRENT_YEAR = 2026


def _make_item(
    *,
    tenant,
    partner,
    direction: str,
    amount: Decimal,
    entry_date: date,
    created_by,
    source_object_id: int,
    entry_number: str,
) -> SubledgerItem:
    je = JournalEntry.all_objects.create(
        tenant=tenant,
        entry_number=entry_number,
        entry_date=entry_date,
        description='test',
        created_by=created_by,
    )
    ct = ContentType.objects.get_for_model(JournalEntry)
    return SubledgerItem.all_objects.create(
        tenant=tenant,
        partner=partner,
        direction=direction,
        source_content_type=ct,
        source_object_id=source_object_id,
        journal_entry=je,
        original_amount=amount,
        open_amount=amount,
        due_date=entry_date,
        status='open',
    )


def _make_allocation(
    *,
    tenant,
    item: SubledgerItem,
    amount: Decimal,
    entry_date: date,
    created_by,
    entry_number: str,
) -> SubledgerAllocation:
    je = JournalEntry.all_objects.create(
        tenant=tenant,
        entry_number=entry_number,
        entry_date=entry_date,
        description='payment',
        created_by=created_by,
    )
    return SubledgerAllocation.all_objects.create(
        tenant=tenant,
        subledger_item=item,
        journal_entry=je,
        amount=amount,
    )


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class FinancePartnerStatementApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif = __import__(
            'accounting.services.rrif_import',
            fromlist=['import_rrif_chart'],
        ).import_rrif_chart
        import_rrif(clear=True)

        cls.tenant = Tenant.objects.create(slug='finstmt', name='Finance Statement Co')
        provision_tenant_chart(cls.tenant)

        User = get_user_model()
        cls.owner = User.objects.create_user(username='fs-owner', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')

        cls.partner = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Statement Partner',
            partner_type='both',
            status='active',
            tax_number='77777777777',
            address='A',
            city='Zagreb',
            postal_code='10000',
        )

    def setUp(self):
        SubledgerAllocation.all_objects.filter(tenant=self.tenant).delete()
        SubledgerItem.all_objects.filter(tenant=self.tenant, partner=self.partner).delete()
        JournalEntry.all_objects.filter(tenant=self.tenant).delete()

    def _client(self):
        client = APIClient()
        token = RefreshToken.for_user(self.owner).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = HOST
        return client

    def _url(self, **params):
        base = f'/api/finance/partners/{self.partner.pk}/statement/'
        if not params:
            return base
        qs = '&'.join(f'{k}={v}' for k, v in params.items())
        return f'{base}?{qs}'

    @patch('domains.finance.services.statement.timezone.localdate')
    def test_ap_running_balance_chain(self, mock_localdate):
        mock_localdate.return_value = date(CURRENT_YEAR, 8, 23)
        prior = _make_item(
            tenant=self.tenant,
            partner=self.partner,
            direction='payable',
            amount=Decimal('1000.00'),
            entry_date=date(2025, 11, 15),
            created_by=self.owner,
            source_object_id=10_001,
            entry_number='JE-AP-PS',
        )
        expense = _make_item(
            tenant=self.tenant,
            partner=self.partner,
            direction='payable',
            amount=Decimal('500.00'),
            entry_date=date(CURRENT_YEAR, 2, 15),
            created_by=self.owner,
            source_object_id=10_002,
            entry_number='JE-AP-OBL',
        )
        _make_allocation(
            tenant=self.tenant,
            item=expense,
            amount=Decimal('500.00'),
            entry_date=date(CURRENT_YEAR, 3, 1),
            created_by=self.owner,
            entry_number='JE-AP-PAY',
        )
        prior.status = 'open'
        prior.save(update_fields=['status'])

        client = self._client()
        response = client.get(self._url(year=CURRENT_YEAR, direction='all'))
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data['year'], CURRENT_YEAR)
        balances = [row['balance'] for row in data['rows']]
        self.assertEqual(balances[0], '-1000.00')
        self.assertEqual(balances[1], '-1500.00')
        self.assertEqual(balances[2], '-1000.00')
        self.assertEqual(data['opening_balance']['balance'], '-1000.00')
        self.assertEqual(data['opening_balance']['debit'], '0.00')
        self.assertEqual(data['opening_balance']['credit'], '1000.00')
        obligation = next(r for r in data['rows'] if r['kind'] == 'obligation')
        self.assertEqual(obligation['debit'], '0.00')
        self.assertEqual(obligation['credit'], '500.00')
        allocation = next(r for r in data['rows'] if r['kind'] == 'allocation')
        self.assertEqual(allocation['debit'], '500.00')
        self.assertEqual(allocation['credit'], '0.00')

    @patch('domains.finance.services.statement.timezone.localdate')
    def test_ar_running_balance_chain(self, mock_localdate):
        mock_localdate.return_value = date(CURRENT_YEAR, 8, 23)
        _make_item(
            tenant=self.tenant,
            partner=self.partner,
            direction='receivable',
            amount=Decimal('1000.00'),
            entry_date=date(2025, 12, 1),
            created_by=self.owner,
            source_object_id=20_001,
            entry_number='JE-AR-PS',
        )
        invoice = _make_item(
            tenant=self.tenant,
            partner=self.partner,
            direction='receivable',
            amount=Decimal('500.00'),
            entry_date=date(CURRENT_YEAR, 2, 1),
            created_by=self.owner,
            source_object_id=20_002,
            entry_number='JE-AR-OBL',
        )
        _make_allocation(
            tenant=self.tenant,
            item=invoice,
            amount=Decimal('500.00'),
            entry_date=date(CURRENT_YEAR, 2, 20),
            created_by=self.owner,
            entry_number='JE-AR-PAY',
        )

        client = self._client()
        response = client.get(self._url(year=CURRENT_YEAR, direction='receivable'))
        self.assertEqual(response.status_code, 200)
        balances = [row['balance'] for row in response.data['rows']]
        self.assertEqual(balances, ['1000.00', '1500.00', '1000.00'])

    @patch('domains.finance.services.statement.timezone.localdate')
    def test_carry_forward_year_without_events(self, mock_localdate):
        mock_localdate.return_value = date(CURRENT_YEAR, 8, 23)
        _make_item(
            tenant=self.tenant,
            partner=self.partner,
            direction='payable',
            amount=Decimal('1000.00'),
            entry_date=date(2025, 6, 1),
            created_by=self.owner,
            source_object_id=30_001,
            entry_number='JE-CF',
        )

        client = self._client()
        response = client.get(self._url(year=CURRENT_YEAR))
        self.assertEqual(response.status_code, 200)
        self.assertIn(CURRENT_YEAR, response.data['available_years'])
        self.assertNotIn(2024, response.data['available_years'])
        self.assertEqual(len(response.data['rows']), 1)
        self.assertEqual(response.data['rows'][0]['kind'], 'opening_balance')
        self.assertEqual(response.data['rows'][0]['balance'], '-1000.00')

    @patch('domains.finance.services.statement.timezone.localdate')
    def test_carry_forward_does_not_generate_future_years(self, mock_localdate):
        mock_localdate.return_value = date(CURRENT_YEAR, 8, 23)
        _make_item(
            tenant=self.tenant,
            partner=self.partner,
            direction='payable',
            amount=Decimal('1000.00'),
            entry_date=date(2020, 1, 1),
            created_by=self.owner,
            source_object_id=40_001,
            entry_number='JE-OLD',
        )
        client = self._client()
        response = client.get(self._url())
        years = response.data['available_years']
        self.assertTrue(all(y <= CURRENT_YEAR for y in years))
        self.assertNotIn(CURRENT_YEAR + 1, years)

    @patch('domains.finance.services.statement.timezone.localdate')
    def test_default_year_from_api(self, mock_localdate):
        mock_localdate.return_value = date(CURRENT_YEAR, 8, 23)
        _make_item(
            tenant=self.tenant,
            partner=self.partner,
            direction='payable',
            amount=Decimal('100.00'),
            entry_date=date(2025, 3, 1),
            created_by=self.owner,
            source_object_id=50_001,
            entry_number='JE-DEF',
        )
        client = self._client()
        response = client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['year'], CURRENT_YEAR)

    @patch('domains.finance.services.statement.timezone.localdate')
    def test_direction_filter_excludes_other_side(self, mock_localdate):
        mock_localdate.return_value = date(CURRENT_YEAR, 8, 23)
        _make_item(
            tenant=self.tenant,
            partner=self.partner,
            direction='receivable',
            amount=Decimal('100.00'),
            entry_date=date(CURRENT_YEAR, 1, 10),
            created_by=self.owner,
            source_object_id=60_001,
            entry_number='JE-AR',
        )
        _make_item(
            tenant=self.tenant,
            partner=self.partner,
            direction='payable',
            amount=Decimal('200.00'),
            entry_date=date(CURRENT_YEAR, 1, 20),
            created_by=self.owner,
            source_object_id=60_002,
            entry_number='JE-AP',
        )
        client = self._client()
        response = client.get(self._url(year=CURRENT_YEAR, direction='receivable'))
        kinds = [r['kind'] for r in response.data['rows'] if r['kind'] != 'opening_balance']
        self.assertEqual(len(kinds), 1)
        row = next(r for r in response.data['rows'] if r['kind'] == 'obligation')
        self.assertEqual(row['direction'], 'receivable')

    @patch('domains.finance.services.statement.timezone.localdate')
    def test_same_date_sorted_by_journal_entry_pk(self, mock_localdate):
        mock_localdate.return_value = date(CURRENT_YEAR, 8, 23)
        same_day = date(CURRENT_YEAR, 5, 10)
        item = _make_item(
            tenant=self.tenant,
            partner=self.partner,
            direction='payable',
            amount=Decimal('300.00'),
            entry_date=same_day,
            created_by=self.owner,
            source_object_id=70_001,
            entry_number='JE-OBL-EARLY',
        )
        pay_je = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='JE-PAY-LATE',
            entry_date=same_day,
            description='payment',
            created_by=self.owner,
        )
        SubledgerAllocation.all_objects.create(
            tenant=self.tenant,
            subledger_item=item,
            journal_entry=pay_je,
            amount=Decimal('300.00'),
        )
        client = self._client()
        response = client.get(self._url(year=CURRENT_YEAR, direction='payable'))
        event_rows = [r for r in response.data['rows'] if r['kind'] != 'opening_balance']
        self.assertEqual(event_rows[0]['kind'], 'obligation')
        self.assertEqual(event_rows[1]['kind'], 'allocation')
        self.assertLess(
            event_rows[0]['journal_entry_id'],
            event_rows[1]['journal_entry_id'],
        )

    def test_missing_partner_404(self):
        client = self._client()
        response = client.get('/api/finance/partners/999999/statement/')
        self.assertEqual(response.status_code, 404)
