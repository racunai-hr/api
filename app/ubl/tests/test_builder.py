import json
from pathlib import Path

from ubl.builder.invoice import render_invoice_ubl
from ubl.domain.document import UblDocument

GOLDEN_DIR = Path(__file__).resolve().parent / 'golden' / 'hrcius2025'


def test_builder_emits_hrcius_customization_id():
    payload = json.loads((GOLDEN_DIR / 'fixtures' / 'invoice_minimal.json').read_text())
    document = UblDocument.model_validate(payload)
    xml = render_invoice_ubl(document)
    assert 'urn:mfin.gov.hr:cius-2025:1.0' in xml
    assert 'EndpointID' in xml
    assert 'TaxSubtotal' in xml
