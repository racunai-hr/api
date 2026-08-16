from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import pytest
from lxml import etree

from fiscal_gateway.client.as4_client import As4ApplicationResponseResult
from fiscal_gateway.client.domibus_push import (
    DomibusPushParseError,
    build_push_ack_response,
    parse_domibus_push,
)
from fiscal_gateway.client.eracun_lookup import UBL_NS
from fiscal_gateway.services.application_response import (
    RESPONSE_CODE_ACCEPT,
    RESPONSE_CODE_REJECT,
    build_application_response_xml,
    wrap_application_response_sbdh,
)
from fiscal_gateway.services.inbound_as4 import handle_inbound_push, validate_inbound_invoice

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'
INVOICE_XML = FIXTURES / 'pts_invoice.xml'


def _wrap_invoice_sbdh(invoice_bytes: bytes) -> bytes:
    from fiscal_gateway.client.as4_client import wrap_invoice_sbdh

    return wrap_invoice_sbdh(
        invoice_bytes,
        supplier_oib='11528564544',
        customer_oib='36619131370',
        invoice_id='13062026-TP-5054',
    )


def _sample_push_soap(payload: bytes, *, customer_oib: str = '36619131370') -> bytes:
    encoded = base64.b64encode(payload).decode('ascii')
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
    xmlns:eb="http://docs.oasis-open.org/ebxml-msg/ebms/v3.0/ns/core/200704/">
  <soap:Header>
    <eb:Messaging>
      <eb:UserMessage>
        <eb:MessageInfo>
          <eb:Timestamp>2026-06-13T10:00:00Z</eb:Timestamp>
          <eb:MessageId>pts-msg-001@porezna.hr</eb:MessageId>
          <eb:ConversationId>conv-001</eb:ConversationId>
        </eb:MessageInfo>
        <eb:PartyInfo>
          <eb:From>
            <eb:PartyId type="urn:oasis:names:tc:ebcore:partyid-type:unregistered">domieracuntest</eb:PartyId>
            <eb:Role>http://docs.oasis-open.org/ebxml-msg/ebms/v3.0/ns/core/200704/initiator</eb:Role>
          </eb:From>
          <eb:To>
            <eb:PartyId type="urn:oasis:names:tc:ebcore:partyid-type:unregistered">FISKAL 2</eb:PartyId>
            <eb:Role>http://docs.oasis-open.org/ebxml-msg/ebms/v3.0/ns/core/200704/responder</eb:Role>
          </eb:To>
        </eb:PartyInfo>
        <eb:CollaborationInfo>
          <eb:Service type="cenbii-procid-ubl">urn:fdc:eracun.hr:poacc:en16931:any</eb:Service>
          <eb:Action>busdox-docid-qns::urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice</eb:Action>
        </eb:CollaborationInfo>
        <eb:MessageProperties>
          <eb:Property name="originalSender">9934:11528564544</eb:Property>
          <eb:Property name="finalRecipient">9934:{customer_oib}</eb:Property>
        </eb:MessageProperties>
      </eb:UserMessage>
    </eb:Messaging>
  </soap:Header>
  <soap:Body>
    <submitMessage xmlns="eu.domibus">
      <payload payloadId="cid:message" contentType="text/xml">
        <value>{encoded}</value>
      </payload>
    </submitMessage>
  </soap:Body>
