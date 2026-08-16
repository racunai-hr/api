from __future__ import annotations

from pathlib import Path

from django.conf import settings
from lxml import etree
from signxml import methods
from signxml.algorithms import DigestAlgorithm, SignatureMethod
from signxml.xades import XAdESSigner

from fiscal_gateway.client.certificate import CertificateError, load_p12
from fiscal_gateway.config_utils import resolve_p12_password, resolve_p12_path
from fiscal_gateway.models import FiscalTenantConfig
from ubl.domain.errors import UblError

DS_NS = 'http://www.w3.org/2000/09/xmldsig#'
SAC_NS = 'urn:oasis:names:specification:ubl:schema:xsd:SignatureAggregateComponents-2'


class UblSigningError(UblError):
    """UBL XAdES signing failed."""


def _load_material(tenant) -> tuple[bytes, bytes]:
    config = FiscalTenantConfig.all_objects.filter(tenant=tenant, is_active=True).first()
    if config:
        try:
            material = load_p12(resolve_p12_path(config), resolve_p12_password(config))
            return material.private_key_pem, material.certificate_pem
        except CertificateError as exc:
            raise UblSigningError([str(exc)]) from exc

    default_path = getattr(settings, 'FISCAL_CERT_P12_PATH', '')
    default_password = getattr(settings, 'FISCAL_CERT_P12_PASSWORD', '')
    if default_path:
        try:
            material = load_p12(default_path, default_password)
            return material.private_key_pem, material.certificate_pem
        except CertificateError as exc:
            raise UblSigningError([str(exc)]) from exc

    raise UblSigningError(['Certifikat za UBL potpis nije konfiguriran'])


def sign_invoice_ubl(xml: str, *, tenant) -> str:
    """XAdES-B enveloped potpis UBL Invoice root elementa u UBLExtensions strukturi."""
    private_key_pem, certificate_pem = _load_material(tenant)
    root = etree.fromstring(xml.encode('utf-8'))
    sig_info = root.find(f'.//{{{SAC_NS}}}SignatureInformation')

    signer = XAdESSigner(
        method=methods.enveloped,
        signature_algorithm=SignatureMethod.RSA_SHA256,
        digest_algorithm=DigestAlgorithm.SHA256,
        c14n_algorithm='http://www.w3.org/2001/10/xml-exc-c14n#',
    )
    signed_root = signer.sign(
        root,
        key=private_key_pem,
        cert=certificate_pem,
    )

    signature = signed_root.find(f'.//{{{DS_NS}}}Signature')
    if signature is not None and sig_info is not None and signature.getparent() is not sig_info:
        parent = signature.getparent()
        if parent is not None:
            parent.remove(signature)
        sig_info.append(signature)

    return etree.tostring(
        signed_root,
        xml_declaration=True,
        encoding='UTF-8',
        pretty_print=False,
    ).decode('utf-8')
