from __future__ import annotations

import uuid
from datetime import datetime, timezone

from lxml import etree
from signxml import methods
from signxml.algorithms import DigestAlgorithm, SignatureMethod
from signxml.xades import XAdESSigner

from fiscal_gateway.client.certificate import CertificateMaterial


def sign_fiscal_root(root: etree._Element, material: CertificateMaterial) -> etree._Element:
    """XAdES-B enveloped potpis root elementa (EvidentirajERacunZahtjev)."""
    signer = XAdESSigner(
        method=methods.enveloped,
        signature_algorithm=SignatureMethod.RSA_SHA256,
        digest_algorithm=DigestAlgorithm.SHA256,
        c14n_algorithm='http://www.w3.org/2001/10/xml-exc-c14n#',
    )
    return signer.sign(
        root,
        key=material.private_key_pem,
        cert=material.certificate_pem,
    )


def new_request_id() -> str:
    return f'id-{uuid.uuid4().hex}'


def fiscal_timestamp() -> str:
    """ISO timestamp za Zaglavlje — pattern YYYY-MM-DDTHH:MM:SS.mmmm (4 decimale)."""
    now = datetime.now(timezone.utc)
    return now.strftime('%Y-%m-%dT%H:%M:%S') + f'.{now.microsecond // 100:04d}'
