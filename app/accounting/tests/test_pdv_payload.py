"""PdvPayload immutability, canonical JSON, parse/render round-trip."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from django.test import SimpleTestCase, TestCase

from accounting.services.tax_forms.pdv.canonical import canonical_json, payload_hash
from accounting.services.tax_forms.pdv.mapping import PDV_MAPPING_VERSION
from accounting.services.tax_forms.pdv.parse import parse_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.payload import (
    PDV_SCHEMA_VERSION,
    PdvFieldPair,
    PdvFormHeader,
    PdvPayload,
    TaxpayerInfo,
    freeze_fields,
)
from accounting.services.tax_forms.pdv.render import render_pdv_obrazac_xml

_FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'pdv'
_SUBMITTED_APRIL_2026 = _FIXTURES / 'submitted_april_2026.xml'
_EXPECTED_PAYLOAD = _FIXTURES / 'expected_payload_april_2026.json'


class PdvPayloadImmutabilityTests(SimpleTestCase):
    def test_frozen_payload_rejects_attribute_mutation(self):
        payload = PdvPayload(
            schema_version=PDV_SCHEMA_VERSION,
            mapping_version=PDV_MAPPING_VERSION,
            period_from=date(2026, 4, 1),
            period_to=date(2026, 4, 30),
            taxpayer=TaxpayerInfo(
                oib='36619131370',
                name='Fine Star d.o.o.',
                street='Bana Josipa Jelačića',
                house_number='58',
                city='Šibenik',
                postal_code='22000',
            ),
            fields=freeze_fields({'203': PdvFieldPair(Decimal('0.00'), Decimal('0.00'))}),
        )
        with self.assertRaises(AttributeError):
            payload.schema_version = '12.0'  # type: ignore[misc]

    def test_fields_mapping_is_read_only(self):
        fields = freeze_fields({'400': Decimal('-84.11')})
        payload = PdvPayload(
            schema_version=PDV_SCHEMA_VERSION,
            mapping_version=PDV_MAPPING_VERSION,
            period_from=date(2026, 4, 1),
            period_to=date(2026, 4, 30),
            taxpayer=TaxpayerInfo('36619131370', 'Fine Star', 'Ulica', '1', 'Grad', '10000'),
            fields=fields,
        )
        self.assertIsInstance(payload.fields, MappingProxyType)
        with self.assertRaises(TypeError):
            payload.fields['400'] = Decimal('0.00')  # type: ignore[index]


class PdvCanonicalTests(TestCase):
    def test_submitted_april_fixture_matches_expected_canonical_json(self):
        payload = parse_pdv_obrazac_xml(_SUBMITTED_APRIL_2026.read_bytes())
        expected = json.loads(_EXPECTED_PAYLOAD.read_text(encoding='utf-8'))
        self.assertEqual(json.loads(canonical_json(payload)), expected)
        self.assertEqual(len(payload_hash(payload)), 64)

    def test_round_trip_preserves_canonical_fields(self):
        original = parse_pdv_obrazac_xml(_SUBMITTED_APRIL_2026.read_bytes())
        header = PdvFormHeader(
            tax_office_code='3566',
            prepared_by_first_name='Zorana',
            prepared_by_last_name='Lambasa',
        )
        xml = render_pdv_obrazac_xml(original, header=header)
        parsed = parse_pdv_obrazac_xml(xml)

        self.assertEqual(canonical_json(parsed), canonical_json(original))
