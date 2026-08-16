from __future__ import annotations

from unittest.mock import patch

import pytest

from ubl.signing.verify import SignatureVerificationError, signature_present, verify_invoice_signature


def test_signature_present_false_on_unsigned():
    assert not signature_present('<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>')


def test_verify_raises_without_signature():
    with pytest.raises(SignatureVerificationError, match='nema ds:Signature'):
        verify_invoice_signature(
            '<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'
        )


@patch('ubl.signing.verify.XMLVerifier')
def test_verify_delegates_to_signxml(mock_verifier_cls):
    mock_verifier_cls.return_value.verify.return_value = True
    xml = (
        '<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">'
        '<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#"/>'
        '</Invoice>'
    )
    verify_invoice_signature(xml)
    mock_verifier_cls.return_value.verify.assert_called_once()
