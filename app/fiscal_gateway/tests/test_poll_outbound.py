from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings

from fiscal_gateway.models import As4DocumentLink, DirectTenantConfig
from fiscal_gateway.services.outbound_eracun import poll_outbound_statuses
from tenants.models import Tenant


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class TestPollOutboundStatuses(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        from partners.models import Partner

        User = get_user_model()
        self.tenant = Tenant.objects.create(slug='poll-test', name='Poll Test')
        self.user = User.objects.create_user(username='polluser', password='pass')
        self.partner = Partner.all_objects.create(
            tenant=self.tenant,
            name='Partner',
            tax_number='12345678901',
            address='Adresa',
            city='Zagreb',
            postal_code='10000',
            created_by=self.user,
        )
        self.config = DirectTenantConfig.all_objects.create(
            tenant=self.tenant,
            oib='36619131370',
            is_active=True,
        )

    def _create_invoice(self, number: str):
        from invoices.models import Invoice

        return Invoice.all_objects.create(
            tenant=self.tenant,
            invoice_number=number,
            status='sent',
            company_to=self.partner,
            issue_date=date(2026, 6, 1),
            due_date=date(2026, 6, 15),
            created_by=self.user,
        )

    @patch('fiscal_gateway.services.outbound_eracun.fetch_message_status')
    def test_poll_updates_delivered_status(self, mock_fetch):
        from django.contrib.contenttypes.models import ContentType
        from invoices.models import Invoice

        invoice = self._create_invoice('POLL-1')
        link = As4DocumentLink.all_objects.create(
            tenant=self.tenant,
            direction=As4DocumentLink.DIRECTION_OUTBOUND,
            message_id='msg@domibus.eu',
            as4_status=As4DocumentLink.STATUS_SENT,
            content_type=ContentType.objects.get_for_model(Invoice),
            object_id=invoice.pk,
        )
        mock_fetch.return_value = 'ACKNOWLEDGED'

        updated = poll_outbound_statuses(self.config)
        self.assertEqual(updated, 1)
        link.refresh_from_db()
        self.assertEqual(link.as4_status, As4DocumentLink.STATUS_DELIVERED)

    @patch('fiscal_gateway.services.outbound_eracun.fetch_message_status')
    def test_poll_no_change_when_unknown(self, mock_fetch):
        from django.contrib.contenttypes.models import ContentType
        from invoices.models import Invoice

        invoice = self._create_invoice('POLL-2')
        As4DocumentLink.all_objects.create(
            tenant=self.tenant,
            direction=As4DocumentLink.DIRECTION_OUTBOUND,
            message_id='msg2@domibus.eu',
            as4_status=As4DocumentLink.STATUS_SENT,
            content_type=ContentType.objects.get_for_model(Invoice),
            object_id=invoice.pk,
        )
        mock_fetch.return_value = None
        self.assertEqual(poll_outbound_statuses(self.config), 0)
