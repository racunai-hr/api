from unittest.mock import MagicMock, patch

import pytest
from django.contrib import admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from integrations.errors import IntegrationError
from invoices.admin import InvoiceAdmin
from invoices.models import Invoice
from ubl.domain.errors import SemanticValidationError


@pytest.mark.django_db
def test_send_eracun_action_shows_semantic_validation_messages():
    request = RequestFactory().post('/admin/invoices/invoice/')
    request.user = MagicMock(is_authenticated=True, is_staff=True)
    request.session = {}
    request._messages = FallbackStorage(request)

    invoice = MagicMock(spec=Invoice)
    invoice.__str__ = lambda self: 'INV-001'

    admin_instance = InvoiceAdmin(Invoice, admin.site)
    queryset = MagicMock()
    queryset.__iter__ = lambda self: iter([invoice])

    with patch(
        'invoices.admin.InvoiceIntegrationService.send_eracun',
        side_effect=SemanticValidationError(['Kupac: OIB mora imati 11 znamenki']),
    ):
        admin_instance.send_eracun_action(request, queryset)

    stored = list(request._messages)
    assert any('OIB mora imati 11 znamenki' in str(message) for message in stored)


@pytest.mark.django_db
def test_send_eracun_action_shows_integration_error():
    request = RequestFactory().post('/admin/invoices/invoice/')
    request.user = MagicMock(is_authenticated=True, is_staff=True)
    request.session = {}
    request._messages = FallbackStorage(request)

    invoice = MagicMock(spec=Invoice)
    invoice.__str__ = lambda self: 'INV-002'

    admin_instance = InvoiceAdmin(Invoice, admin.site)
    queryset = MagicMock()
    queryset.__iter__ = lambda self: iter([invoice])

    with patch(
        'invoices.admin.InvoiceIntegrationService.send_eracun',
        side_effect=IntegrationError('Nema aktivne integracije'),
    ):
        admin_instance.send_eracun_action(request, queryset)

    stored = list(request._messages)
    assert any('Nema aktivne integracije' in str(message) for message in stored)
