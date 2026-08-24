"""ADR-0029 OfficialDocument posting — A+B Knjiži, profile lock, symmetric guard."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import (
    AssetJournalLinkRole,
    ChartOfAccounts,
    DepreciationMethod,
    FixedAsset,
    FixedAssetJournalLink,
    FixedAssetOrigin,
    FixedAssetStatus,
    JournalEntry,
    OfficialDocument,
    OfficialDocumentPostingProfile,
    SubledgerItem,
)
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import (
    OFFICIAL_DOCUMENT_POSTED,
    PostingEventSourceMismatch,
    build_document_posting_plan,
    ensure_default_posting_rules,
    post_document,
)
from accounting.services.rrif_import import import_rrif_chart
from expenses.models import Expense, ExpenseCategory, ExpensePostingProfile
from partners.models import Partner
from tenants.models import Tenant, TenantMembership

HOST = 'official-post.racunai.hr'
PDF_BYTES = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n'


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class OfficialDocumentPostingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='official-post', name='Official Post Co')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.owner = User.objects.create_user(username='off-post-owner', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        cls.issuer = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Carinska uprava',
            tax_number='18683136487',
            partner_type='supplier',
            status='active',
            country_code='HR',
            country='Hrvatska',
        )
        cls.account_0373 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='0373')
        cls.asset = FixedAsset.all_objects.create(
            tenant=cls.tenant,
            name='Audi A8',
            inventory_number='OS-A8',
            status=FixedAssetStatus.IN_PREPARATION,
            origin=FixedAssetOrigin.PURCHASE,
            acquisition_cost=Decimal('80000.00'),
            purchase_date=date(2026, 8, 1),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=cls.account_0373,
            asset_account=ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='032001'),
            accumulated_depreciation_account=ChartOfAccounts.all_objects.get(
                tenant=cls.tenant, account_code='0393'
            ),
            depreciation_expense_account=ChartOfAccounts.all_objects.get(
                tenant=cls.tenant, account_code='4314'
            ),
        )
        cls.ppmv = OfficialDocumentPostingProfile.all_objects.get(
            tenant=cls.tenant,
            code='ppmv_vehicle_acquisition',
        )
        cls.admin_fee = OfficialDocumentPostingProfile.all_objects.get(
            tenant=cls.tenant,
            code='administrative_fee',
        )

    def setUp(self):
        self.client = APIClient()
        token = RefreshToken.for_user(self.owner).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.client.defaults['HTTP_HOST'] = HOST
        self.client.defaults['HTTP_IDEMPOTENCY_KEY'] = 'official-post-1'

    def _pdf(self):
        return SimpleUploadedFile('rjesnje.pdf', PDF_BYTES, content_type='application/pdf')

    def _create_registered(self, **overrides):
        body = {
            'official_kind': 'tax_decision',
            'issuer_id': self.issuer.pk,
            'document_number': overrides.pop('document_number', 'UP/I-410-22/26-09/49557'),
            'issue_date': '2026-08-03',
            'due_date': '2026-08-18',
            'amount': '10347.20',
            'currency': 'EUR',
            'related_fixed_asset_id': self.asset.pk,
            'register': 'true',
            'file': self._pdf(),
        }
        body.update(overrides)
        response = self.client.post('/api/finance/official-documents/', body, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def _set_profile(self, doc_id, profile_id):
        return self.client.post(
            f'/api/finance/official-documents/{doc_id}/posting-profile/',
            {'posting_profile_id': profile_id},
            format='json',
        )

    def _post(self, doc_id, *, key='official-post-1'):
        return self.client.post(
            f'/api/finance/official-documents/{doc_id}/post/',
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def _marker_entries(self, document_id):
        ct = ContentType.objects.get_for_model(OfficialDocument)
        return list(
            JournalEntry.all_objects.filter(
                tenant=self.tenant,
                source_content_type=ct,
                source_object_id=document_id,
                description__startswith=f'[{OFFICIAL_DOCUMENT_POSTED}]',
                status='posted',
            )
        )

    def _payables(self, document_id):
        ct = ContentType.objects.get_for_model(OfficialDocument)
        return list(
            SubledgerItem.all_objects.filter(
                tenant=self.tenant,
                source_content_type=ct,
                source_object_id=document_id,
            ).exclude(status='cancelled')
        )

    def _asset_links(self, *, asset=None, entry=None):
        qs = FixedAssetJournalLink.all_objects.filter(tenant=self.tenant)
        if asset is not None:
            qs = qs.filter(fixed_asset=asset)
        if entry is not None:
            qs = qs.filter(journal_entry=entry)
        return list(qs)

    def test_register_without_profile_is_ok(self):
        created = self._create_registered()
        self.assertIsNone(created['posting_profile_id'])
        document = OfficialDocument.all_objects.get(pk=created['id'])
        self.assertEqual(document.status, OfficialDocument.STATUS_REGISTERED)
        self.assertIsNone(document.posting_profile_id)

    def test_kind_tax_decision_does_not_infer_profile(self):
        created = self._create_registered(document_number='UP/I-NO-INFER')
        self.assertIsNone(created['posting_profile_code'])

    def test_catalog_has_no_liability_only_or_tax_obligation(self):
        response = self.client.get('/api/finance/official-document-posting-profiles/')
        self.assertEqual(response.status_code, 200)
        codes = {row['code'] for row in response.data}
        effects = {row['economic_effect'] for row in response.data}
        self.assertEqual(codes, {'ppmv_vehicle_acquisition', 'administrative_fee'})
        self.assertEqual(effects, {'capitalize', 'expense'})
        self.assertNotIn('tax_obligation', codes)
        self.assertNotIn('liability_only', effects)
        for row in response.data:
            self.assertNotIn('debit_account_code', row)
            self.assertNotIn('credit_account_code', row)

    def test_post_without_profile_is_400(self):
        created = self._create_registered(document_number='UP/I-NO-PROFILE')
        response = self._post(created['id'])
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data['code'], 'missing_profile')

    def test_post_ppmv_without_asset_is_400(self):
        created = self._create_registered(document_number='UP/I-NO-ASSET')
        OfficialDocument.all_objects.filter(pk=created['id']).update(related_fixed_asset=None)
        set_profile = self._set_profile(created['id'], self.ppmv.pk)
        self.assertEqual(set_profile.status_code, 200, set_profile.data)
        response = self._post(created['id'])
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data['code'], 'missing_fixed_asset')

    def test_set_profile_on_registered_then_lock_after_post(self):
        created = self._create_registered(document_number='UP/I-LOCK')
        first = self._set_profile(created['id'], self.ppmv.pk)
        self.assertEqual(first.status_code, 200, first.data)
        posted = self._post(created['id'])
        self.assertEqual(posted.status_code, 200, posted.data)
        locked = self._set_profile(created['id'], self.admin_fee.pk)
        self.assertEqual(locked.status_code, 409, locked.data)
        self.assertEqual(locked.data['code'], 'profile_locked')
        document = OfficialDocument.all_objects.get(pk=created['id'])
        self.assertEqual(document.posting_profile_id, self.ppmv.pk)

    def test_ppmv_post_creates_one_je_and_one_payable(self):
        created = self._create_registered()
        self._set_profile(created['id'], self.ppmv.pk)
        response = self._post(created['id'])
        self.assertEqual(response.status_code, 200, response.data)
        document = OfficialDocument.all_objects.get(pk=created['id'])
        ct = ContentType.objects.get_for_model(OfficialDocument)
        entries = list(
            JournalEntry.all_objects.filter(
                tenant=self.tenant,
                source_content_type=ct,
                source_object_id=document.pk,
                description__startswith=f'[{OFFICIAL_DOCUMENT_POSTED}]',
                status='posted',
            )
        )
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        lines = list(entry.lines.all())
        debit = next(line for line in lines if line.debit_amount)
        credit = next(line for line in lines if line.credit_amount)
        self.assertEqual(debit.account.account_code, '0373')
        self.assertTrue(str(credit.account.account_code).startswith('2201'))
        self.assertEqual(debit.debit_amount, Decimal('10347.20'))
        items = list(
            SubledgerItem.all_objects.filter(
                tenant=self.tenant,
                source_content_type=ct,
                source_object_id=document.pk,
            ).exclude(status='cancelled')
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].direction, 'payable')
        self.assertEqual(items[0].partner_id, self.issuer.pk)
        self.assertEqual(items[0].open_amount, Decimal('10347.20'))
        self.assertEqual(items[0].status, 'open')
        self.assertEqual(document.status, OfficialDocument.STATUS_REGISTERED)
        links = self._asset_links(asset=self.asset, entry=entry)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].role, AssetJournalLinkRole.DEPENDENT_COST)
        self.assertEqual(links[0].fixed_asset_id, self.asset.pk)
        self.assertEqual(links[0].journal_entry_id, entry.pk)

    def test_second_post_is_idempotent(self):
        created = self._create_registered(document_number='UP/I-IDEM')
        self._set_profile(created['id'], self.ppmv.pk)
        first = self._post(created['id'], key='idem-1')
        second = self._post(created['id'], key='idem-2')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        ct = ContentType.objects.get_for_model(OfficialDocument)
        self.assertEqual(
            JournalEntry.all_objects.filter(
                tenant=self.tenant,
                source_content_type=ct,
                source_object_id=created['id'],
                description__startswith=f'[{OFFICIAL_DOCUMENT_POSTED}]',
            ).count(),
            1,
        )
        self.assertEqual(
            SubledgerItem.all_objects.filter(
                tenant=self.tenant,
                source_content_type=ct,
                source_object_id=created['id'],
            ).exclude(status='cancelled').count(),
            1,
        )
        entries = self._marker_entries(created['id'])
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(self._asset_links(asset=self.asset, entry=entries[0])), 1)
        third = self._post(created['id'], key='idem-3')
        self.assertEqual(third.status_code, 200)
        self.assertEqual(len(self._marker_entries(created['id'])), 1)
        self.assertEqual(len(self._payables(created['id'])), 1)
        self.assertEqual(len(self._asset_links(asset=self.asset, entry=entries[0])), 1)

    def test_second_post_heals_missing_capitalize_link(self):
        created = self._create_registered(document_number='UP/I-HEAL')
        self._set_profile(created['id'], self.ppmv.pk)
        first = self._post(created['id'], key='heal-1')
        self.assertEqual(first.status_code, 200, first.data)
        entries = self._marker_entries(created['id'])
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        payables = self._payables(created['id'])
        self.assertEqual(len(payables), 1)
        payable_id = payables[0].pk
        FixedAssetJournalLink.all_objects.filter(
            tenant=self.tenant,
            journal_entry=entry,
        ).delete()
        self.assertEqual(len(self._asset_links(entry=entry)), 0)
        second = self._post(created['id'], key='heal-2')
        self.assertEqual(second.status_code, 200, second.data)
        healed_entries = self._marker_entries(created['id'])
        self.assertEqual(len(healed_entries), 1)
        self.assertEqual(healed_entries[0].pk, entry.pk)
        healed_payables = self._payables(created['id'])
        self.assertEqual(len(healed_payables), 1)
        self.assertEqual(healed_payables[0].pk, payable_id)
        links = self._asset_links(asset=self.asset, entry=entry)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].role, AssetJournalLinkRole.DEPENDENT_COST)

    def test_administrative_fee_post_creates_no_asset_link(self):
        created = self._create_registered(
            official_kind='other',
            document_number='UPR-2026-1',
        )
        set_profile = self._set_profile(created['id'], self.admin_fee.pk)
        self.assertEqual(set_profile.status_code, 200, set_profile.data)
        response = self._post(created['id'], key='admin-fee-1')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(self._marker_entries(created['id'])), 1)
        self.assertEqual(len(self._payables(created['id'])), 1)
        self.assertEqual(len(self._asset_links(asset=self.asset)), 0)

    def test_capitalize_link_rejects_foreign_tenant_asset(self):
        from domains.finance.services.official_documents import (
            OfficialDocumentBadRequest,
            _ensure_capitalize_asset_link,
        )

        other = Tenant.objects.create(slug='other-post', name='Other Post Co')
        provision_tenant_chart(other)
        other_asset = FixedAsset.all_objects.create(
            tenant=other,
            name='Tuđi Audi',
            inventory_number='OS-FOREIGN',
            status=FixedAssetStatus.IN_PREPARATION,
            origin=FixedAssetOrigin.PURCHASE,
            acquisition_cost=Decimal('1000.00'),
            purchase_date=date(2026, 8, 1),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=ChartOfAccounts.all_objects.get(tenant=other, account_code='0373'),
            asset_account=ChartOfAccounts.all_objects.get(tenant=other, account_code='032001'),
            accumulated_depreciation_account=ChartOfAccounts.all_objects.get(
                tenant=other, account_code='0393'
            ),
            depreciation_expense_account=ChartOfAccounts.all_objects.get(
                tenant=other, account_code='4314'
            ),
        )
        created = self._create_registered(document_number='UP/I-X-TENANT')
        OfficialDocument.all_objects.filter(pk=created['id']).update(
            related_fixed_asset=other_asset,
        )
        self._set_profile(created['id'], self.ppmv.pk)
        response = self._post(created['id'], key='x-tenant-1')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data['code'], 'invalid_asset')
        self.assertEqual(len(self._marker_entries(created['id'])), 0)
        self.assertEqual(len(self._payables(created['id'])), 0)
        self.assertFalse(
            FixedAssetJournalLink.all_objects.filter(
                journal_entry__source_object_id=created['id'],
            ).exists()
        )
        self.assertFalse(
            FixedAssetJournalLink.all_objects.filter(fixed_asset=other_asset).exists()
        )

        created_ok = self._create_registered(document_number='UP/I-X-HELPER')
        self._set_profile(created_ok['id'], self.ppmv.pk)
        self.assertEqual(self._post(created_ok['id'], key='x-helper-1').status_code, 200)
        document = OfficialDocument.all_objects.get(pk=created_ok['id'])
        entry = self._marker_entries(created_ok['id'])[0]
        document.related_fixed_asset = other_asset
        with self.assertRaises(OfficialDocumentBadRequest) as raised:
            _ensure_capitalize_asset_link(
                tenant=self.tenant,
                document=document,
                entry=entry,
            )
        self.assertEqual(raised.exception.code, 'invalid_asset')
        self.assertFalse(
            FixedAssetJournalLink.all_objects.filter(fixed_asset=other_asset).exists()
        )
        self.assertEqual(len(self._asset_links(asset=self.asset, entry=entry)), 1)

    def test_manual_link_without_ap_is_409_and_does_not_lock_profile(self):
        created = self._create_registered(document_number='UP/I-LEGACY')
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202608-0099',
            entry_date=date(2026, 8, 3),
            description='PPMV ručno',
            status='posted',
            created_by=self.owner,
        )
        linked = self.client.post(
            f'/api/finance/official-documents/{created["id"]}/link-journal/',
            {'journal_entry_id': entry.pk},
            format='json',
        )
        self.assertEqual(linked.status_code, 200, linked.data)
        set_profile = self._set_profile(created['id'], self.ppmv.pk)
        self.assertEqual(set_profile.status_code, 200, set_profile.data)
        posted = self._post(created['id'])
        self.assertEqual(posted.status_code, 409, posted.data)
        self.assertEqual(posted.data['code'], 'already_linked_manual_posting')
        ct = ContentType.objects.get_for_model(OfficialDocument)
        self.assertEqual(
            JournalEntry.all_objects.filter(
                tenant=self.tenant,
                source_content_type=ct,
                source_object_id=created['id'],
            ).count(),
            1,
        )
        self.assertFalse(
            SubledgerItem.all_objects.filter(
                tenant=self.tenant,
                source_content_type=ct,
                source_object_id=created['id'],
            ).exists()
        )
        entry.refresh_from_db()
        self.assertEqual(entry.description, 'PPMV ručno')

    def test_expense_approved_on_official_document_raises(self):
        created = self._create_registered(document_number='UP/I-GUARD-E')
        document = OfficialDocument.all_objects.get(pk=created['id'])
        with self.assertRaises(PostingEventSourceMismatch):
            build_document_posting_plan(self.tenant, document, 'expense_approved')
        with self.assertRaises(PostingEventSourceMismatch):
            post_document(self.tenant, document, 'expense_approved', self.owner)

    def test_official_event_on_expense_raises(self):
        category = ExpenseCategory.all_objects.create(
            tenant=self.tenant,
            name='Ostalo',
            default_account=ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code='4120'),
        )
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='T-GUARD',
            status='approved',
            supplier=self.issuer,
            category=category,
            amount=Decimal('10.00'),
            tax_amount=Decimal('0.00'),
            expense_date=date(2026, 8, 3),
            posting_profile=ExpensePostingProfile.OPEX,
            created_by=self.owner,
        )
        with self.assertRaises(PostingEventSourceMismatch):
            build_document_posting_plan(self.tenant, expense, OFFICIAL_DOCUMENT_POSTED)
        with self.assertRaises(PostingEventSourceMismatch):
            post_document(self.tenant, expense, OFFICIAL_DOCUMENT_POSTED, self.owner)

    def test_read_model_unpaid_from_subledger_after_post(self):
        created = self._create_registered(document_number='UP/I-READ')
        self._set_profile(created['id'], self.ppmv.pk)
        self.assertEqual(self._post(created['id']).status_code, 200)
        detail = self.client.get(f'/api/documents/official/{created["id"]}/').json()
        self.assertEqual(detail['operational_status']['value'], 'posted')
        self.assertEqual(detail['subledger']['state']['value'], 'open')
        self.assertEqual(detail['subledger']['open_amount']['value'], '10347.20')
        self.assertEqual(detail['document_status']['value'], 'registered')
