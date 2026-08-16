from __future__ import annotations

from dataclasses import dataclass

import requests
from django.conf import settings
from lxml import etree

from fiscal_gateway.config_utils import resolve_cis_env, resolve_p12_password, resolve_p12_path
from fiscal_gateway.constants import CIS_ENDPOINTS, NS_EFISKALIZACIJA, NS_SOAP
from fiscal_gateway.models import FiscalTenantConfig

from .certificate import load_p12


class CISClientError(Exception):
    """Raised when CIS communication fails."""


@dataclass
class CISResponse:
    http_status: int
    raw_xml: str
    accepted: bool | None = None
    request_id: str = ''
    jir: str = ''
    error_code: str = ''
    error_message: str = ''


class CISClient:
    def __init__(self, config: FiscalTenantConfig):
        self.config = config
        self.cis_env = resolve_cis_env(config)
        override = (getattr(settings, 'FISCAL_CIS_ENDPOINT', '') or '').strip()
        self.endpoint = override or CIS_ENDPOINTS.get(self.cis_env)
        if not self.endpoint:
            raise CISClientError(f'Nepoznata CIS okolina: {self.cis_env}')
        self.verify_ssl = settings.FISCAL_CIS_VERIFY_SSL

    def ping(self, timeout: float = 20.0) -> CISResponse:
        """Provjera dostupnosti CIS-a (GET na endpoint)."""
        try:
            response = requests.get(
                self.endpoint,
                timeout=timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException as exc:
            raise CISClientError(f'CIS nedostupan ({self.endpoint}): {exc}') from exc
        return CISResponse(http_status=response.status_code, raw_xml=response.text[:500])

    def submit_signed_request(self, signed_root: etree._Element, timeout: float = 60.0) -> CISResponse:
        soap_envelope = self._wrap_soap(signed_root)
        soap_bytes = etree.tostring(
            soap_envelope,
            xml_declaration=True,
            encoding='UTF-8',
            standalone=True,
        )
        headers = {
            'Content-Type': 'text/xml; charset=utf-8',
            'SOAPAction': '',
        }
        try:
            response = requests.post(
                self.endpoint,
                data=soap_bytes,
                headers=headers,
                timeout=timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException as exc:
            raise CISClientError(f'CIS POST greška: {exc}') from exc

        raw = response.text
        parsed = self._parse_response(raw)
        parsed.http_status = response.status_code
        parsed.raw_xml = raw
        return parsed

    def load_certificate(self):
        return load_p12(
            resolve_p12_path(self.config),
            resolve_p12_password(self.config),
        )

    @staticmethod
    def _wrap_soap(signed_root: etree._Element) -> etree._Element:
        envelope = etree.Element(
            f'{{{NS_SOAP}}}Envelope',
            nsmap={'S': NS_SOAP, 'SOAP-ENV': NS_SOAP},
        )
        etree.SubElement(envelope, f'{{{NS_SOAP}}}Header')
        body = etree.SubElement(envelope, f'{{{NS_SOAP}}}Body')
        body.append(signed_root)
        return envelope

    @staticmethod
    def _parse_response(raw_xml: str) -> CISResponse:
        result = CISResponse(http_status=0, raw_xml=raw_xml)
        if not raw_xml.strip():
            return result

        try:
            root = etree.fromstring(raw_xml.encode('utf-8'))
        except etree.XMLSyntaxError:
            return result

        ns = {'fis': NS_EFISKALIZACIJA}
        odgovor = root.find('.//fis:EvidentirajERacunOdgovor', ns)
        if odgovor is None:
            odgovor = root.find('.//{*}EvidentirajERacunOdgovor')

        if odgovor is None:
            return result

        prihvacen = odgovor.find('.//fis:prihvacenZahtjev', ns)
        if prihvacen is None:
            prihvacen = odgovor.find('.//{*}prihvacenZahtjev')
        if prihvacen is not None and prihvacen.text is not None:
            result.accepted = prihvacen.text.strip().lower() == 'true'

        id_zahtjeva = odgovor.find('.//fis:idZahtjeva', ns)
        if id_zahtjeva is None:
            id_zahtjeva = odgovor.find('.//{*}idZahtjeva')
        if id_zahtjeva is not None and id_zahtjeva.text:
            result.request_id = id_zahtjeva.text.strip()

        jir = odgovor.find('.//fis:JIR', ns)
        if jir is None:
            jir = odgovor.find('.//{*}JIR')
        if jir is not None and jir.text:
            result.jir = jir.text.strip()

        greska = odgovor.find('.//fis:greska', ns)
        if greska is None:
            greska = odgovor.find('.//{*}greska')
        if greska is not None:
            sifra = greska.find('.//fis:sifra', ns)
            if sifra is None:
                sifra = greska.find('.//{*}sifra')
            opis = greska.find('.//fis:opis', ns)
            if opis is None:
                opis = greska.find('.//{*}opis')
            if sifra is not None and sifra.text:
                result.error_code = sifra.text.strip()
            if opis is not None and opis.text:
                result.error_message = opis.text.strip()

        return result

    @staticmethod
    def summarize_response(response: CISResponse) -> str:
        if response.accepted:
            parts = ['Zahtjev prihvaćen']
            if response.jir:
                parts.append(f'JIR={response.jir}')
            if response.request_id:
                parts.append(f'idZahtjeva={response.request_id}')
            return ', '.join(parts)
        if response.error_code or response.error_message:
            return f'{response.error_code}: {response.error_message}'.strip(': ')
        return f'HTTP {response.http_status}'
