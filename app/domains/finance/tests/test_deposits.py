"""Acceptance tests for Deposit / kaucija (ADR-0024)."""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import Deposit, JournalEntry, JournalEntryLine, VATLedgerEntry
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from domains.finance.services.deposits import (
    create_deposit,
    post_deposit,
    return_deposit,
    reverse_deposit,
)
from domains.finance.services.subledger import get_subledger_item_for_source
from partners.models import Partner
from payments.models import BankAccount
from tenants.models import Tenant, TenantMembership

HOST = 'deposit.racunai.hr'
OTHER_HOST = 'deposit2.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class DepositLifecycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='deposit', name='Deposit Co')
        cls.other = Tenant.objects.create(slug='deposit2', name='Other')
        provision_tenant_chart(cls.tenant)
        provision_tenant_chart(cls.other)
        User = get_user_model()
        cls.owner = User.objects.create_user(username='dep-owner', password='test')
        cls.viewer = User.objects.create_user(username='dep-viewer', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')
        cls.partner = Partner.all_objects.create(
            tenant=cls.tenant,
            name='SaM Automobile',
            tax_number='',
            vat_number='DE355497142',
            partner_type='supplier',
            status='active',
            address='Saegmuehlweg 4',
            city='Sinsheim',
            postal_code='74889',
            country_code='DE',
            country='Njemačka',
        )
        cls.bank = BankAccount.all_objects.create(
            tenant=cls.tenant,
            account_name='Žiro',
            bank_name='PBZ',
            account_number='1',
            iban='HR1210010051863000160',
            currency='EUR',
            status='active',
        )
        cls.other_bank = BankAccount.all_objects.create(
            tenant=cls.other,
            account_name='Other',
            bank_name='X',
            account_number='2',
            currency='EUR',
            status='active',
        )

    def setUp(self):
        self.client = self._client(self.owner)

    def _client(self, user, host=HOST):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = host
        return client

    def _create(self, **overrides):
        body = {
            'partner_id': self.partner.pk,
            'amount': '5000.00',
            'currency': 'EUR',
            'deposit_date': '2026-07-30',
            'reference': 'SaM kaucija',
        }
        body.update(overrides)
        return self.client.post('/api/finance/deposits/', body, format='json')

    def test_viewer_cannot_create(self):
        response = self._client(self.viewer).post(
            '/api/finance/deposits/',
            {
                'partner_id': self.partner.pk,
                'amount': '100.00',
                'deposit_date': '2026-07-30',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 404)

    def test_post_given_creates_je_and_open_subledger(self):
        created = self._create()
        self.assertEqual(created.status_code, 201, created.data)
        deposit_id = created.data['id']
        posted = self.client.post(
            f'/api/finance/deposits/{deposit_id}/post/',
            {},
            format='json',
            HTTP_IDEMPOTENCY_KEY='post-1',
        )
        self.assertEqual(posted.status_code, 200, posted.data)
        self.assertEqual(posted.data['workflow_status'], 'open')
        self.assertEqual(posted.data['operational_status'], 'open')
        self.assertEqual(posted.data['open_amount'], '5000.00')

        deposit = Deposit.all_objects.get(pk=deposit_id)
        entry = deposit.given_journal_entry
        self.assertIsNotNone(entry)
        self.assertEqual(entry.status, 'posted')
        lines = list(JournalEntryLine.objects.filter(journal_entry=entry))
        codes = {(line.account.account_code or line.account.rrif_code, line.debit_amount, line.credit_amount) for line in lines}
        self.assertTrue(any(c[0].startswith('1152') and c[1] == Decimal('5000.00') for c in codes))
        self.assertTrue(any(c[0].startswith('1020') and c[2] == Decimal('5000.00') for c in codes))
        self.assertFalse(
            VATLedgerEntry.all_objects.filter(
                source_content_type=ContentType.objects.get_for_model(Deposit),
                source_object_id=deposit_id,
            ).exists()
        )
        # no P&L
        for line in lines:
            code = line.account.account_code or ''
            self.assertFalse(code.startswith('4') or code.startswith('7'))

    def test_full_return_closes_subledger(self):
        created = self._create()
        deposit_id = created.data['id']
        self.client.post(
            f'/api/finance/deposits/{deposit_id}/post/',
            {},
            format='json',
            HTTP_IDEMPOTENCY_KEY='p2',
        )
        returned = self.client.post(
            f'/api/finance/deposits/{deposit_id}/return/',
            {'return_bank_account_id': self.bank.pk, 'return_date': '2026-08-15'},
            format='json',
            HTTP_IDEMPOTENCY_KEY='r1',
        )
        self.assertEqual(returned.status_code, 200, returned.data)
        self.assertEqual(returned.data['workflow_status'], 'returned')
        self.assertEqual(returned.data['operational_status'], 'closed')
        self.assertEqual(returned.data['open_amount'], '0.00')

    def test_partial_return_rejected(self):
        created = self._create()
        deposit_id = created.data['id']
        self.client.post(
            f'/api/finance/deposits/{deposit_id}/post/',
            {},
            format='json',
            HTTP_IDEMPOTENCY_KEY='p3',
        )
        bad = self.client.post(
            f'/api/finance/deposits/{deposit_id}/return/',
            {'return_bank_account_id': self.bank.pk, 'amount': '2000.00'},
            format='json',
            HTTP_IDEMPOTENCY_KEY='r-partial',
        )
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.data['code'], 'full_return_required')
        deposit = Deposit.all_objects.get(pk=deposit_id)
        self.assertEqual(deposit.status, 'open')
        self.assertIsNone(deposit.return_journal_entry_id)

    def test_return_on_draft_rejected(self):
        created = self._create()
        bad = self.client.post(
            f"/api/finance/deposits/{created.data['id']}/return/",
            {'return_bank_account_id': self.bank.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='r-draft',
        )
        self.assertEqual(bad.status_code, 409)

    def test_foreign_bank_rejected(self):
        created = self._create()
        deposit_id = created.data['id']
        self.client.post(
            f'/api/finance/deposits/{deposit_id}/post/',
            {},
            format='json',
            HTTP_IDEMPOTENCY_KEY='p4',
        )
        bad = self.client.post(
            f'/api/finance/deposits/{deposit_id}/return/',
            {'return_bank_account_id': self.other_bank.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='r-x',
        )
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.data['code'], 'return_bank_required')

    def test_currency_mismatch_rejected(self):
        created = self._create()
        deposit_id = created.data['id']
        self.client.post(
            f'/api/finance/deposits/{deposit_id}/post/',
            {},
            format='json',
            HTTP_IDEMPOTENCY_KEY='p5',
        )
        chf_bank = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='CHF',
            bank_name='X',
            account_number='9',
            currency='CHF',
            status='active',
        )
        bad = self.client.post(
            f'/api/finance/deposits/{deposit_id}/return/',
            {'return_bank_account_id': chf_bank.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='r-chf',
        )
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.data['code'], 'currency_mismatch')

    def test_reverse_open_cancels_subledger(self):
        created = self._create()
        deposit_id = created.data['id']
        self.client.post(
            f'/api/finance/deposits/{deposit_id}/post/',
            {},
            format='json',
            HTTP_IDEMPOTENCY_KEY='p6',
        )
        reversed_resp = self.client.post(
            f'/api/finance/deposits/{deposit_id}/reverse/',
            {},
            format='json',
            HTTP_IDEMPOTENCY_KEY='rev1',
        )
        self.assertEqual(reversed_resp.status_code, 200, reversed_resp.data)
        self.assertEqual(reversed_resp.data['workflow_status'], 'reversed')
        deposit = Deposit.all_objects.get(pk=deposit_id)
        item = get_subledger_item_for_source(self.tenant, deposit)
        self.assertTrue(item is None or item.status == 'cancelled')
        self.assertEqual(deposit.given_journal_entry.status, 'reversed')

    def test_second_return_idempotent(self):
        created = self._create()
        deposit_id = created.data['id']
        self.client.post(
            f'/api/finance/deposits/{deposit_id}/post/',
            {},
            format='json',
            HTTP_IDEMPOTENCY_KEY='p7',
        )
        first = self.client.post(
            f'/api/finance/deposits/{deposit_id}/return/',
            {'return_bank_account_id': self.bank.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='r2',
        )
        second = self.client.post(
            f'/api/finance/deposits/{deposit_id}/return/',
            {'return_bank_account_id': self.bank.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='r3',
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        deposit = Deposit.all_objects.get(pk=deposit_id)
        self.assertEqual(
            JournalEntry.all_objects.filter(
                source_content_type=ContentType.objects.get_for_model(Deposit),
                source_object_id=deposit_id,
                description__startswith='[deposit_returned:',
            ).count(),
            1,
        )

    def test_cross_tenant_404(self):
        created = self._create()
        other = self._client(self.owner, host=OTHER_HOST)
        # owner not member of other tenant → 404
        User = get_user_model()
        outsider = User.objects.create_user(username='outsider', password='test')
        TenantMembership.objects.create(user=outsider, tenant=self.other, role='owner')
        client = self._client(outsider, host=OTHER_HOST)
        response = client.get(f"/api/finance/deposits/{created.data['id']}/")
        self.assertEqual(response.status_code, 404)

    def test_document_list_includes_deposit(self):
        created = self._create()
        deposit_id = created.data['id']
        self.client.post(
            f'/api/finance/deposits/{deposit_id}/post/',
            {},
            format='json',
            HTTP_IDEMPOTENCY_KEY='p-doc',
        )
        response = self.client.get('/api/documents/?direction=deposit')
        self.assertEqual(response.status_code, 200, response.data)
        ids = [row['id'] for row in response.data['results']]
        self.assertIn(deposit_id, ids)
        row = next(r for r in response.data['results'] if r['id'] == deposit_id)
        self.assertEqual(row['kind'], 'deposit')
        self.assertEqual(row['direction'], 'deposit')
        self.assertEqual(row['operational_status']['value'], 'open')
        self.assertEqual(row['amounts']['vat'], '0.00')
        detail = self.client.get(f'/api/documents/deposit/{deposit_id}/')
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['kind'], 'deposit')


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    SECURE_SSL_REDIRECT=False,
)
class DepositConcurrencyTests(TransactionTestCase):
    def setUp(self):
        import_rrif_chart(clear=True)
        self.tenant = Tenant.objects.create(slug='dep-race', name='Race Co')
        provision_tenant_chart(self.tenant)
        User = get_user_model()
        self.user = User.objects.create_user(username='race-user', password='test')
        TenantMembership.objects.create(user=self.user, tenant=self.tenant, role='owner')
        self.partner = Partner.all_objects.create(
            tenant=self.tenant,
            name='Partner',
            tax_number='',
            vat_number='DE123456789',
            partner_type='supplier',
            status='active',
            address='A',
            city='B',
            postal_code='1',
            country_code='DE',
            country='Njemačka',
        )
        self.bank = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Žiro',
            bank_name='B',
            account_number='1',
            currency='EUR',
            status='active',
        )

    def test_parallel_post_one_je(self):
        dto = create_deposit(
            tenant=self.tenant,
            data={
                'partner_id': self.partner.pk,
                'amount': '5000.00',
                'deposit_date': date(2026, 7, 30),
            },
            user=self.user,
        )
        deposit_id = dto['id']
        results = []

        def worker():
            from django.db import connection

            connection.close()
            results.append(post_deposit(tenant=self.tenant, deposit_id=deposit_id, user=self.user))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 2)
        deposit = Deposit.all_objects.get(pk=deposit_id)
        self.assertEqual(deposit.status, 'open')
        self.assertEqual(
            JournalEntry.all_objects.filter(
                source_content_type=ContentType.objects.get_for_model(Deposit),
                source_object_id=deposit_id,
                description__startswith='[deposit_given:',
                status='posted',
            ).count(),
            1,
        )

    def test_parallel_return_one_je(self):
        dto = create_deposit(
            tenant=self.tenant,
            data={
                'partner_id': self.partner.pk,
                'amount': '5000.00',
                'deposit_date': date(2026, 7, 30),
            },
            user=self.user,
        )
        post_deposit(tenant=self.tenant, deposit_id=dto['id'], user=self.user)
        results = []
        errors = []

        def worker():
            from django.db import connection

            connection.close()
            try:
                results.append(
                    return_deposit(
                        tenant=self.tenant,
                        deposit_id=dto['id'],
                        user=self.user,
                        data={'return_bank_account_id': self.bank.pk},
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(len(results) >= 1)
        deposit = Deposit.all_objects.get(pk=dto['id'])
        self.assertEqual(deposit.status, 'returned')
        self.assertEqual(
            JournalEntry.all_objects.filter(
                source_content_type=ContentType.objects.get_for_model(Deposit),
                source_object_id=dto['id'],
                description__startswith='[deposit_returned:',
            ).count(),
            1,
        )

    def test_return_reverse_race_one_wins(self):
        dto = create_deposit(
            tenant=self.tenant,
            data={
                'partner_id': self.partner.pk,
                'amount': '5000.00',
                'deposit_date': date(2026, 7, 30),
            },
            user=self.user,
        )
        post_deposit(tenant=self.tenant, deposit_id=dto['id'], user=self.user)
        outcomes = []

        def do_return():
            from django.db import connection

            connection.close()
            try:
                outcomes.append(
                    (
                        'return',
                        return_deposit(
                            tenant=self.tenant,
                            deposit_id=dto['id'],
                            user=self.user,
                            data={'return_bank_account_id': self.bank.pk},
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                outcomes.append(('return_err', exc))

        def do_reverse():
            from django.db import connection

            connection.close()
            try:
                outcomes.append(('reverse', reverse_deposit(tenant=self.tenant, deposit_id=dto['id'], user=self.user)))
            except Exception as exc:  # noqa: BLE001
                outcomes.append(('reverse_err', exc))

        t1 = threading.Thread(target=do_return)
        t2 = threading.Thread(target=do_reverse)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        deposit = Deposit.all_objects.get(pk=dto['id'])
        self.assertIn(deposit.status, ('returned', 'reversed'))
        # Exactly one terminal transition
        terminal = [o for o in outcomes if o[0] in ('return', 'reverse')]
        self.assertEqual(len(terminal), 1, outcomes)
