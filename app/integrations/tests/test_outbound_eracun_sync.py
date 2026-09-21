from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from accounting.services.chart import provision_tenant_chart
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from integrations.services.outbound_eracun_sync import import_available_outbound_eracun
from invoices.models import Invoice
from settings.models import CompanySettings
from super_integration.models import SuperDocumentLink
from tenants.models import Tenant

INVOICE_XML = Path(__file__).resolve().parent.parent.parent / 'fiscal_gateway' / 'fixtures' / 'pts_invoice.xml'
OIB = '99999999994'


def _item(guid: str) -> dict:
    return {
        'document_id': str(uuid.uuid4()),
        'direction': 'OUTBOUND',
        'exchange_status': 'DELIVERED',
        'payment_status': 'UNPAID',
        'recipient_status': 'PENDING',
        'bound_provider': 'pondi',
        'provider_refs': {
            'invoice_guid': guid,
            'company_guid': OIB,
            'historical_intake': True,
        },
    }


@override_settings(ERACUN_INBOUND_SYNC_ENABLED=True)
class OutboundEracunImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='out-sync', name='Out Sync')
        CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Out Co',
            company_address='Ulica 1',
            company_phone='01',
            company_email='a@b.c',
            vat_number=OIB,
        )
        User = get_user_model()
        User.objects.create_superuser('out-sync-owner', 'outsync@test.hr', 'test')
        provision_tenant_chart(cls.tenant)

    @patch('integrations.services.outbound_eracun_sync.GatewayV1Client')
    def test_import_creates_sent_invoice(self, client_cls):
        ubl = INVOICE_XML.read_text()
        item = _item('out-1')
        client = MagicMock()
        client.list_outbound_documents.return_value = {
            'items': [item],
            'next_cursor': None,
            'has_more': False,
        }
        client.get_outbound_ubl.return_value = ubl
        client_cls.return_value = client

        result = import_available_outbound_eracun(self.tenant)

        self.assertEqual(result['imported'], 1, result)
        self.assertEqual(result['failed'], 0, result)
        invoice = Invoice.all_objects.get(tenant=self.tenant, invoice_number='13062026-TP-5054')
        self.assertEqual(invoice.status, 'draft')
        self.assertEqual(invoice.company_to.tax_number, OIB)
        link = SuperDocumentLink.all_objects.get(
            tenant=self.tenant,
            direction=SuperDocumentLink.DIRECTION_OUTBOUND,
            super_guid='out-1',
        )
        self.assertEqual(link.object_id, invoice.pk)
        self.assertTrue(invoice.items.exists())
