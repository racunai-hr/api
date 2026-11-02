"""M1.7 G4 — admin inbound rejection routing (AS4 vs gateway)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings

from expenses.admin import ExpenseAdmin
from expenses.models import Expense, ExpenseCategory, ExpenseSource
from fiscal_gateway.models import As4DocumentLink
from integrations.services.inbound_rejection import reject_inbound_expense_from_admin
from partners.models import Partner
from super_integration.models import SuperDocumentLink
from tenants.models import Tenant


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class AdminInboundRejectionRoutingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='admin-rej', name='Admin Reject')
        User = get_user_model()
        cls.user = User.objects.create_user(username='admin-rej', password='test')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Supplier',
            partner_type='supplier',
            status='active',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

    def _expense(self, *, source: str = ExpenseSource.MANUAL) -> Expense:
        return Expense.all_objects.create(
            tenant=self.tenant,
            expense_number=f'T-ADM-{Expense.all_objects.count() + 1}',
            source=source,
            status='draft',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('10.00'),
            tax_amount=Decimal('2.00'),
            currency='EUR',
            expense_date=date(2026, 8, 1),
            created_by=self.user,
        )

    def _as4_link(self, expense: Expense) -> As4DocumentLink:
        return As4DocumentLink.all_objects.create(
            tenant=self.tenant,
            direction=As4DocumentLink.DIRECTION_INBOUND,
            message_id=f'msg-{expense.pk}@test.hr',
            content_type=ContentType.objects.get_for_model(Expense),
            object_id=expense.pk,
        )

    def _super_link(self, expense: Expense, guid: str = 'super-guid-1') -> SuperDocumentLink:
        return SuperDocumentLink.all_objects.create(
            tenant=self.tenant,
            direction=SuperDocumentLink.DIRECTION_INBOUND,
            super_guid=guid,
            content_type=ContentType.objects.get_for_model(Expense),
            object_id=expense.pk,
        )

    @patch('integrations.services.inbound_rejection.submit_eracun_rejection')
    @patch('integrations.manager.IntegrationManager.reject_inbound_expense')
    @patch('fiscal_gateway.client.gateway_v1_client.GatewayV1Client')
    def test_admin_reject_direct_expense_still_uses_as4_connector(
        self,
        mock_gateway_cls,
        mock_reject_as4,
        mock_submit_gateway,
    ):
        expense = self._expense(source=ExpenseSource.MANUAL)
        self._as4_link(expense)

        channel = reject_inbound_expense_from_admin(expense)

        self.assertEqual(channel, 'as4')
        mock_reject_as4.assert_called_once_with(expense, description='Odbijeno u adminu')
        mock_submit_gateway.assert_not_called()
        mock_gateway_cls.assert_not_called()

    @patch('integrations.manager.IntegrationManager.reject_inbound_expense')
    @patch('integrations.services.inbound_rejection.submit_eracun_rejection')
    @patch('fiscal_gateway.client.gateway_v1_client.GatewayV1Client')
    def test_admin_reject_super_origin_uses_gateway_not_super_client(
        self,
        mock_gateway_cls,
        mock_submit_gateway,
        mock_reject_as4,
    ):
        expense = self._expense(source=ExpenseSource.SUPER)
        self._super_link(expense)

        channel = reject_inbound_expense_from_admin(expense)

        self.assertEqual(channel, 'gateway')
        mock_submit_gateway.assert_called_once()
        self.assertIs(mock_submit_gateway.call_args.args[0], expense)
        mock_reject_as4.assert_not_called()
        mock_gateway_cls.assert_not_called()

    @patch('expenses.admin.reject_inbound_expense_from_admin')
    def test_admin_action_delegates_to_router(self, mock_router):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        expense = self._expense()
        mock_router.return_value = 'as4'
        request = RequestFactory().post('/')
        request.session = 'session'
        request._messages = FallbackStorage(request)
        admin_instance = ExpenseAdmin(Expense, None)
        admin_instance.reject_inbound_action(request, Expense.all_objects.filter(pk=expense.pk))
        mock_router.assert_called_once_with(expense)

    @patch('integrations.services.inbound_rejection.submit_eracun_rejection')
    @patch('integrations.manager.IntegrationManager.reject_inbound_expense')
    def test_as4_link_wins_when_both_provenance_links_exist(
        self,
        mock_reject_as4,
        mock_submit_gateway,
    ):
        expense = self._expense(source=ExpenseSource.MANUAL)
        self._as4_link(expense)
        self._super_link(expense)

        channel = reject_inbound_expense_from_admin(expense)

        self.assertEqual(channel, 'as4')
        mock_reject_as4.assert_called_once()
        mock_submit_gateway.assert_not_called()
