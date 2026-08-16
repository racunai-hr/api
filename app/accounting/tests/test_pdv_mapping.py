"""Tests for PDV mapping rules and registry sync."""

from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from accounting.models import VATEntryCategory, VATLedgerEntry
from accounting.services.tax_forms.pdv.boxes import implemented_boxes
from accounting.services.tax_forms.pdv.mapping import (
    PDV_MAPPING,
    build_mapping_from_registry,
    invoice_rate_to_box,
    journal_line_to_box,
    map_domestic_rc_obveza_209,
    map_domestic_rc_pretporez_309,
    map_eu_goods_base_207,
    map_eu_goods_obveza_207,
    map_eu_goods_pretporez_307,
    map_eu_services_obveza_210,
    map_eu_services_pretporez_306,
    map_ioss_pretporez_308,
    map_expense_input_25,
    map_invoice_eu_goods_101,
    map_invoice_eu_services_103,
    map_invoice_eu_distance_204,
    map_invoice_eu_electronic_214,
    map_invoice_oss_215,
    map_invoice_output_13,
    map_invoice_output_25,
    map_invoice_output_5,
    map_viii1_cars_acquisition_612,
    map_viii1_cars_disposal_613,
    map_viii1_other_di_acquisition_614,
    map_viii1_other_di_disposal_615,
    map_viii1_real_estate_611,
    map_viii1_total_610,
)


class PdvMappingRegistryTests(SimpleTestCase):
    def test_build_mapping_from_registry_covers_implemented_boxes(self):
        mapping = build_mapping_from_registry()
        self.assertEqual(set(mapping), {box.code for box in implemented_boxes()})

    def test_pdv_mapping_matches_implemented_boxes(self):
        mapping = build_mapping_from_registry()
        self.assertEqual(set(mapping.keys()), set(PDV_MAPPING.keys()))
        for box in implemented_boxes():
            self.assertIn(box.code, PDV_MAPPING)

    def test_invoice_rate_to_box(self):
        self.assertEqual(invoice_rate_to_box(Decimal('25')), '203')
        self.assertEqual(invoice_rate_to_box(Decimal('13')), '202')
        self.assertEqual(invoice_rate_to_box(Decimal('5')), '201')
        self.assertIsNone(invoice_rate_to_box(Decimal('0')))

    def test_journal_line_to_box(self):
        self.assertEqual(
            journal_line_to_box(account_code='1400', debit_amount=Decimal('25'), credit_amount=Decimal('0')),
            '303',
        )
        self.assertEqual(
            journal_line_to_box(account_code='24001', debit_amount=Decimal('0'), credit_amount=Decimal('25')),
            '203',
        )
        self.assertEqual(
            journal_line_to_box(account_code='240011', debit_amount=Decimal('0'), credit_amount=Decimal('13')),
            '202',
        )
        self.assertEqual(
            journal_line_to_box(account_code='240010', debit_amount=Decimal('0'), credit_amount=Decimal('5')),
            '201',
        )
        self.assertEqual(
            journal_line_to_box(account_code='14022', debit_amount=Decimal('2000'), credit_amount=Decimal('0')),
            '307',
        )
        self.assertEqual(
            journal_line_to_box(account_code='24022', debit_amount=Decimal('0'), credit_amount=Decimal('2000')),
            '207',
        )
        self.assertIsNone(
            journal_line_to_box(account_code='1000', debit_amount=Decimal('100'), credit_amount=Decimal('0')),
        )

    def test_mapping_rule_metadata(self):
        rule_203 = map_invoice_output_25()
        self.assertEqual(rule_203.vat_box, '203')
        self.assertEqual(rule_203.ledger_type, VATLedgerEntry.LEDGER_I_RA)
        self.assertEqual(rule_203.entry_category, VATEntryCategory.DOMESTIC)
        self.assertEqual(rule_203.rrif_vat_account, '24001')

        rule_303 = map_expense_input_25()
        self.assertEqual(rule_303.vat_box, '303')
        self.assertEqual(rule_303.ledger_type, VATLedgerEntry.LEDGER_U_RA)
        self.assertEqual(rule_303.rrif_vat_account, '1400')

    def test_each_implemented_box_has_mapping_test(self):
        expected = {
            '101': map_invoice_eu_goods_101,
            '103': map_invoice_eu_services_103,
            '204': map_invoice_eu_distance_204,
            '214': map_invoice_eu_electronic_214,
            '215': map_invoice_oss_215,
            '201': map_invoice_output_5,
            '202': map_invoice_output_13,
            '203': map_invoice_output_25,
            '303': map_expense_input_25,
            '207': map_eu_goods_base_207,
            '209': map_domestic_rc_obveza_209,
            '210': map_eu_services_obveza_210,
            '306': map_eu_services_pretporez_306,
            '307': map_eu_goods_pretporez_307,
            '309': map_domestic_rc_pretporez_309,
            '308': map_ioss_pretporez_308,
            '610': map_viii1_total_610,
            '611': map_viii1_real_estate_611,
            '612': map_viii1_cars_acquisition_612,
            '613': map_viii1_cars_disposal_613,
            '614': map_viii1_other_di_acquisition_614,
            '615': map_viii1_other_di_disposal_615,
        }
        for box in implemented_boxes():
            self.assertIn(box.code, expected)
            rule = expected[box.code]()
            self.assertEqual(rule.vat_box, box.code)
