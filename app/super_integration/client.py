"""HTTP klijent za SUPER Web API v5.1.3."""

from __future__ import annotations

import uuid
from typing import Any

import requests
from django.core.cache import cache
from django.utils import timezone

from .models import SuperTenantConfig


class SuperAPIError(Exception):
    def __init__(self, message: str, response: dict | None = None):
        super().__init__(message)
        self.response = response or {}


class SuperClient:
    INBOUND_STATUS_RECEIVED = 10
    INBOUND_STATUS_APPROVED = 30
    INBOUND_STATUS_REJECTED = 40

    OUTBOUND_STATUS_SENT = 40
    OUTBOUND_STATUS_DELIVERED = 60

    def __init__(self, config: SuperTenantConfig):
        self.config = config
        self.base_url = config.api_base_url.rstrip('/')
        self._session = requests.Session()

    def _cache_key(self) -> str:
        return f'super_token:{self.config.pk}'

    def _message_id(self) -> str:
        return str(uuid.uuid4())

    def get_token(self, force_refresh: bool = False) -> str:
        cache_key = self._cache_key()
        if not force_refresh:
            cached = cache.get(cache_key)
            if cached:
                return cached

        url = f'{self.base_url}/Token'
        response = self._session.post(
            url,
            data={
                'grant_type': 'password',
                'username': self.config.username,
                'password': self.config.password,
            },
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get('access_token')
        if not token:
            raise SuperAPIError('SUPER Token endpoint nije vratio access_token', payload)
        expires_in = int(payload.get('expires_in', 840))
        cache.set(cache_key, token, max(expires_in - 60, 60))
        return token

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        data = {**data, 'MessageId': data.get('MessageId') or self._message_id()}
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
            'Authorization': f'Bearer {self.get_token()}',
        }
        url = f'{self.base_url}{path}'
        response = self._session.post(url, data=data, headers=headers, timeout=120)
        response.raise_for_status()
        payload = response.json()
        error = payload.get('ErrorMessage') or payload.get('errorMessage')
        if error:
            raise SuperAPIError(str(error), payload)
        return payload

    def check_participant(self, oib: str, scheme: str = '9934') -> bool:
        payload = self._post('/api/Ams/CheckParticipant', {
            'Scheme': scheme,
            'Identifier': oib,
        })
        published = payload.get('IsPublished')
        if published is None:
            published = payload.get('isPublished')
        return bool(published)

    def get_invoice_list(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        received_date_from: str | None = None,
        received_date_to: str | None = None,
        unique_from: int | None = 1,
    ) -> list[dict]:
        data = {'CompanyGuid': self.config.company_guid}
        if date_from:
            data['DateFrom'] = date_from
        if date_to:
            data['DateTo'] = date_to
        if received_date_from:
            data['ReceivedDateFrom'] = received_date_from
        if received_date_to:
            data['ReceivedDateTo'] = received_date_to
        if unique_from is not None:
            data['UniqueIdFrom'] = str(unique_from)
        payload = self._post('/api/Invoice/GetInvoiceList', data)
        return payload.get('Invoices') or payload.get('invoices') or []

    def get_invoice_ubl(self, guid: str) -> tuple[str, dict]:
        payload = self._post('/api/Invoice/GetInvoice', {'Guid': guid})
        return payload.get('InvoiceUBL') or payload.get('invoiceUBL') or '', payload

    def set_invoice_status(self, guid: str, status: int) -> dict:
        return self._post('/api/Invoice/SetInvoiceStatus', {
            'Guid': guid,
            'InvoiceStatus': status,
        })

    def get_invoice_visualization_pdf_b64(self, guid: str) -> str:
        payload = self._post('/api/Invoice/GetInvoiceDetailVisualization', {'Guid': guid})
        return payload.get('InvoiceDetailVisualization') or ''

    def reject_invoice(self, guid: str, reason_type: int = 11, description: str = '') -> dict:
        return self._post('/api/EReporting/RejectInvoice', {
            'CompanyGuid': self.config.company_guid,
            'Guid': guid,
            'RejectReasonType': reason_type,
            'RejectionReasonDescription': description or 'Odbijeno u racunAI',
        })

    def send_sending_invoice_ubl(self, ubl_xml: str, document_type: int = 1) -> dict:
        import base64

        encoded = base64.b64encode(ubl_xml.encode('utf-8')).decode('ascii')
        return self._post('/api/SendingInvoice/SendSendingInvoiceUbl', {
            'CompanyGuid': self.config.company_guid,
            'Base64EncodedUbl': encoded,
            'UblDocumentType': document_type,
        })

    def get_sending_invoice_statuses(
        self,
        guids: list[str],
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        data = {'CompanyGuid': self.config.company_guid}
        if date_from:
            data['DateFrom'] = date_from
        if date_to:
            data['DateTo'] = date_to
        if guids:
            data['SendingInvoiceGuidList'] = ','.join(guids)
        payload = self._post('/api/SendingInvoice/GetSendingInvoiceStatuses', data)
        return (
            payload.get('SendingInvoiceStatuses')
            or payload.get('sendingInvoiceStatuses')
            or payload.get('InvoiceStatuses')
            or []
        )

    def add_payment_for_sending_invoice(
        self,
        guid: str,
        payment_date,
        amount: Decimal,
        payment_method: int = 1,
        mark_paid_in_full: bool = True,
    ) -> dict:
        return self._post('/api/EReporting/AddPaymentForSendingInvoice', {
            'CompanyGuid': self.config.company_guid,
            'Guid': guid,
            'PaymentDate': payment_date.strftime('%Y-%m-%d'),
            'PaymentAmount': f'{amount:.2f}',
            'PaymentMethod': payment_method,
            'MarkAsPaidInFull': str(mark_paid_in_full).lower(),
        })

    def touch_inbound_sync(self):
        self.config.last_inbound_sync_at = timezone.now()
        self.config.save(update_fields=['last_inbound_sync_at'])

    def touch_outbound_poll(self):
        self.config.last_outbound_poll_at = timezone.now()
        self.config.save(update_fields=['last_outbound_poll_at'])
