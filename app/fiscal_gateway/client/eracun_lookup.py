from __future__ import annotations

import base64
import hashlib
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests
from lxml import etree

AMS_DNS = 'dns1.hitronet.hr'
AMS_DOMAIN_DEMO = 'iso6523-actorid-upis.demo.ams.porezna-uprava.hr'
MPS_BASE_DEMO = 'https://cis.porezna-uprava.hr:8411/EracunMPSCT'
DOCUMENT_ID = (
    'busdox-docid-qns::urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice'
    '##urn:cen.eu:en16931:2017#compliant#urn:mfin.gov.hr:cius-2025:1.0'
    '#conformant#urn:mfin.gov.hr:ext-2025:1.0::2.1'
)
APPLICATION_RESPONSE_DOCUMENT_ID = (
    'busdox-docid-qns::urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2'
    '::ApplicationResponse##urn:cen.eu:en16931:2017#compliant#urn:mfin.gov.hr:cius-2025:1.0'
    '#conformant#urn:mfin.gov.hr:ext-2025:1.0::2.1'
)
INVOICE_NS = 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2'
APPLICATION_RESPONSE_NS = 'urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2'
UBL_NS = {
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
}


class EracunLookupError(Exception):
    pass


@dataclass(frozen=True)
class EracunParticipant:
    oib: str
    name: str = ''


@dataclass(frozen=True)
class EracunLookupResult:
    invoice_id: str
    supplier: EracunParticipant
    customer: EracunParticipant
    ams_naptr: str
    mps_url: str
    as4_endpoint: str
    responder_ap_party_id: str


def _cn_from_x509_subject(subject: str) -> str:
    match = re.search(r'(?:^|,)\s*CN\s*=\s*([^,]+)', subject)
    if not match:
        raise EracunLookupError(f'Certifikat nema CN u subjectu: {subject[:120]}')
    return match.group(1).strip()


def _ap_party_id_from_mps_xml(xml: bytes) -> str:
    root = etree.fromstring(xml)
    subject_name = root.findtext('.//{*}X509SubjectName')
    if subject_name:
        return _cn_from_x509_subject(subject_name)

    cert_b64 = root.findtext('.//{*}Certificate')
    if not cert_b64:
        raise EracunLookupError('MPS odgovor ne sadrži certifikat pristupne točke')

    cert_pem = (
        b'-----BEGIN CERTIFICATE-----\n'
        + cert_b64.encode()
        + b'\n-----END CERTIFICATE-----\n'
    )
    with tempfile.NamedTemporaryFile(suffix='.cer') as handle:
        handle.write(cert_pem)
        handle.flush()
        result = subprocess.run(
            ['openssl', 'x509', '-in', handle.name, '-noout', '-subject'],
            capture_output=True,
            text=True,
            check=False,
        )
    subject = (result.stdout or result.stderr or '').strip()
    if result.returncode != 0 or 'CN=' not in subject:
        raise EracunLookupError('Ne mogu pročitati CN iz MPS certifikata')
    openssl_subject = subject.split('subject=', 1)[-1]
    return _cn_from_x509_subject(openssl_subject)


def _ams_hash_label(oib: str) -> str:
    digest = hashlib.sha256(f'9934:{oib}'.encode()).digest()
    return base64.b32encode(digest).decode('ascii').rstrip('=')


def parse_invoice_xml(path: str | Path) -> tuple[str, EracunParticipant, EracunParticipant]:
    root = etree.parse(str(path)).getroot()
    invoice_id = root.findtext('cbc:ID', namespaces=UBL_NS) or ''

    def party_xpath(party_tag: str) -> EracunParticipant:
        oib = root.findtext(
            f'.//cac:{party_tag}/cac:Party/cbc:EndpointID',
            namespaces=UBL_NS,
        ) or ''
        name = root.findtext(
            f'.//cac:{party_tag}/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName',
            namespaces=UBL_NS,
        ) or ''
        return EracunParticipant(oib=oib.strip(), name=name.strip())

    return invoice_id, party_xpath('AccountingSupplierParty'), party_xpath('AccountingCustomerParty')


def lookup_recipient_endpoint(
    customer_oib: str,
    *,
    verify_ssl: bool = False,
    mps_base: str | None = None,
) -> tuple[str, str, str, str]:
    label = _ams_hash_label(customer_oib)
    naptr = ''
    try:
        import subprocess

        result = subprocess.run(
            ['dig', '+short', f'@{AMS_DNS}', f'{label}.{AMS_DOMAIN_DEMO}', 'NAPTR'],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        naptr = (result.stdout or '').strip()
    except FileNotFoundError:
        naptr = (
            f'(dig nedostupan — koristi se demo MPS {MPS_BASE_DEMO}; '
            f'NAPTR label: {label})'
        )

    mps_base_url = (mps_base or MPS_BASE_DEMO).rstrip('/')
    participant_path = quote(f'iso6523-actorid-upis::9934:{customer_oib}', safe='')
    doc_path = quote(DOCUMENT_ID, safe='')
    mps_request = f'{mps_base_url}/{participant_path}/services/{doc_path}'
    response = requests.get(
        mps_request,
        timeout=30,
        verify=verify_ssl,
        headers={'Accept': 'text/xml'},
    )
    if response.status_code != 200:
        raise EracunLookupError(f'MPS HTTP {response.status_code}: {response.text[:200]}')

    xml = etree.fromstring(response.content)
    endpoint = xml.findtext('.//{*}EndpointURI')
    if not endpoint:
        raise EracunLookupError('MPS odgovor ne sadrži EndpointURI')

    responder_ap_party_id = _ap_party_id_from_mps_xml(response.content)
    return naptr, mps_request, endpoint.strip(), responder_ap_party_id


def prepare_eracun_send(
    path: str | Path,
    *,
    verify_ssl: bool = False,
    mps_base: str | None = None,
) -> EracunLookupResult:
    invoice_id, supplier, customer = parse_invoice_xml(path)
    if not customer.oib:
        raise EracunLookupError('U XML-u nema AccountingCustomerParty/cbc:EndpointID')
    if not supplier.oib:
        raise EracunLookupError('U XML-u nema AccountingSupplierParty/cbc:EndpointID')

    naptr, mps_url, as4_endpoint, responder_ap_party_id = lookup_recipient_endpoint(
        customer.oib,
        verify_ssl=verify_ssl,
        mps_base=mps_base,
    )
    return EracunLookupResult(
        invoice_id=invoice_id,
        supplier=supplier,
        customer=customer,
        ams_naptr=naptr,
        mps_url=mps_url,
        as4_endpoint=as4_endpoint,
        responder_ap_party_id=responder_ap_party_id,
    )
