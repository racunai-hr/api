"""ObrazacPDVispravak matches the official schema and rejects a 0% EU invoice."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from accounting.services.tax_forms.pdv_ispravak.payload import (
    PdvIspravakPayload,
    PdvIspravakPreparer,
    PdvIspravakRow,
    PdvIspravakTaxpayer,
)
from accounting.services.tax_forms.pdv_ispravak.render import render_pdv_ispravak_xml
from accounting.services.tax_forms.pdv_ispravak.validation import (
    SCHEMA_DIR,
    PdvIspravakValidationError,
    validate_pdv_ispravak_payload,
    validate_pdv_ispravak_xml,
)

_OFFICIAL_EXAMPLE = SCHEMA_DIR / 'ObrazacPDVispravak1.xml'


def _taxpayer() -> PdvIspravakTaxpayer:
    return PdvIspravakTaxpayer(
        name='FINE STAR D.O.O.',
        oib='36619131370',
        city='Šibenik',
        street='Bana Josipa Jelačića',
        house_number='58',
        phone='+38522333444',
        email='avrcanus@gmail.com',
    )


def _preparer() -> PdvIspravakPreparer:
    return PdvIspravakPreparer(
        first_name='Toni',
        last_name='Šupe',
        place='Šibenik',
        prepared_on=date(2026, 10, 23),
    )


class PdvIspravakXmlTests(SimpleTestCase):
    def test_official_example_passes_xsd(self):
        validate_pdv_ispravak_xml(_OFFICIAL_EXAMPLE.read_bytes())

    def test_twenty_five_percent_row_renders_valid_xml(self):
        payload = PdvIspravakPayload(
            period_from=date(2026, 9, 1),
            period_to=date(2026, 9, 30),
            taxpayer=_taxpayer(),
            preparer=_preparer(),
            rows=(
                PdvIspravakRow(
                    invoice_number='2025-010',
                    invoice_date=date(2025, 6, 1),
                    identifier_kind=1,
                    identifier='12345678901',
                    customer_name='Kupac d.o.o.',
                    base_5=Decimal('0.00'),
                    vat_5=Decimal('0.00'),
                    base_13=Decimal('0.00'),
                    vat_13=Decimal('0.00'),
                    base_25=Decimal('100.00'),
                    vat_25=Decimal('25.00'),
                ),
            ),
        )
        validate_pdv_ispravak_payload(payload)
        xml_bytes = render_pdv_ispravak_xml(payload)
        validate_pdv_ispravak_xml(xml_bytes)
        self.assertIn(b'ObrazacPDVispravak', xml_bytes)
        self.assertNotIn(b'ObrazacPDV ', xml_bytes)
        self.assertIn(b'Isporuka25Osnovica>100.00<', xml_bytes)
        self.assertIn(b'Isporuka25Ispravak>25.00<', xml_bytes)
        self.assertIn(b'UkupnoOsnovica25>100.00<', xml_bytes)

    def test_july_zero_rate_eu_service_is_rejected(self):
        payload = PdvIspravakPayload(
            period_from=date(2026, 7, 1),
            period_to=date(2026, 7, 31),
            taxpayer=_taxpayer(),
            preparer=_preparer(),
            rows=(
                PdvIspravakRow(
                    invoice_number='INV-001/2026',
                    invoice_date=date(2026, 7, 27),
                    identifier_kind=2,
                    identifier='NL005402124B73',
                    customer_name='Asenova Diensten',
                    base_5=Decimal('0.00'),
                    vat_5=Decimal('0.00'),
                    base_13=Decimal('0.00'),
                    vat_13=Decimal('0.00'),
                    base_25=Decimal('0.00'),
                    vat_25=Decimal('0.00'),
                ),
            ),
        )
        with self.assertRaises(PdvIspravakValidationError):
            validate_pdv_ispravak_payload(payload)

    def test_empty_form_is_schema_valid_but_not_offered(self):
        payload = PdvIspravakPayload(
            period_from=date(2026, 9, 1),
            period_to=date(2026, 9, 30),
            taxpayer=_taxpayer(),
            preparer=_preparer(),
            rows=(),
        )
        with self.assertRaises(PdvIspravakValidationError):
            validate_pdv_ispravak_payload(payload)
        validate_pdv_ispravak_payload(payload, allow_empty=True)
        validate_pdv_ispravak_xml(render_pdv_ispravak_xml(payload))
