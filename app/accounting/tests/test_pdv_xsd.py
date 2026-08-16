"""XSD validation for rendered Obrazac PDV v11.0 XML."""

from pathlib import Path

from django.test import SimpleTestCase
from lxml import etree

from accounting.services.tax_forms.pdv.parse import parse_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.payload import PdvFormHeader
from accounting.services.tax_forms.pdv.render import render_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.validation import (
    PDV_OBRAZAC_XSD,
    PdvSchemaValidationError,
    validate_pdv_obrazac_xml,
)

_FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'pdv'
_SUBMITTED_APRIL_2026 = _FIXTURES / 'submitted_april_2026.xml'
_SIGNATURE_NS = 'http://www.w3.org/2000/09/xmldsig#'


class PdvXsdValidationTests(SimpleTestCase):
    def test_schema_files_are_available_locally(self):
        self.assertTrue(PDV_OBRAZAC_XSD.is_file(), msg=str(PDV_OBRAZAC_XSD))
        etree.XMLSchema(etree.parse(str(PDV_OBRAZAC_XSD)))

    def test_rendered_unsigned_xml_passes_xsd(self):
        payload = parse_pdv_obrazac_xml(_SUBMITTED_APRIL_2026.read_bytes())
        header = PdvFormHeader(
            tax_office_code='3566',
            prepared_by_first_name='Zorana',
            prepared_by_last_name='Lambasa',
        )
        xml = render_pdv_obrazac_xml(payload, header=header)

        try:
            validate_pdv_obrazac_xml(xml)
        except PdvSchemaValidationError as exc:
            self.fail(f'Rendered PDV XML failed XSD validation: {exc}')

    def test_submitted_fixture_without_signature_passes_xsd(self):
        root = etree.fromstring(_SUBMITTED_APRIL_2026.read_bytes())
        for signature in root.findall(f'{{{_SIGNATURE_NS}}}Signature'):
            root.remove(signature)

        try:
            validate_pdv_obrazac_xml(root)
        except PdvSchemaValidationError as exc:
            self.fail(f'Reference PDV XML failed XSD validation: {exc}')

    def test_submitted_fixture_with_signature_passes_signed_validation(self):
        try:
            validate_pdv_obrazac_xml(_SUBMITTED_APRIL_2026.read_bytes(), signed=True)
        except PdvSchemaValidationError as exc:
            self.fail(f'Signed PDV XML failed validation: {exc}')

    def test_unsigned_fixture_rejects_signed_validation(self):
        root = etree.fromstring(_SUBMITTED_APRIL_2026.read_bytes())
        for signature in root.findall(f'{{{_SIGNATURE_NS}}}Signature'):
            root.remove(signature)
        xml = etree.tostring(root)

        with self.assertRaises(PdvSchemaValidationError):
            validate_pdv_obrazac_xml(xml, signed=True)
