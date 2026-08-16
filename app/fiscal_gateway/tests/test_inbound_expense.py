from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from fiscal_gateway.client.as4_client import As4ApplicationResponseResult
from fiscal_gateway.client.domibus_push import parse_domibus_push
from fiscal_gateway.services.inbound_as4 import handle_inbound_push
from fiscal_gateway.tests.test_as4_inbound import INVOICE_XML, _sample_push_soap, _wrap_invoice_sbdh
from settings.models import CompanySettings
from tenants.models import Tenant


@pytest.mark.django_db
class TestInboundExpenseCreation:
    @patch('fiscal_gateway.services.inbound_as4.submit_application_response_via_domibus')
    def test_handle_push_creates_expense(self, mock_submit, settings):
        settings.DOMIBUS_AP_OIB = '99999999994'
        mock_submit.return_value = As4ApplicationResponseResult(
            message_id='resp-001@racunai.hr',
            domibus_response='<ok/>',
        )

        tenant = Tenant.objects.create(slug='as4-inbound', name='AS4 Inbound')
        CompanySettings.all_objects.create(
            tenant=tenant,
            company_name='Test Co',
            company_address='Ulica 1',
            street='Ulica',
            house_number='1',
            postal_code='10000',
            city='Zagreb',
            country='HR',
            company_phone='0912345678',
            company_email='test@test.hr',
            vat_number='99999999994',
        )
        User = get_user_model()
        User.objects.create_superuser('admin', 'admin@test.hr', 'pass')

        payload = _wrap_invoice_sbdh(INVOICE_XML.read_bytes())
        message = parse_domibus_push(_sample_push_soap(payload, customer_oib='99999999994'))
        result = handle_inbound_push(message)

        assert result.accepted is True
        from expenses.models import Expense
        from fiscal_gateway.models import As4DocumentLink

        link = As4DocumentLink.all_objects.filter(
            tenant=tenant,
            direction=As4DocumentLink.DIRECTION_INBOUND,
            message_id=message.message_id,
        ).first()
        assert link is not None
        expense = Expense.all_objects.filter(tenant=tenant, pk=link.object_id).first()
        assert expense is not None
        assert expense.receipt_number == '13062026-TP-5054'