</soap:Envelope>""".encode('utf-8')


@pytest.fixture
def invoice_root():
    return etree.parse(str(INVOICE_XML)).getroot()


class TestDomibusPushParser:
    def test_parse_submit_message(self):
        payload = _wrap_invoice_sbdh(INVOICE_XML.read_bytes())
        soap = _sample_push_soap(payload)
        message = parse_domibus_push(soap)
        assert message.operation == 'submitMessage'
        assert message.message_id == 'pts-msg-001@porezna.hr'
        assert message.from_party_id == 'domieracuntest'
        assert message.to_party_id == 'FISKAL 2'
        assert b'Invoice' in message.payload_xml

    def test_parse_invalid_xml(self):
        with pytest.raises(DomibusPushParseError):
            parse_domibus_push(b'not-xml')

    def test_build_ack_response(self):
        body = build_push_ack_response('submitMessage', message_id='abc@domibus.eu')
        assert b'submitMessageResponse' in body


class TestInboundValidation:
    def test_valid_invoice(self, invoice_root, settings):
        settings.DOMIBUS_AP_OIB = '99999999994'
        result = validate_inbound_invoice(invoice_root)
        assert result.accepted is True
        assert result.invoice_id == '13062026-TP-5054'

    def test_rejects_wrong_customer(self, invoice_root, settings):
        settings.DOMIBUS_AP_OIB = '36619131370'
        result = validate_inbound_invoice(invoice_root)
        assert result.accepted is False
        assert any('36619131370' in err for err in result.errors)


class TestApplicationResponse:
    def test_build_accept_response(self, invoice_root):
        payload = build_application_response_xml(
            invoice_root=invoice_root,
            receiver_oib='36619131370',
            accepted=True,
        )
        root = etree.fromstring(payload.xml)
        code = root.findtext('.//cbc:ResponseCode', namespaces=UBL_NS)
        assert code == RESPONSE_CODE_ACCEPT

    def test_build_reject_response(self, invoice_root):
        payload = build_application_response_xml(
            invoice_root=invoice_root,
            receiver_oib='36619131370',
            accepted=False,
            reason='Test razlog',
        )
        root = etree.fromstring(payload.xml)
        code = root.findtext('.//cbc:ResponseCode', namespaces=UBL_NS)
        assert code == RESPONSE_CODE_REJECT

    def test_wrap_sbdh(self, invoice_root):
        payload = build_application_response_xml(
            invoice_root=invoice_root,
            receiver_oib='36619131370',
            accepted=True,
        )
        sbdh = wrap_application_response_sbdh(
            payload.xml,
            sender_oib='36619131370',
            receiver_oib='11528564544',
            response_id=payload.response_id,
        )
        assert b'StandardBusinessDocument' in sbdh
        assert b'ApplicationResponse' in sbdh


class TestInboundHandler:
    @patch('fiscal_gateway.services.inbound_as4.resolve_tenant_for_customer_oib', return_value=None)
    @patch('fiscal_gateway.services.inbound_as4.submit_application_response_via_domibus')
    def test_handle_push_sends_application_response(self, mock_submit, _mock_tenant, settings):
        settings.DOMIBUS_AP_OIB = '99999999994'
        mock_submit.return_value = As4ApplicationResponseResult(
            message_id='resp-001@racunai.hr',
            domibus_response='<ok/>',
        )
        payload = _wrap_invoice_sbdh(INVOICE_XML.read_bytes())
        message = parse_domibus_push(_sample_push_soap(payload))
        result = handle_inbound_push(message)
        assert result.accepted is True
        assert result.response_message_id == 'resp-001@racunai.hr'
        mock_submit.assert_called_once()


class TestAs4InboundView:
    @patch('fiscal_gateway.api.views.as4_inbound.handle_inbound_push')
    @patch('fiscal_gateway.api.views.as4_inbound.parse_domibus_push')
    def test_post_returns_soap_ack(self, mock_parse, mock_handle):
        from django.test import RequestFactory

        from fiscal_gateway.api.views.as4_inbound import As4InboundPushView
        from fiscal_gateway.client.domibus_push import DomibusPushMessage
        from fiscal_gateway.services.inbound_as4 import InboundAs4Result, InboundValidationResult

        mock_parse.return_value = DomibusPushMessage(
            operation='submitMessage',
            message_id='pts-msg-001@porezna.hr',
            ref_to_message_id='',
            conversation_id='conv-001',
            action='invoice',
            service='svc',
            from_party_id='domieracuntest',
            to_party_id='FISKAL 2',
            original_sender='9934:11528564544',
            final_recipient='9934:36619131370',
            payload_xml=b'<Invoice/>',
        )
        mock_handle.return_value = InboundAs4Result(
            accepted=True,
            invoice_id='13062026-TP-5054',
            response_message_id='resp-001@racunai.hr',
            validation=InboundValidationResult(True, (), '13062026-TP-5054', '11528564544', '36619131370'),
        )

        request = RequestFactory().post(
            '/api/fiscal/as4/inbound/',
            data=b'<soap/>',
            content_type='application/soap+xml',
        )
        response = As4InboundPushView.as_view()(request)
        assert response.status_code == 200
        assert b'submitMessageResponse' in response.content
