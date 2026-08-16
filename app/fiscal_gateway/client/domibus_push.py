from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass

from lxml import etree

DOMIBUS_NS = 'eu.domibus'
WSPLUGIN_NS = 'http://eu.domibus.wsplugin'
EBMS_NS = 'http://docs.oasis-open.org/ebxml-msg/ebms/v3.0/ns/core/200704/'
SOAP_NS = 'http://www.w3.org/2003/05/soap-envelope'


class DomibusPushParseError(Exception):
    pass


@dataclass(frozen=True)
class DomibusPushMessage:
    operation: str
    message_id: str
    ref_to_message_id: str
    conversation_id: str
    action: str
    service: str
    from_party_id: str
    to_party_id: str
    original_sender: str
    final_recipient: str
    payload_xml: bytes
    error_code: str = ''
    error_detail: str = ''


def _local_name(tag: str | None) -> str:
    if not tag:
        return ''
    if '}' in tag:
        return tag.rsplit('}', 1)[-1]
    return tag


def _find_child(parent: etree._Element | None, local_name: str) -> etree._Element | None:
    if parent is None:
        return None
    for child in parent:
        if _local_name(child.tag) == local_name:
            return child
    return None


def _find_descendant(parent: etree._Element | None, local_name: str) -> etree._Element | None:
    if parent is None:
        return None
    found = parent.find(f'.//{{*}}{local_name}')
    return found


def _message_property(user_message: etree._Element | None, name: str) -> str:
    if user_message is None:
        return ''
    for prop in user_message.findall(f'.//{{{EBMS_NS}}}Property'):
        if prop.get('name') == name:
            return (prop.text or '').strip()
    for prop in user_message.findall('.//{*}Property'):
        if prop.get('name') == name:
            return (prop.text or '').strip()
    return ''


def _party_id(user_message: etree._Element | None, party_tag: str) -> str:
    party_info = _find_descendant(user_message, 'PartyInfo')
    if party_info is None:
        return ''
    party = _find_child(party_info, party_tag)
    party_id = _find_child(party, 'PartyId')
    return (party_id.text or '').strip() if party_id is not None else ''


def _decode_payload(body: etree._Element) -> bytes:
    values: list[str] = []
    for node in body.iter():
        if _local_name(node.tag) != 'value':
            continue
        text = (node.text or '').strip()
        if text:
            values.append(text)

    if not values:
        raise DomibusPushParseError('SOAP tijelo ne sadrži base64 payload')

    # Prefer the largest decoded payload (SBDH + UBL over attachment stubs).
    decoded_candidates = []
    for value in values:
        try:
            decoded_candidates.append(base64.b64decode(value, validate=False))
        except binascii.Error as exc:
            raise DomibusPushParseError(f'Nevaljan base64 payload: {exc}') from exc

    return max(decoded_candidates, key=len)


def _detect_operation(body: etree._Element) -> str:
    for child in body:
        name = _local_name(child.tag)
        if name in {
            'submitMessage',
            'submitRequest',
            'receiveFailure',
            'receiveSuccess',
            'sendFailure',
            'sendSuccess',
        }:
            return 'submitMessage' if name == 'submitRequest' else name
    raise DomibusPushParseError('Nepoznata Domibus push operacija')


def parse_domibus_push(soap_bytes: bytes) -> DomibusPushMessage:
    try:
        envelope = etree.fromstring(soap_bytes)
    except etree.XMLSyntaxError as exc:
        raise DomibusPushParseError(f'Nevaljan SOAP XML: {exc}') from exc

    header = envelope.find(f'{{{SOAP_NS}}}Header')
    body = envelope.find(f'{{{SOAP_NS}}}Body')
    if body is None:
        raise DomibusPushParseError('SOAP Envelope nema Body')

    operation = _detect_operation(body)
    messaging = None
    if header is not None:
        messaging = header.find(f'{{{EBMS_NS}}}Messaging')
        if messaging is None:
            messaging = header.find('.//{*}Messaging')

    user_message = _find_descendant(messaging, 'UserMessage')
    message_info = _find_descendant(user_message, 'MessageInfo')
    collaboration = _find_descendant(user_message, 'CollaborationInfo')

    message_id_node = _find_child(message_info, 'MessageId')
    message_id = (message_id_node.text or '').strip() if message_id_node is not None else ''
    ref_node = _find_child(message_info, 'RefToMessageId')
    ref_to_message_id = (ref_node.text or '').strip() if ref_node is not None else ''
    conversation_node = _find_child(message_info, 'ConversationId')
    conversation_id = (conversation_node.text or '').strip() if conversation_node is not None else ''

    service_node = _find_child(collaboration, 'Service')
    action_node = _find_child(collaboration, 'Action')
    service = (service_node.text or service_node.get('value') or '').strip() if service_node is not None else ''
    action = (action_node.text or action_node.get('value') or '').strip() if action_node is not None else ''

    original_sender = _message_property(user_message, 'originalSender')
    final_recipient = _message_property(user_message, 'finalRecipient')

    payload_xml = b''
    error_code = ''
    error_detail = ''
    if operation == 'submitMessage':
        payload_xml = _decode_payload(body)
    elif operation == 'receiveFailure':
        error_code = (_find_descendant(body, 'ErrorCode').text or '').strip()
        error_detail = (_find_descendant(body, 'ErrorDetail').text or '').strip()

    return DomibusPushMessage(
        operation=operation,
        message_id=message_id,
        ref_to_message_id=ref_to_message_id,
        conversation_id=conversation_id,
        action=action,
        service=service,
        from_party_id=_party_id(user_message, 'From'),
        to_party_id=_party_id(user_message, 'To'),
        original_sender=original_sender,
        final_recipient=final_recipient,
        payload_xml=payload_xml,
        error_code=error_code,
        error_detail=error_detail,
    )


def build_push_ack_response(operation: str, *, message_id: str = '') -> bytes:
    op = operation
    if op == 'submitRequest':
        op = 'submitMessage'

    response_local = {
        'submitMessage': 'submitMessageResponse',
        'receiveFailure': 'receiveFailureResponse',
        'receiveSuccess': 'receiveSuccessResponse',
        'sendFailure': 'sendFailureResponse',
        'sendSuccess': 'sendSuccessResponse',
    }.get(op, 'submitMessageResponse')

    ns = DOMIBUS_NS
    envelope = etree.Element(f'{{{SOAP_NS}}}Envelope', nsmap={'soap': SOAP_NS})
    body = etree.SubElement(envelope, f'{{{SOAP_NS}}}Body')
    response = etree.SubElement(body, f'{{{ns}}}{response_local}')
    if message_id and response_local == 'submitMessageResponse':
        etree.SubElement(response, f'{{{ns}}}messageID').text = message_id
    return etree.tostring(
        envelope,
        xml_declaration=True,
        encoding='UTF-8',
    )


def oib_from_participant(value: str) -> str:
    value = (value or '').strip()
    match = re.search(r'(\d{11})', value)
    return match.group(1) if match else value
