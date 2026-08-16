from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from lxml import etree

from fiscal_gateway.client.certificate import CertificateError, load_p12
from fiscal_gateway.client.cis_client import CISClient
from fiscal_gateway.services.validation import validate_efiskalizacija_xml
from fiscal_gateway.signing.builders import (
    FiscalIssuer,
    FiscalParty,
    OutgoingInvoicePayload,
    build_evidentiraj_eracun_zahtjev,
    demo_payload_from_options,
)
from fiscal_gateway.signing.xades import sign_fiscal_root


@pytest.fixture
def p12_path():
    candidates = [
        Path(settings.FISCAL_CERT_P12_PATH),
        Path('/run/secrets/fiscal-cert/36619131370.F2.2.p12'),
    ]
    for path in candidates:
        if path.is_file():
            return path
    pytest.skip('Demo .p12 nije dostupan')


@pytest.fixture
def p12_password():
    if not settings.FISCAL_CERT_P12_PASSWORD:
        secret = Path(__file__).resolve().parents[4] / '.certificates' / '.secret'
        if secret.is_file():
            for line in secret.read_text().splitlines():
                if '=' in line:
                    return line.split('=', 1)[1].strip()
        pytest.skip('FISCAL_CERT_P12_PASSWORD nije postavljen')
    return settings.FISCAL_CERT_P12_PASSWORD


class TestCertificate:
    def test_load_p12_success(self, p12_path, p12_password):
        material = load_p12(p12_path, p12_password)
        assert material.is_valid_now
        assert material.private_key_pem.startswith(b'-----BEGIN')
        assert material.certificate_pem.startswith(b'-----BEGIN')

    def test_load_p12_missing_file(self):
        with pytest.raises(CertificateError, match='nije pronađen'):
            load_p12('/nonexistent/cert.p12', 'x')

    def test_load_p12_wrong_password(self, p12_path):
        with pytest.raises(CertificateError, match='Ne mogu učitati'):
            load_p12(p12_path, 'wrong-password')


class TestBuilders:
    def test_build_evidentiraj_xml_structure(self):
        payload = demo_payload_from_options({'document_number': '99-P1-1'})
        root = build_evidentiraj_eracun_zahtjev(payload)
        xml = etree.tostring(root, encoding='unicode')
        assert 'EvidentirajERacunZahtjev' in xml
        assert '99-P1-1' in xml
        assert payload.issuer.oib in xml

    def test_sign_produces_signature(self, p12_path, p12_password):
        material = load_p12(p12_path, p12_password)
        payload = OutgoingInvoicePayload(
            document_number='TEST-SIGN-1',
            issue_date='2026-06-12',
            issuer=FiscalIssuer(name='Fine Star d.o.o.', oib='36619131370', operator_oib='11528564544'),
            recipient=FiscalParty(name='Test', oib='11111111119'),
        )
        root = build_evidentiraj_eracun_zahtjev(payload)
        signed = sign_fiscal_root(root, material)
        assert signed.find('.//{http://www.w3.org/2000/09/xmldsig#}Signature') is not None

    def test_signed_xml_passes_xsd(self, p12_path, p12_password):
        material = load_p12(p12_path, p12_password)
        payload = demo_payload_from_options({'document_number': 'XSD-1'})
        root = build_evidentiraj_eracun_zahtjev(payload)
        signed = sign_fiscal_root(root, material)
        validate_efiskalizacija_xml(signed)


class TestCISClient:
    def test_parse_rejected_response(self):
        raw = """<?xml version="1.0"?>
        <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
            xmlns:fis="http://www.porezna-uprava.gov.hr/fin/2024/types/eFiskalizacija">
          <soapenv:Body>
            <fis:EvidentirajERacunOdgovor fis:id="x">
              <fis:Odgovor>
                <fis:idZahtjeva>1</fis:idZahtjeva>
                <fis:prihvacenZahtjev>false</fis:prihvacenZahtjev>
                <fis:greska>
                  <fis:sifra>S003</fis:sifra>
                  <fis:opis>Certifikat nije izdan</fis:opis>
                </fis:greska>
              </fis:Odgovor>
            </fis:EvidentirajERacunOdgovor>
          </soapenv:Body>
        </soapenv:Envelope>"""
        parsed = CISClient._parse_response(raw)
        assert parsed.accepted is False
        assert parsed.error_code == 'S003'
        assert parsed.request_id == '1'

    @patch('fiscal_gateway.client.cis_client.requests.post')
    def test_submit_mock_success(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            text="""<?xml version="1.0"?>
            <Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/"
                xmlns:fis="http://www.porezna-uprava.gov.hr/fin/2024/types/eFiskalizacija">
              <Body>
                <fis:EvidentirajERacunOdgovor fis:id="x">
                  <fis:Odgovor>
                    <fis:idZahtjeva>42</fis:idZahtjeva>
                    <fis:prihvacenZahtjev>true</fis:prihvacenZahtjev>
                    <fis:JIR>JIR-TEST-123</fis:JIR>
                  </fis:Odgovor>
                </fis:EvidentirajERacunOdgovor>
              </Body>
            </Envelope>""",
        )
        config = MagicMock()
        config.cis_env = 'demo'
        client = CISClient(config)
        root = etree.Element('test')
        response = client.submit_signed_request(root)
        assert response.accepted is True
        assert response.jir == 'JIR-TEST-123'
