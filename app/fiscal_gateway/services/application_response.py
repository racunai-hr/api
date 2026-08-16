from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from lxml import etree

from fiscal_gateway.client.eracun_lookup import (
    APPLICATION_RESPONSE_NS,
    INVOICE_NS,
    UBL_NS,
)

HR_CUSTOMIZATION_ID = (
    'urn:cen.eu:en16931:2017#compliant#urn:mfin.gov.hr:cius-2025:1.0'
    '#conformant#urn:mfin.gov.hr:ext-2025:1.0'
)
SBDH_NS = 'http://www.unece.org/cefact/namespaces/StandardBusinessDocumentHeader'
RESPONSE_CODE_ACCEPT = 'AP'
RESPONSE_CODE_REJECT = 'RE'


@dataclass(frozen=True)
class ApplicationResponsePayload:
    response_id: str
    xml: bytes


def _find_invoice_element(payload_root: etree._Element) -> etree._Element | None:
    if payload_root.tag == f'{{{INVOICE_NS}}}Invoice':
        return payload_root
    return payload_root.find(f'.//{{{INVOICE_NS}}}Invoice')


def build_application_response_xml(
    *,
    invoice_root: etree._Element,
    receiver_oib: str,
    accepted: bool,
    reason: str = '',
    status_reason_code: str = 'O',
) -> ApplicationResponsePayload:
    invoice_id = invoice_root.findtext('cbc:ID', namespaces=UBL_NS) or 'unknown'
    supplier_oib = invoice_root.findtext(
        './/cac:AccountingSupplierParty/cac:Party/cbc:EndpointID',
        namespaces=UBL_NS,
    ) or ''
    invoice_type_code = invoice_root.findtext('cbc:InvoiceTypeCode', namespaces=UBL_NS) or '380'
    profile_id = invoice_root.findtext('cbc:ProfileID', namespaces=UBL_NS) or 'P1'

    now = datetime.now(timezone.utc)
    response_id = f'AR-{invoice_id}-{now.strftime("%Y%m%d%H%M%S")}'

    root = etree.Element(
        f'{{{APPLICATION_RESPONSE_NS}}}ApplicationResponse',
        nsmap={
            None: APPLICATION_RESPONSE_NS,
            'cac': UBL_NS['cac'],
            'cbc': UBL_NS['cbc'],
        },
    )
    etree.SubElement(root, f'{{{UBL_NS["cbc"]}}}CustomizationID').text = HR_CUSTOMIZATION_ID
    etree.SubElement(root, f'{{{UBL_NS["cbc"]}}}ProfileID').text = profile_id
    etree.SubElement(root, f'{{{UBL_NS["cbc"]}}}ID').text = response_id
    etree.SubElement(root, f'{{{UBL_NS["cbc"]}}}IssueDate').text = now.strftime('%Y-%m-%d')
    etree.SubElement(root, f'{{{UBL_NS["cbc"]}}}IssueTime').text = now.strftime('%H:%M:%S')

    sender = etree.SubElement(root, f'{{{UBL_NS["cac"]}}}SenderParty')
    sender_party = etree.SubElement(sender, f'{{{UBL_NS["cac"]}}}Party')
    etree.SubElement(sender_party, f'{{{UBL_NS["cbc"]}}}EndpointID', schemeID='9934').text = receiver_oib
    sender_legal = etree.SubElement(sender_party, f'{{{UBL_NS["cac"]}}}PartyLegalEntity')
    etree.SubElement(sender_legal, f'{{{UBL_NS["cbc"]}}}RegistrationName').text = receiver_oib

    receiver = etree.SubElement(root, f'{{{UBL_NS["cac"]}}}ReceiverParty')
    receiver_party = etree.SubElement(receiver, f'{{{UBL_NS["cac"]}}}Party')
    etree.SubElement(receiver_party, f'{{{UBL_NS["cbc"]}}}EndpointID', schemeID='9934').text = supplier_oib
    receiver_legal = etree.SubElement(receiver_party, f'{{{UBL_NS["cac"]}}}PartyLegalEntity')
    etree.SubElement(receiver_legal, f'{{{UBL_NS["cbc"]}}}RegistrationName').text = supplier_oib

    document_response = etree.SubElement(root, f'{{{UBL_NS["cac"]}}}DocumentResponse')
    response = etree.SubElement(document_response, f'{{{UBL_NS["cac"]}}}Response')
    response_code = RESPONSE_CODE_ACCEPT if accepted else RESPONSE_CODE_REJECT
    etree.SubElement(
        response,
        f'{{{UBL_NS["cbc"]}}}ResponseCode',
        listID='UNCL4343',
    ).text = response_code
    etree.SubElement(response, f'{{{UBL_NS["cbc"]}}}Description').text = (
        'eRačun zaprimljen i validiran.' if accepted else (reason or 'eRačun nije validan.')
    )
    if not accepted:
        status = etree.SubElement(response, f'{{{UBL_NS["cac"]}}}Status')
        etree.SubElement(
            status,
            f'{{{UBL_NS["cbc"]}}}StatusReasonCode',
            listID='MFINVrstaRazlogaOdbijanja',
        ).text = status_reason_code

    document_reference = etree.SubElement(document_response, f'{{{UBL_NS["cac"]}}}DocumentReference')
    etree.SubElement(document_reference, f'{{{UBL_NS["cbc"]}}}ID').text = invoice_id
    etree.SubElement(
        document_reference,
        f'{{{UBL_NS["cbc"]}}}DocumentTypeCode',
        listID='UNCL1001',
    ).text = invoice_type_code

    xml = etree.tostring(root, xml_declaration=True, encoding='UTF-8')
    return ApplicationResponsePayload(response_id=response_id, xml=xml)


def wrap_application_response_sbdh(
    application_response_xml: bytes,
    *,
    sender_oib: str,
    receiver_oib: str,
    response_id: str,
) -> bytes:
    app_root = etree.fromstring(application_response_xml)
    root = etree.Element(f'{{{SBDH_NS}}}StandardBusinessDocument', nsmap={None: SBDH_NS})
    header = etree.SubElement(root, f'{{{SBDH_NS}}}StandardBusinessDocumentHeader')
    etree.SubElement(header, f'{{{SBDH_NS}}}HeaderVersion').text = '1.0'

    sender = etree.SubElement(header, f'{{{SBDH_NS}}}Sender')
    etree.SubElement(sender, f'{{{SBDH_NS}}}Identifier', Authority='iso6523-actorid-upis').text = (
        f'9934:{sender_oib}'
    )

    receiver = etree.SubElement(header, f'{{{SBDH_NS}}}Receiver')
    etree.SubElement(receiver, f'{{{SBDH_NS}}}Identifier', Authority='iso6523-actorid-upis').text = (
        f'9934:{receiver_oib}'
    )

    doc = etree.SubElement(header, f'{{{SBDH_NS}}}DocumentIdentification')
    etree.SubElement(doc, f'{{{SBDH_NS}}}Standard').text = (
        'urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2'
    )
    etree.SubElement(doc, f'{{{SBDH_NS}}}TypeVersion').text = '2.1'
    etree.SubElement(doc, f'{{{SBDH_NS}}}InstanceIdentifier').text = response_id
    etree.SubElement(doc, f'{{{SBDH_NS}}}Type').text = 'ApplicationResponse'
    etree.SubElement(doc, f'{{{SBDH_NS}}}CreationDateAndTime').text = datetime.now(timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ'
    )
    root.append(app_root)
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8')


def new_as4_message_id() -> str:
    return f'{uuid4()}@racunai.hr'
