"""Deterministic sort order for compare_pdv_payload_fields()."""

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from accounting.services.tax_forms.pdv.diff import (
    PdvFieldDifference,
    PdvFieldDifferenceKind,
    compare_pdv_payload_fields,
)
from accounting.services.tax_forms.pdv.mapping import PDV_MAPPING_VERSION
from accounting.services.tax_forms.pdv.payload import (
    PDV_SCHEMA_VERSION,
    PdvFieldPair,
    PdvPayload,
    TaxpayerInfo,
    freeze_fields,
)


def _payload(fields: dict) -> PdvPayload:
    return PdvPayload(
        schema_version=PDV_SCHEMA_VERSION,
        mapping_version=PDV_MAPPING_VERSION,
        period_from=date(2026, 4, 1),
        period_to=date(2026, 4, 30),
        taxpayer=TaxpayerInfo('36619131370', 'Fine Star', 'Ulica', '1', 'Grad', '10000'),
        fields=freeze_fields(fields),
    )


class ComparePdvPayloadFieldsSortOrderTests(SimpleTestCase):
    def test_sort_order_is_kind_then_field(self):
        expected = _payload({
            '203': PdvFieldPair(Decimal('0.00'), Decimal('0.00')),
            '303': PdvFieldPair(Decimal('336.43'), Decimal('84.11')),
            '400': Decimal('-84.11'),
            '610': Decimal('0.00'),
        })
        actual = _payload({
            '203': PdvFieldPair(Decimal('0.00'), Decimal('0.00')),
            '303': PdvFieldPair(Decimal('336.43'), Decimal('85.11')),
            '400': Decimal('-83.11'),
            '620': Decimal('1.00'),
        })

        differences = compare_pdv_payload_fields(expected, actual)
        self.assertEqual(
            differences,
            [
                PdvFieldDifference(
                    field='620',
                    kind=PdvFieldDifferenceKind.MISSING_IN_EXPECTED,
                    expected='',
                    actual='scalar',
                ),
                PdvFieldDifference(
                    field='610',
                    kind=PdvFieldDifferenceKind.MISSING_IN_ACTUAL,
                    expected='scalar',
                    actual='',
                ),
                PdvFieldDifference(
                    field='303.porez',
                    kind=PdvFieldDifferenceKind.VALUE_CHANGED,
                    expected='84.11',
                    actual='85.11',
                ),
                PdvFieldDifference(
                    field='400',
                    kind=PdvFieldDifferenceKind.VALUE_CHANGED,
                    expected='-84.11',
                    actual='-83.11',
                ),
            ],
        )

    def test_matching_payloads_return_empty_list(self):
        fields = {'400': Decimal('-84.11')}
        self.assertEqual(compare_pdv_payload_fields(_payload(fields), _payload(fields)), [])


class PayloadMismatchSummaryTests(SimpleTestCase):
    def test_summary_pluralization(self):
        from accounting.services.tax_forms.pdv.diff import PayloadMismatchError

        cases = [
            (
                [
                    PdvFieldDifference('610', PdvFieldDifferenceKind.MISSING_IN_ACTUAL, 'scalar', ''),
                    PdvFieldDifference('620', PdvFieldDifferenceKind.MISSING_IN_EXPECTED, '', 'scalar'),
                    PdvFieldDifference('630', PdvFieldDifferenceKind.TYPE_CHANGED, 'scalar', 'pair'),
                ],
                '3 strukturne razlike',
            ),
            (
                [
                    PdvFieldDifference('303.porez', PdvFieldDifferenceKind.VALUE_CHANGED, '84.11', '85.11'),
                    PdvFieldDifference('400', PdvFieldDifferenceKind.VALUE_CHANGED, '-84.11', '-83.11'),
                ],
                '2 razlike u vrijednostima',
            ),
            (
                [
                    PdvFieldDifference('610', PdvFieldDifferenceKind.MISSING_IN_ACTUAL, 'scalar', ''),
                    PdvFieldDifference('303.porez', PdvFieldDifferenceKind.VALUE_CHANGED, '84.11', '85.11'),
                    PdvFieldDifference('400', PdvFieldDifferenceKind.VALUE_CHANGED, '-84.11', '-83.11'),
                    PdvFieldDifference('401', PdvFieldDifferenceKind.VALUE_CHANGED, '1.00', '2.00'),
                    PdvFieldDifference('402', PdvFieldDifferenceKind.VALUE_CHANGED, '3.00', '4.00'),
                ],
                '1 strukturna razlika, 4 razlike u vrijednostima',
            ),
            ([], 'Nema razlika'),
        ]
        for differences, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(PayloadMismatchError(differences).summary(), expected)

    def test_is_structural_flags(self):
        diff = PdvFieldDifference(
            field='303.porez',
            kind=PdvFieldDifferenceKind.VALUE_CHANGED,
            expected='84.11',
            actual='85.11',
        )
        self.assertFalse(diff.is_structural)
        self.assertTrue(diff.is_value_difference)

        structural = PdvFieldDifference(
            field='610',
            kind=PdvFieldDifferenceKind.MISSING_IN_ACTUAL,
            expected='scalar',
            actual='',
        )
        self.assertTrue(structural.is_structural)
        self.assertFalse(structural.is_value_difference)
