"""Inbound eRačun sync — gateway inbox to draft Expense, dedupe on DB invariants."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from lxml import etree
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from expenses.models import Expense, ExpenseCategory, ExpenseImportMetadata, ExpenseSource
from fiscal_gateway.client.gateway_v1_client import GatewayV1Error
from integrations.services.inbound_eracun_sync import (
    CODE_DISABLED,
    CODE_IN_PROGRESS,
    CODE_NO_OIB,
    EracunSyncError,
    import_available_inbound_eracun,
    refresh_inbound_inbox,
)
from partners.models import Partner
from settings.models import CompanySettings
from super_integration.models import SuperDocumentLink
from tenants.models import Tenant, TenantMembership

INVOICE_XML = Path(__file__).resolve().parent.parent.parent / 'fiscal_gateway' / 'fixtures' / 'pts_invoice.xml'
UBL_NS = 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2'
CBC_NS = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
HOST = 'esync.racunai.hr'
OIB = '36619131370'


def _ubl(invoice_number: str) -> str:
    """Real PTS invoice with a distinct cbc:ID, so each document is its own Expense."""
    root = etree.parse(str(INVOICE_XML)).getroot()
    invoice = root.find(f'.//{{{UBL_NS}}}Invoice')
    if invoice is None:
        invoice = root
    node = invoice.find(f'{{{CBC_NS}}}ID')
    node.text = invoice_number
    return etree.tostring(invoice, encoding='unicode')


def _item(guid: str) -> dict:
    return {
        'document_id': str(uuid.uuid4()),
        'direction': 'INBOUND',
        'intake_status': 'AVAILABLE',
        'bound_provider': 'super',
        'provider_refs': {'invoice_guid': guid, 'company_guid': 'company-1'},
    }


def _gateway(items: list[dict], *, ubl_by_guid: dict[str, str] | None = None) -> MagicMock:
    client = MagicMock()
    client.list_inbound_documents.return_value = {
        'items': items,
        'next_cursor': None,
        'has_more': False,
    }
    guid_by_doc = {
        row['document_id']: (row.get('provider_refs') or {}).get('invoice_guid', '')
        for row in items
    }

    def _ubl_for(document_id: str) -> str:
        guid = guid_by_doc[str(document_id)]
        if ubl_by_guid is not None and guid in ubl_by_guid:
            return ubl_by_guid[guid]
        return _ubl(f'INV-{guid}')

    client.get_inbound_ubl.side_effect = _ubl_for
    return client


@override_settings(ERACUN_INBOUND_SYNC_ENABLED=True)
class InboundEracunSyncServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='esync-svc', name='Sync Svc')
        CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Sync Co',
            company_address='Ulica 1',
            company_phone='01',
            company_email='a@b.c',
            tax_number=OIB,
        )
        User = get_user_model()
        cls.owner = User.objects.create_superuser('esync-owner', 'esync@test.hr', 'test')

    def _link_for_guid(self, guid: str) -> SuperDocumentLink:
        category = ExpenseCategory.all_objects.create(tenant=self.tenant, name=f'Cat {guid}')
        supplier = Partner.all_objects.create(
            tenant=self.tenant,
            name=f'Supplier {guid}',
            partner_type='supplier',
            status='active',
        )
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            expense_number=f'LEGACY-{guid}',
            source=ExpenseSource.SUPER,
            status='draft',
            category=category,
            supplier=supplier,
            amount=Decimal('10.00'),
            tax_amount=Decimal('2.00'),
            currency='EUR',
            expense_date=date(2026, 8, 1),
            created_by=self.owner,
        )
        ExpenseImportMetadata.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            source=ExpenseSource.SUPER,
            external_id=guid,
            super_guid=guid,
        )
        return SuperDocumentLink.all_objects.create(
            tenant=self.tenant,
            direction=SuperDocumentLink.DIRECTION_INBOUND,
            content_type=ContentType.objects.get_for_model(Expense),
            object_id=expense.pk,
            super_guid=guid,
        )

    @patch('integrations.services.inbound_eracun_sync.GatewayV1Client')
    def test_import_creates_draft_expense_with_eracun_source(self, client_cls):
        client_cls.return_value = _gateway([_item('g-1')])

        result = import_available_inbound_eracun(self.tenant)

        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['skipped'], 0)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(result['remaining_importable'], 0)

        expense = Expense.all_objects.get(tenant=self.tenant, source=ExpenseSource.ERACUN)
        self.assertEqual(expense.status, 'draft')
        # Business source is eRačun; the provider stays in the integration namespace.
        metadata = ExpenseImportMetadata.all_objects.get(expense=expense)
        self.assertEqual(metadata.source, ExpenseSource.SUPER)
        self.assertEqual(metadata.external_id, 'g-1')
        link = SuperDocumentLink.all_objects.get(super_guid='g-1')
        self.assertEqual(link.object_id, expense.pk)
        self.assertTrue(link.ubl_xml)

    @patch('integrations.services.inbound_eracun_sync.GatewayV1Client')
    def test_legacy_super_row_is_skipped_not_duplicated(self, client_cls):
        self._link_for_guid('g-legacy')
        client_cls.return_value = _gateway([_item('g-legacy')])

        result = import_available_inbound_eracun(self.tenant)

        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(SuperDocumentLink.all_objects.filter(super_guid='g-legacy').count(), 1)
        self.assertFalse(Expense.all_objects.filter(source=ExpenseSource.ERACUN).exists())

    @patch('integrations.services.inbound_eracun_sync.GatewayV1Client')
    def test_partial_document_failure_does_not_stop_the_run(self, client_cls):
        items = [_item(f'g-{index}') for index in range(1, 6)]
        client_cls.return_value = _gateway(items, ubl_by_guid={'g-3': '<not-ubl/>'})

        result = import_available_inbound_eracun(self.tenant)

        self.assertEqual(result['imported'], 4)
        self.assertEqual(result['failed'], 1)
        self.assertEqual([row['invoice_guid'] for row in result['errors']], ['g-3'])
        self.assertFalse(SuperDocumentLink.all_objects.filter(super_guid='g-3').exists())
        self.assertEqual(Expense.all_objects.filter(source=ExpenseSource.ERACUN).count(), 4)

    @patch('integrations.services.inbound_eracun_sync.GatewayV1Client')
    def test_concurrent_duplicate_yields_exactly_one_expense(self, client_cls):
        client_cls.return_value = _gateway([_item('g-race')])
        real_exists = SuperDocumentLink.all_objects.filter

        # Simulate the racing writer: the pre-check sees nothing, the DB constraint does.
        with patch(
            'integrations.services.inbound_eracun_sync._already_imported',
            return_value=False,
        ):
            first = import_available_inbound_eracun(self.tenant)
            second = import_available_inbound_eracun(self.tenant)

        self.assertEqual(first['imported'], 1)
        self.assertEqual(second['imported'], 0)
        self.assertEqual(second['skipped'], 1)
        self.assertEqual(real_exists(super_guid='g-race').count(), 1)
        self.assertEqual(Expense.all_objects.filter(source=ExpenseSource.ERACUN).count(), 1)

    @patch('integrations.services.inbound_eracun_sync.GatewayV1Client')
    def test_cap_reports_remaining_importable(self, client_cls):
        client_cls.return_value = _gateway([_item(f'g-{index}') for index in range(1, 4)])

        first = import_available_inbound_eracun(self.tenant, limit=2)
        self.assertEqual(first['imported'], 2)
        self.assertEqual(first['remaining_importable'], 1)

        second = import_available_inbound_eracun(self.tenant, limit=2)
        self.assertEqual(second['imported'], 1)
        self.assertEqual(second['skipped'], 2)
        self.assertEqual(second['remaining_importable'], 0)

    @patch('integrations.services.inbound_eracun_sync.GatewayV1Client')
    def test_document_without_invoice_guid_is_skipped_not_failed(self, client_cls):
        # Gateway rows with no provider invoice identity carry nothing to import or
        # dedupe against — reporting them as failures would cry wolf on every click.
        item = _item('g-1')
        item['provider_refs'] = {}
        client_cls.return_value = _gateway([item])

        result = import_available_inbound_eracun(self.tenant)

        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['errors'], [])

    @patch('integrations.services.inbound_eracun_sync.GatewayV1Client')
    def test_gateway_list_error_is_502(self, client_cls):
        client = MagicMock()
        client.list_inbound_documents.side_effect = GatewayV1Error('down')
        client_cls.return_value = client

        with self.assertRaises(EracunSyncError) as ctx:
            import_available_inbound_eracun(self.tenant)
        self.assertEqual(ctx.exception.http_status, 502)

    @override_settings(ERACUN_INBOUND_SYNC_ENABLED=False)
    @patch('integrations.services.inbound_eracun_sync.GatewayV1Client')
    def test_disabled_flag_blocks_before_any_gateway_call(self, client_cls):
        with self.assertRaises(EracunSyncError) as ctx:
            import_available_inbound_eracun(self.tenant)
        self.assertEqual(ctx.exception.code, CODE_DISABLED)
        self.assertEqual(ctx.exception.http_status, 503)
        client_cls.assert_not_called()

    @patch('integrations.services.inbound_eracun_sync.GatewayV1Client')
    def test_missing_oib_is_rejected(self, client_cls):
        tenant = Tenant.objects.create(slug='esync-no-oib', name='No OIB')
        with self.assertRaises(EracunSyncError) as ctx:
            import_available_inbound_eracun(tenant)
        self.assertEqual(ctx.exception.code, CODE_NO_OIB)
        client_cls.assert_not_called()

    @patch('integrations.services.inbound_eracun_sync.GatewayV1Client')
    def test_refresh_returns_gateway_status(self, client_cls):
        client = MagicMock()
        client.start_reconciliation.return_value = {
            'reconciliation_id': 'rec-1',
            'status': 'COMPLETED',
        }
        client_cls.return_value = client

        body = refresh_inbound_inbox(self.tenant, idempotency_key='key-1')

        self.assertEqual(body['status'], 'COMPLETED')
        client.start_reconciliation.assert_called_once_with(OIB, idempotency_key='key-1')

    @patch('integrations.services.inbound_eracun_sync.GatewayV1Client')
    def test_refresh_failed_reconciliation_is_502(self, client_cls):
        client = MagicMock()
        client.start_reconciliation.return_value = {
            'reconciliation_id': 'rec-2',
            'status': 'FAILED',
            'error': {'code': 'CAPABILITY_NOT_SUPPORTED', 'message': 'no binding', 'retryable': False},
        }
        client_cls.return_value = client

        with self.assertRaises(EracunSyncError) as ctx:
            refresh_inbound_inbox(self.tenant, idempotency_key='key-2')
        self.assertEqual(ctx.exception.code, 'CAPABILITY_NOT_SUPPORTED')
        self.assertEqual(ctx.exception.http_status, 502)


@override_settings(
    ERACUN_INBOUND_SYNC_ENABLED=True,
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
)
class InboundEracunSyncApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='esync', name='Sync API')
        CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Sync API Co',
            company_address='Ulica 1',
            company_phone='01',
            company_email='a@b.c',
            tax_number=OIB,
        )
        User = get_user_model()
        cls.owner = User.objects.create_superuser('esync-api-owner', 'api@test.hr', 'test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        cls.viewer = User.objects.create_user(username='esync-viewer', password='test')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')

    def setUp(self):
        self.client = APIClient()
        self.client.defaults['HTTP_HOST'] = HOST
        self._auth(self.owner)

    def _auth(self, user):
        token = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')

    @patch('integrations.services.inbound_eracun_sync.GatewayV1Client')
    def test_import_endpoint_returns_counters(self, client_cls):
        client_cls.return_value = _gateway([_item('g-api-1')])

        response = self.client.post('/api/purchasing/eracun/inbound-import/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['imported'], 1)

    def test_import_endpoint_requires_authentication(self):
        self.client.credentials()
        response = self.client.post('/api/purchasing/eracun/inbound-import/')
        self.assertEqual(response.status_code, 401)

    def test_import_endpoint_hidden_from_read_only_role(self):
        self._auth(self.viewer)
        response = self.client.post('/api/purchasing/eracun/inbound-import/')
        self.assertEqual(response.status_code, 404)

    @override_settings(ERACUN_INBOUND_SYNC_ENABLED=False)
    def test_import_endpoint_503_when_disabled(self):
        response = self.client.post('/api/purchasing/eracun/inbound-import/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], CODE_DISABLED)

    @patch('integrations.services.inbound_eracun_sync._tenant_lock')
    def test_import_endpoint_409_when_already_running(self, lock):
        lock.side_effect = EracunSyncError(CODE_IN_PROGRESS, 'busy')
        response = self.client.post('/api/purchasing/eracun/inbound-import/')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], CODE_IN_PROGRESS)

    def test_refresh_endpoint_requires_idempotency_key(self):
        response = self.client.post('/api/purchasing/eracun/inbox-refresh/')
        self.assertEqual(response.status_code, 400)

    @patch('integrations.services.inbound_eracun_sync.GatewayV1Client')
    def test_refresh_endpoint_passes_idempotency_key(self, client_cls):
        client = MagicMock()
        client.start_reconciliation.return_value = {'reconciliation_id': 'r-1', 'status': 'COMPLETED'}
        client_cls.return_value = client

        response = self.client.post(
            '/api/purchasing/eracun/inbox-refresh/',
            HTTP_IDEMPOTENCY_KEY='abc-123',
        )

        self.assertEqual(response.status_code, 200)
        client.start_reconciliation.assert_called_once_with(OIB, idempotency_key='abc-123')
