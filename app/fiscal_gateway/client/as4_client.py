from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from django.conf import settings
from lxml import etree
from zeep import Client, Settings

from fiscal_gateway.client.eracun_lookup import (
    APPLICATION_RESPONSE_DOCUMENT_ID,
    DOCUMENT_ID,
    EracunLookupError,
    parse_invoice_xml,
    prepare_eracun_send,
)
from fiscal_gateway.services.application_response import new_as4_message_id

SBDH_NS = 'http://www.unece.org/cefact/namespaces/StandardBusinessDocumentHeader'
EBMS_NS = 'http://docs.oasis-open.org/ebxml-msg/ebms/v3.0/ns/core/200704/'
SOAP_NS = 'http://www.w3.org/2003/05/soap-envelope'
INITIATOR_ROLE = f'{EBMS_NS}initiator'
RESPONDER_ROLE = f'{EBMS_NS}responder'
INITIATOR_PARTY_TYPE = 'urn:oasis:names:tc:ebcore:partyid-type:unregistered'
RESPONDER_PARTY_TYPE = INITIATOR_PARTY_TYPE
SERVICE_VALUE = 'urn:fdc:eracun.hr:poacc:en16931:any'
SERVICE_TYPE = 'cenbii-procid-ubl'


class As4SendError(Exception):
    pass


@dataclass(frozen=True)
class As4SendResult:
    message_id: str
    invoice_id: str
    supplier_oib: str
    customer_oib: str
    domibus_response: str


@dataclass(frozen=True)
class As4ApplicationResponseResult:
    message_id: str
    domibus_response: str


def _domibus_ws_url(override: str | None = None) -> str:
    base = (override or getattr(settings, 'DOMIBUS_WS_URL', '') or '').strip()
    if not base:
        raise As4SendError('DOMIBUS_WS_URL nije postavljen')
    return urljoin(base if base.endswith('/') else f'{base}/', 'services/wsplugin?wsdl')


def wrap_invoice_sbdh(invoice_xml: bytes, *, supplier_oib: str, customer_oib: str, invoice_id: str) -> bytes:
    inv_root = etree.fromstring(invoice_xml)
    root = etree.Element(f'{{{SBDH_NS}}}StandardBusinessDocument', nsmap={None: SBDH_NS})
    header = etree.SubElement(root, f'{{{SBDH_NS}}}StandardBusinessDocumentHeader')
    etree.SubElement(header, f'{{{SBDH_NS}}}HeaderVersion').text = '1.0'

    sender = etree.SubElement(header, f'{{{SBDH_NS}}}Sender')
    etree.SubElement(sender, f'{{{SBDH_NS}}}Identifier', Authority='iso6523-actorid-upis').text = (
        f'9934:{supplier_oib}'
    )

    receiver = etree.SubElement(header, f'{{{SBDH_NS}}}Receiver')
    etree.SubElement(receiver, f'{{{SBDH_NS}}}Identifier', Authority='iso6523-actorid-upis').text = (
        f'9934:{customer_oib}'
    )

    doc = etree.SubElement(header, f'{{{SBDH_NS}}}DocumentIdentification')
    etree.SubElement(doc, f'{{{SBDH_NS}}}Standard').text = (
        'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2'
    )
    etree.SubElement(doc, f'{{{SBDH_NS}}}TypeVersion').text = '2.1'
    etree.SubElement(doc, f'{{{SBDH_NS}}}InstanceIdentifier').text = invoice_id
    etree.SubElement(doc, f'{{{SBDH_NS}}}Type').text = 'Invoice'
    etree.SubElement(doc, f'{{{SBDH_NS}}}CreationDateAndTime').text = datetime.now(timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ'
    )
    root.append(inv_root)
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8')


def _ensure_messaging_header(envelope: etree._Element) -> None:
    header = envelope.find(f'{{{SOAP_NS}}}Header')
    if header is None:
        raise As4SendError('SOAP poruka nema Header')
    user_message = header.find(f'{{{EBMS_NS}}}UserMessage')
    if user_message is None:
        return
    if header.find(f'{{{EBMS_NS}}}Messaging') is not None:
        return
    header.remove(user_message)
    messaging = etree.Element(f'{{{EBMS_NS}}}Messaging')
    messaging.append(user_message)
    header.append(messaging)


