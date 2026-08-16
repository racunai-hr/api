from __future__ import annotations

from lxml import etree
from signxml import XMLVerifier

DS_NS = 'http://www.w3.org/2000/09/xmldsig#'


class SignatureVerificationError(Exception):
    """UBL XAdES signature verification failed."""


def verify_invoice_signature(xml: str) -> None:
    """Verify enveloped XAdES signature on UBL Invoice root element."""
    root = etree.fromstring(xml.encode('utf-8'))
    signature = root.find(f'.//{{{DS_NS}}}Signature')
    if signature is None:
        raise SignatureVerificationError('UBL dokument nema ds:Signature element')

    try:
        XMLVerifier().verify(root, x509_cert=signature)
    except Exception as exc:
        raise SignatureVerificationError(str(exc)) from exc


def signature_present(xml: str) -> bool:
    root = etree.fromstring(xml.encode('utf-8'))
    return root.find(f'.//{{{DS_NS}}}Signature') is not None
