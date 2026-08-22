"""Inbound eRačun rejection via intermediary — pending state, no legacy fallback."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from expenses.models import Expense, ExpenseCategory, ExpenseSource
from fiscal_gateway.client.gateway_v1_client import GatewayV1Error
from integrations.models import InboundEracunRejectionAttempt, InboundGatewayDocumentLink
from integrations.services.inbound_rejection import (
    InboundRejectionError,
    build_actions_reject_block,
    resolve_gateway_document,
    resolve_reject_action,
    submit_eracun_rejection,
    UNAVAILABLE_CAPABILITY,
    UNAVAILABLE_NOT_ON_GATEWAY,
    UNAVAILABLE_REJECTION_PENDING,
)
from partners.models import Partner
from settings.models import CompanySettings
from super_integration.models import SuperDocumentLink
from tenants.models import Tenant, TenantMembership

HOST = 'rej.racunai.hr'


class InboundEracunRejectionServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='rej-svc', name='Reject Svc')
        CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Reject Co',
            company_address='Ulica 1',
            company_phone='01',
            company_email='a@b.c',
            tax_number='36619131370',
        )
        User = get_user_model()
        cls.owner = User.objects.create_user(username='rej-owner', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Supplier',
            partner_type='supplier',
            status='active',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

    def _expense(self, *, number: str = 'T-REJ-1', status: str = 'draft') -> Expense:
        return Expense.all_objects.create(
            tenant=self.tenant,
            expense_number=number,
            source=ExpenseSource.SUPER,
            status=status,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('10.00'),
            tax_amount=Decimal('2.00'),
            currency='EUR',
            expense_date=date(2026, 8, 1),
            created_by=self.owner,
        )

    def _super_link(self, expense: Expense, guid: str = 'guid-abc') -> SuperDocumentLink:
        return SuperDocumentLink.all_objects.create(
            tenant=self.tenant,
            direction=SuperDocumentLink.DIRECTION_INBOUND,
            content_type=ContentType.objects.get_for_model(Expense),
            object_id=expense.pk,
            super_guid=guid,
            super_unique_id=1,
            super_status=10,
        )

    def _gateway_link(self, expense: Expense, *, capable: bool = True) -> InboundGatewayDocumentLink:
        return InboundGatewayDocumentLink.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            gateway_document_id=uuid.uuid4(),
            bound_provider='super',
            provider_ref='guid-abc',
            taxpayer_oib='36619131370',
            rejection_capable=capable,
        )

    def test_actions_not_on_gateway_without_link(self):
        expense = self._expense()
        self._super_link(expense)
        with patch(
            'integrations.services.inbound_rejection.resolve_gateway_document',
            return_value=None,
        ):
            action = resolve_reject_action(expense)
        self.assertFalse(action.available)
        self.assertEqual(action.unavailable_code, UNAVAILABLE_NOT_ON_GATEWAY)

    def test_actions_rejection_pending_blocks(self):
        expense = self._expense()
        link = self._gateway_link(expense)
        InboundEracunRejectionAttempt.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            gateway_document_id=link.gateway_document_id,
            idempotency_key='k1',
            reason_code='REJECTED_BY_RECIPIENT',
            status=InboundEracunRejectionAttempt.STATUS_PENDING,
        )
        block = build_actions_reject_block(expense)
        self.assertFalse(block['available'])
        self.assertEqual(block['unavailable_code'], UNAVAILABLE_REJECTION_PENDING)

    def test_submit_sets_pending_not_expense_rejected(self):
        expense = self._expense()
        link = self._gateway_link(expense)
        attempt_id = str(uuid.uuid4())
        client = MagicMock()
        client.reject_e_reporting.return_value = (
            202,
            {'attempt_id': attempt_id, 'e_reporting_status': 'PENDING'},
        )
        body = submit_eracun_rejection(
            expense,
            reason_code='REJECTED_BY_RECIPIENT',
            reason_text='test',
            idempotency_key='idem-1',
            client=client,
        )
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'draft')
        self.assertEqual(body['status'], 'draft')
        self.assertEqual(body['lifecycle']['workflow'], 'rejection_pending')
        self.assertEqual(body['e_reporting']['status'], 'PENDING')
        client.reject_e_reporting.assert_called_once()
        pending = InboundEracunRejectionAttempt.all_objects.get(expense=expense)
        self.assertEqual(pending.status, 'pending')
        self.assertEqual(str(pending.gateway_document_id), str(link.gateway_document_id))

    def test_cached_link_retries_capability_after_transient_failure(self):
        expense = self._expense()
        link = self._gateway_link(expense, capable=False)
        client = MagicMock()
        client.provider_capabilities.return_value = {
            'supports': {'inbound_e_reporting_rejection': True},
        }
        action = resolve_reject_action(expense, client=client)
        self.assertTrue(action.available)
        link.refresh_from_db()
        self.assertTrue(link.rejection_capable)
        client.provider_capabilities.assert_called_once()

    def test_resolve_gateway_document_does_not_cache_transient_capability_failure(self):
        expense = self._expense()
        self._super_link(expense, guid='guid-resolve')
        doc_id = uuid.uuid4()
        client = MagicMock()
        client.list_inbound_documents.return_value = {
            'items': [
                {
                    'document_id': str(doc_id),
                    'bound_provider': 'super',
                    'provider_refs': {'invoice_guid': 'guid-resolve'},
                }
            ],
            'has_more': False,
        }
        client.provider_capabilities.side_effect = GatewayV1Error('down', status_code=503)
        link = resolve_gateway_document(expense, client=client)
        self.assertIsNotNone(link)
        self.assertFalse(link.rejection_capable)

        client.provider_capabilities.side_effect = None
        client.provider_capabilities.return_value = {
            'supports': {'inbound_e_reporting_rejection': True},
        }
        refreshed = resolve_gateway_document(expense, client=client)
        self.assertTrue(refreshed.rejection_capable)
        action = resolve_reject_action(expense, client=client)
        self.assertTrue(action.available)

    def test_definitive_capability_false_stays_unavailable(self):
        expense = self._expense()
        link = self._gateway_link(expense, capable=False)
        client = MagicMock()
        client.provider_capabilities.return_value = {
            'supports': {'inbound_e_reporting_rejection': False},
        }
        action = resolve_reject_action(expense, client=client)
        self.assertFalse(action.available)
        self.assertEqual(action.unavailable_code, UNAVAILABLE_CAPABILITY)
        link.refresh_from_db()
        self.assertFalse(link.rejection_capable)

    def test_second_reject_while_pending_is_409(self):
        expense = self._expense()
        link = self._gateway_link(expense)
        InboundEracunRejectionAttempt.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            gateway_document_id=link.gateway_document_id,
            idempotency_key='k-old',
            reason_code='REJECTED_BY_RECIPIENT',
            status=InboundEracunRejectionAttempt.STATUS_PENDING,
        )
        client = MagicMock()
        with self.assertRaises(InboundRejectionError) as ctx:
            submit_eracun_rejection(
                expense,
                reason_code='REJECTED_BY_RECIPIENT',
                idempotency_key='k-new',
                client=client,
            )
        self.assertEqual(ctx.exception.code, UNAVAILABLE_REJECTION_PENDING)
        client.reject_e_reporting.assert_not_called()

    def test_idempotency_conflict_different_expense(self):
        expense_a = self._expense(number='T-REJ-A')
        expense_b = self._expense(number='T-REJ-B')
        link_a = self._gateway_link(expense_a)
        self._gateway_link(expense_b)
        InboundEracunRejectionAttempt.all_objects.create(
            tenant=self.tenant,
            expense=expense_a,
            gateway_document_id=link_a.gateway_document_id,
            idempotency_key='shared-key',
            reason_code='REJECTED_BY_RECIPIENT',
            reason_text='same body',
            status=InboundEracunRejectionAttempt.STATUS_PENDING,
        )
        client = MagicMock()
        with self.assertRaises(InboundRejectionError) as ctx:
            submit_eracun_rejection(
                expense_b,
                reason_code='REJECTED_BY_RECIPIENT',
                reason_text='same body',
                idempotency_key='shared-key',
                client=client,
            )
        self.assertEqual(ctx.exception.code, 'idempotency_conflict')
        client.reject_e_reporting.assert_not_called()

    def test_no_legacy_fallback_on_gateway_error(self):
        expense = self._expense()
        self._gateway_link(expense)
        client = MagicMock()
        client.reject_e_reporting.side_effect = GatewayV1Error('down', status_code=503)
        with self.assertRaises(InboundRejectionError) as ctx:
            submit_eracun_rejection(
                expense,
                reason_code='OTHER',
                idempotency_key='idem-fail',
                client=client,
            )
        self.assertEqual(ctx.exception.code, 'gateway_unavailable')
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'draft')
        self.assertFalse(
            InboundEracunRejectionAttempt.all_objects.filter(expense=expense).exists()
        )

    def test_pending_attempt_reserved_before_gateway_call(self):
        expense = self._expense()
        self._gateway_link(expense)
        attempt_id = str(uuid.uuid4())
        client = MagicMock()
        client.reject_e_reporting.return_value = (
            202,
            {'attempt_id': attempt_id, 'e_reporting_status': 'PENDING'},
        )
        gateway_calls_at_create: list[int] = []
        original_create = InboundEracunRejectionAttempt.all_objects.create

        def tracked_create(**kwargs):
            gateway_calls_at_create.append(client.reject_e_reporting.call_count)
            return original_create(**kwargs)

        with patch.object(
            InboundEracunRejectionAttempt.all_objects,
            'create',
            side_effect=tracked_create,
        ):
            submit_eracun_rejection(
                expense,
                reason_code='REJECTED_BY_RECIPIENT',
                idempotency_key='reserve-first',
                client=client,
            )
        self.assertEqual(gateway_calls_at_create, [0])
        self.assertEqual(client.reject_e_reporting.call_count, 1)


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class InboundEracunRejectionApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='rej', name='Reject API')
        CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Reject API Co',
            company_address='Ulica 1',
            company_phone='01',
            company_email='a@b.c',
            tax_number='36619131370',
        )
        User = get_user_model()
        cls.owner = User.objects.create_user(username='rej-api-owner', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Supplier',
            partner_type='supplier',
            status='active',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

    def setUp(self):
        self.client = APIClient()
        token = RefreshToken.for_user(self.owner)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
        self.client.defaults['HTTP_HOST'] = HOST

    def _expense(self) -> Expense:
        return Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='T-API-1',
            source=ExpenseSource.SUPER,
            status='draft',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('10.00'),
            tax_amount=Decimal('2.00'),
            currency='EUR',
            expense_date=date(2026, 8, 1),
            created_by=self.owner,
        )

    @patch('integrations.services.inbound_rejection.GatewayV1Client')
    def test_post_202_pending(self, client_cls):
        expense = self._expense()
        InboundGatewayDocumentLink.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            gateway_document_id=uuid.uuid4(),
            bound_provider='super',
            provider_ref='g1',
            taxpayer_oib='36619131370',
            rejection_capable=True,
        )
        instance = client_cls.return_value
        attempt_id = str(uuid.uuid4())
        instance.reject_e_reporting.return_value = (
            202,
            {'attempt_id': attempt_id, 'e_reporting_status': 'PENDING'},
        )
        response = self.client.post(
            f'/api/purchasing/expenses/{expense.pk}/eracun-rejection/',
            {'reason_code': 'REJECTED_BY_RECIPIENT', 'reason_text': 'x'},
            format='json',
            HTTP_IDEMPOTENCY_KEY='api-idem-1',
        )
        self.assertEqual(response.status_code, 202, response.content)
        data = response.json()
        self.assertEqual(data['lifecycle']['workflow'], 'rejection_pending')
        self.assertEqual(data['status'], 'draft')
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'draft')
        self.assertTrue(
            InboundEracunRejectionAttempt.all_objects.filter(
                expense=expense, status='pending'
            ).exists()
        )
        instance.reject_e_reporting.assert_called_once()

    def test_post_409_when_pending(self):
        expense = self._expense()
        link = InboundGatewayDocumentLink.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            gateway_document_id=uuid.uuid4(),
            bound_provider='super',
            provider_ref='g1',
            taxpayer_oib='36619131370',
            rejection_capable=True,
        )
        InboundEracunRejectionAttempt.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            gateway_document_id=link.gateway_document_id,
            idempotency_key='already',
            reason_code='REJECTED_BY_RECIPIENT',
            status=InboundEracunRejectionAttempt.STATUS_PENDING,
        )
        response = self.client.post(
            f'/api/purchasing/expenses/{expense.pk}/eracun-rejection/',
            {'reason_code': 'REJECTED_BY_RECIPIENT'},
            format='json',
            HTTP_IDEMPOTENCY_KEY='api-idem-2',
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'rejection_pending')
