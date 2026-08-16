import json
from pathlib import Path

import pytest
from lxml import etree

from ubl.builder.invoice import render_invoice_ubl
from ubl.builder.registry import supported_versions
from ubl.domain.document import UblDocument
from ubl.domain.errors import SchematronValidationError
from ubl.validators.schematron import schematron_available, validate_schematron
from ubl.validators.semantic import validate_semantic
from ubl.validators.structural import structural_compare
from ubl.validators.xsd import validate_xsd

GOLDEN_VERSIONS = supported_versions() or ['2025']
GOLDEN_DIR = Path(__file__).resolve().parent / 'golden' / 'hrcius2025'
FIXTURE_NAMES = [
    'invoice_finestar_reference',
    'invoice_minimal',
    'invoice_standard',
    'credit_note',
    'export_invoice',
    'reverse_charge',
]


def _load_document(name: str) -> UblDocument:
    payload = json.loads((GOLDEN_DIR / 'fixtures' / f'{name}.json').read_text())
    return UblDocument.model_validate(payload)


@pytest.mark.parametrize('profile_version', GOLDEN_VERSIONS)
@pytest.mark.parametrize('fixture_name', FIXTURE_NAMES)
def test_golden_pipeline(profile_version, fixture_name):
    document = _load_document(fixture_name)
    validate_semantic(document)
    xml = render_invoice_ubl(document, profile_version=profile_version)
    validate_xsd(xml)

    if schematron_available():
        validate_schematron(xml)

    golden_xml_path = GOLDEN_DIR / f'{fixture_name}.xml'
    if golden_xml_path.exists():
        golden_xml = golden_xml_path.read_text()
        diffs = structural_compare(xml, golden_xml)
        assert not diffs, diffs


@pytest.mark.skipif(not schematron_available(), reason='Schematron sheme nisu u repou')
def test_schematron_finestar_reference():
    document = _load_document('invoice_finestar_reference')
    xml = render_invoice_ubl(document)
    validate_schematron(xml)


@pytest.mark.skipif(not schematron_available(), reason='Schematron sheme nisu u repou')
def test_schematron_rejects_invalid_customization_id():
    document = _load_document('invoice_finestar_reference')
    xml = render_invoice_ubl(document)
    root = etree.fromstring(xml.encode('utf-8'))
    ns = {'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'}
    customization = root.find('cbc:CustomizationID', namespaces=ns)
    assert customization is not None
    customization.text = 'invalid-profile'
    bad_xml = etree.tostring(root, encoding='unicode')
    with pytest.raises(SchematronValidationError):
        validate_schematron(bad_xml)
