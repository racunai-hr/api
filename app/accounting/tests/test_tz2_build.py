"""TZ2 build gate and Alma unsigned XML asserts."""

from __future__ import annotations

from decimal import Decimal

from django.test import SimpleTestCase
from lxml import etree

from accounting.services.tax_forms.tz2.aggregate import Tz2BuildInput
from accounting.services.tax_forms.tz2.build import build_tz2_payload
from accounting.services.tax_forms.tz2.render import render_tz2_xml
from accounting.services.tax_forms.tz2.validation import Tz2ValidationError, validate_tz2_payload, validate_tz2_xml
from accounting.tests.fixtures.tax.tz2.load import load_scenario

_NS = 'http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacTZ2/v1-0'


def _input_from_fixture(raw: dict) -> tuple[int, Tz2BuildInput]:
    rate_keys = (
        'room_bed_rate',
        'aux_bed_rate',
        'camp_rate',
        'robinson_rate',
        'opg_room_bed_rate',
        'opg_aux_bed_rate',
        'opg_camp_rate',
        'opg_robinson_rate',
    )
    kwargs = {
        'first_name': raw['first_name'],
        'last_name': raw['last_name'],
        'oib': raw['oib'],
        'municipality_code': raw['municipality_code'],
        'city': raw['city'],
        'street': raw['street'],
        'house_number': raw['house_number'],
        'prepared_by_first_name': raw['prepared_by_first_name'],
        'prepared_by_last_name': raw['prepared_by_last_name'],
        'room_beds': raw.get('room_beds', 0),
        'aux_beds': raw.get('aux_beds', 0),
        'camp_units': raw.get('camp_units', 0),
        'robinson_units': raw.get('robinson_units', 0),
        'opg_room_beds': raw.get('opg_room_beds', 0),
        'opg_aux_beds': raw.get('opg_aux_beds', 0),
        'opg_camp_units': raw.get('opg_camp_units', 0),
        'opg_robinson_units': raw.get('opg_robinson_units', 0),
        'payment_installments': raw.get('payment_installments', False),
        'ep_receipts': Decimal(raw.get('ep_receipts', '0.00')),
    }
    for key in rate_keys:
        if key in raw:
            kwargs[key] = Decimal(raw[key])
    return raw['tax_year'], Tz2BuildInput(**kwargs)


class Tz2BuildTests(SimpleTestCase):
    def test_alma_2026_payload(self):
        data = load_scenario('alma_2026')
        year, build_input = _input_from_fixture(data['input'])
        payload = build_tz2_payload(year, build_input)
        expected = data['expected']
        self.assertEqual(payload.room_beds, 10)
        self.assertEqual(payload.aux_beds, 0)
        self.assertEqual(str(payload.room_bed_rate), expected['podatak02'])
        self.assertEqual(str(payload.opg_aux_bed_rate), expected['podatak17'])
        self.assertEqual(str(payload.room_bed_total), expected['podatak03'])
        self.assertEqual(str(payload.total_assessed), expected['podatak25'])
        self.assertEqual(payload.installment_flag, '1')
        self.assertEqual(str(payload.installment_amount), expected['podatak35'])
        self.assertEqual(str(payload.ep_receipts), expected['podatak36'])
        self.assertEqual(payload.taxpayer.house_number, '0009')
        self.assertEqual(payload.mapping_version, 1)

    def test_official_example_totals(self):
        data = load_scenario('official_example')
        year, build_input = _input_from_fixture(data['input'])
        payload = build_tz2_payload(year, build_input)
        self.assertEqual(str(payload.total_assessed), '392.50')
        self.assertEqual(payload.lump_sum_flag, '1')

    def test_missing_last_name_fails_validation(self):
        data = load_scenario('missing_last_name')
        year, build_input = _input_from_fixture(data['input'])
        payload = build_tz2_payload(year, build_input)
        with self.assertRaises(Tz2ValidationError):
            validate_tz2_payload(payload)


class Tz2AlmaUnsignedXmlTests(SimpleTestCase):
    def test_alma_unsigned_xml_contract(self):
        data = load_scenario('alma_2026')
        year, build_input = _input_from_fixture(data['input'])
        payload = build_tz2_payload(year, build_input)
        xml_bytes = render_tz2_xml(payload)
        validate_tz2_xml(xml_bytes)
        root = etree.fromstring(xml_bytes)
        self.assertEqual(root.tag, f'{{{_NS}}}ObrazacTZ2')
        self.assertEqual(root.get('verzijaSheme'), '1.0')
        self.assertEqual(root.findtext(f'{{{_NS}}}Tijelo/{{{_NS}}}Podatak01'), '10')
        self.assertEqual(root.findtext(f'{{{_NS}}}Tijelo/{{{_NS}}}Podatak17'), '1.99')
        self.assertEqual(root.findtext(f'{{{_NS}}}Tijelo/{{{_NS}}}Podatak34'), '1')
        self.assertEqual(root.findtext(f'{{{_NS}}}Tijelo/{{{_NS}}}Podatak36'), '17360.00')