def _extract_message_id(response_text: str) -> str:
    root = etree.fromstring(response_text.encode())
    message_id = root.findtext('.//{*}messageID')
    if not message_id:
        fault = root.findtext('.//{*}Text') or response_text[:300]
        raise As4SendError(f'Domibus submit neuspješan: {fault}')
    return message_id.strip()


def submit_invoice_xml_via_domibus(
    invoice_xml: str | bytes,
    *,
    initiator_ap_party_id: str | None = None,
    responder_ap_party_id: str | None = None,
    domibus_ws_url: str | None = None,
    mps_base: str | None = None,
    timeout: int = 120,
) -> As4SendResult:
    """Submit UBL Invoice XML (string/bytes) via Domibus WS plugin."""
    import tempfile

    xml_bytes = invoice_xml.encode('utf-8') if isinstance(invoice_xml, str) else invoice_xml
    with tempfile.NamedTemporaryFile(suffix='.xml', delete=False) as handle:
        handle.write(xml_bytes)
        temp_path = Path(handle.name)

    try:
        return submit_invoice_via_domibus(
            temp_path,
            initiator_ap_party_id=initiator_ap_party_id,
            responder_ap_party_id=responder_ap_party_id,
            domibus_ws_url=domibus_ws_url,
            mps_base=mps_base,
            timeout=timeout,
        )
    finally:
        temp_path.unlink(missing_ok=True)


def submit_invoice_via_domibus(
    invoice_path: str | Path,
    *,
    initiator_ap_party_id: str | None = None,
    responder_ap_party_id: str | None = None,
    domibus_ws_url: str | None = None,
    mps_base: str | None = None,
    timeout: int = 120,
) -> As4SendResult:
    path = Path(invoice_path)
    if not path.is_file():
        raise As4SendError(f'XML nije pronađen: {path}')

    lookup = prepare_eracun_send(
        path,
        verify_ssl=settings.FISCAL_CIS_VERIFY_SSL,
        mps_base=mps_base,
    )
    invoice_id, supplier, customer = lookup.invoice_id, lookup.supplier, lookup.customer

    from_party_id = (initiator_ap_party_id or getattr(settings, 'DOMIBUS_AP_PARTY_ID', 'FISKAL 2')).strip()
    to_party_id = (responder_ap_party_id or lookup.responder_ap_party_id).strip()
    if not to_party_id:
        raise EracunLookupError('MPS nije vratio CN pristupne točke primatelja')

    payload = wrap_invoice_sbdh(
        path.read_bytes(),
        supplier_oib=supplier.oib,
        customer_oib=customer.oib,
        invoice_id=invoice_id,
    )

    client = Client(_domibus_ws_url(domibus_ws_url), settings=Settings(strict=False))
    messaging = client.get_type('ns1:Messaging')(
        UserMessage={
            'PartyInfo': {
                'From': {
                    'PartyId': {'_value_1': from_party_id, 'type': INITIATOR_PARTY_TYPE},
                    'Role': INITIATOR_ROLE,
                },
                'To': {
                    'PartyId': {'_value_1': to_party_id, 'type': RESPONDER_PARTY_TYPE},
                    'Role': RESPONDER_ROLE,
                },
            },
            'CollaborationInfo': {
                'Service': {'_value_1': SERVICE_VALUE, 'type': SERVICE_TYPE},
                'Action': DOCUMENT_ID,
            },
            'MessageProperties': {
                'Property': [
                    {'name': 'originalSender', '_value_1': f'9934:{supplier.oib}'},
                    {'name': 'finalRecipient', '_value_1': f'9934:{customer.oib}'},
                ]
            },
            'PayloadInfo': {
                'PartInfo': [
                    {
                        'href': 'cid:message',
                        'PartProperties': {
                            'Property': [{'name': 'MimeType', '_value_1': 'text/xml'}],
                        },
                    }
                ]
            },
        }
    )

    envelope = client.create_message(
        client.service,
        'submitMessage',
        payload=[{'payloadId': 'cid:message', 'contentType': 'text/xml', 'value': payload}],
        _soapheaders=[messaging],
    )
    _ensure_messaging_header(envelope)

    ws_url = _domibus_ws_url(domibus_ws_url).replace('?wsdl', '')
    response = requests.post(
        ws_url,
        data=etree.tostring(envelope),
        headers={'Content-Type': 'application/soap+xml; charset=UTF-8'},
        timeout=timeout,
    )
    if response.status_code != 200:
        raise As4SendError(f'Domibus HTTP {response.status_code}: {response.text[:500]}')

    message_id = _extract_message_id(response.text)
    return As4SendResult(
        message_id=message_id,
        invoice_id=invoice_id,
        supplier_oib=supplier.oib,
        customer_oib=customer.oib,
        domibus_response=response.text,
    )


def submit_application_response_via_domibus(
    payload: bytes,
    *,
    to_ap_party_id: str,
    ref_to_message_id: str,
    conversation_id: str = '',
    supplier_oib: str,
    receiver_oib: str,
    initiator_ap_party_id: str | None = None,
    timeout: int = 120,
) -> As4ApplicationResponseResult:
    if not ref_to_message_id:
        raise As4SendError('RefToMessageId je obavezan za ApplicationResponse')

    from_party_id = (initiator_ap_party_id or getattr(settings, 'DOMIBUS_AP_PARTY_ID', 'FISKAL 2')).strip()
    to_party_id = to_ap_party_id.strip()
    if not to_party_id:
        raise As4SendError('Nedostaje AP party primatelja odgovora')

    message_info = {
        'Timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'MessageId': new_as4_message_id(),
        'RefToMessageId': ref_to_message_id,
    }
    if conversation_id:
        message_info['ConversationId'] = conversation_id

    client = Client(_domibus_ws_url(), settings=Settings(strict=False))
    messaging = client.get_type('ns1:Messaging')(
        UserMessage={
            'MessageInfo': message_info,
            'PartyInfo': {
                'From': {
                    'PartyId': {'_value_1': from_party_id, 'type': INITIATOR_PARTY_TYPE},
                    'Role': INITIATOR_ROLE,
                },
                'To': {
                    'PartyId': {'_value_1': to_party_id, 'type': RESPONDER_PARTY_TYPE},
                    'Role': RESPONDER_ROLE,
                },
            },
            'CollaborationInfo': {
                'Service': {'_value_1': SERVICE_VALUE, 'type': SERVICE_TYPE},
                'Action': APPLICATION_RESPONSE_DOCUMENT_ID,
            },
            'MessageProperties': {
                'Property': [
                    {'name': 'originalSender', '_value_1': f'9934:{receiver_oib}'},
                    {'name': 'finalRecipient', '_value_1': f'9934:{supplier_oib}'},
                ]
            },
            'PayloadInfo': {
                'PartInfo': [
                    {
                        'href': 'cid:message',
                        'PartProperties': {
                            'Property': [{'name': 'MimeType', '_value_1': 'text/xml'}],
                        },
                    }
                ]
            },
        }
    )

    envelope = client.create_message(
        client.service,
        'submitMessage',
        payload=[{'payloadId': 'cid:message', 'contentType': 'text/xml', 'value': payload}],
        _soapheaders=[messaging],
    )
    _ensure_messaging_header(envelope)

    ws_url = _domibus_ws_url().replace('?wsdl', '')
    response = requests.post(
        ws_url,
        data=etree.tostring(envelope),
        headers={'Content-Type': 'application/soap+xml; charset=UTF-8'},
        timeout=timeout,
    )
    if response.status_code != 200:
        raise As4SendError(f'Domibus HTTP {response.status_code}: {response.text[:500]}')

    message_id = _extract_message_id(response.text)
    return As4ApplicationResponseResult(message_id=message_id, domibus_response=response.text)
