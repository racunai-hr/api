"""Tests for PDV mapping rules and registry sync."""

from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from accounting.models import VATEntryCategory, VATLedgerEntry
from accounting.services.tax_forms.pdv.boxes import implemented_boxes
from accounting.services.tax_forms.pdv.mapping import (
    PDV_MAPPING,
    build_mapping_from_registry,
    cvh_mixed_25_amounts,
    expense_matches_domestic_25,
    expense_rate_to_box,
    invoice_rate_to_box,
    is_ch_supplier,
    is_ch_telecom_supplier,
    is_cvh_stp_supplier,
    is_hr_bank_supplier,
    is_hr_insurance_supplier,
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

    def test_expense_rate_to_box_only_25(self):
        self.assertEqual(expense_rate_to_box(Decimal('25')), '303')
        self.assertIsNone(expense_rate_to_box(Decimal('13')))
        self.assertIsNone(expense_rate_to_box(Decimal('5')))
        self.assertIsNone(expense_rate_to_box(Decimal('0')))

    def test_expense_matches_domestic_25(self):
        self.assertTrue(
            expense_matches_domestic_25(
                base_amount=Decimal('66.36'), vat_amount=Decimal('16.59'),
            )
        )
        self.assertTrue(
            expense_matches_domestic_25(
                base_amount=Decimal('15.99'), vat_amount=Decimal('4.00'),
            )
        )
        self.assertTrue(
            expense_matches_domestic_25(
                base_amount=Decimal('40.50'), vat_amount=Decimal('10.13'),
            )
        )
        self.assertFalse(
            expense_matches_domestic_25(
                base_amount=Decimal('223.87'), vat_amount=Decimal('7.49'),
            )
        )
        self.assertFalse(
            expense_matches_domestic_25(
                base_amount=Decimal('13.94'), vat_amount=Decimal('0.00'),
            )
        )

    def test_cvh_mixed_25_amounts_reconstructs_taxable_slice(self):
        self.assertEqual(
            cvh_mixed_25_amounts(
                base_amount=Decimal('223.87'), vat_amount=Decimal('7.49'),
            ),
            (Decimal('29.96'), Decimal('7.49')),
        )
        self.assertEqual(
            cvh_mixed_25_amounts(
                base_amount=Decimal('7.95'), vat_amount=Decimal('0.66'),
            ),
            (Decimal('2.64'), Decimal('0.66')),
        )
        self.assertIsNone(
            cvh_mixed_25_amounts(
                base_amount=Decimal('40.50'), vat_amount=Decimal('10.13'),
            )
        )

    def test_cvh_bank_insurance_identity(self):
        class _P:
            def __init__(self, name, country='Hrvatska', tax_number='73294314024'):
                self.name = name
                self.country = country
                self.tax_number = tax_number
                self.vat_id = ''

        self.assertTrue(is_cvh_stp_supplier(_P('CVH STP "VODICE"')))
        self.assertFalse(is_cvh_stp_supplier(_P('Dobavljač d.o.o.')))
        self.assertTrue(is_hr_bank_supplier(_P('OTP banka d.d.', tax_number='52508873833')))
        self.assertTrue(is_hr_insurance_supplier(_P('CROATIA OSIGURANJE D.D.')))
        self.assertFalse(is_hr_bank_supplier(_P('Telecom26 AG', country='Švicarska', tax_number='CHE-431.728.269')))
        self.assertFalse(is_hr_insurance_supplier(_P('SaM Automobile', country='Njemačka', tax_number='DE355497142')))
        self.assertTrue(is_ch_supplier(_P('Telecom26 AG', country='Švicarska', tax_number='CHE-431.728.269')))
        self.assertTrue(is_ch_telecom_supplier(_P('Telecom26 AG', country='Švicarska', tax_number='CHE-431.728.269')))
        self.assertFalse(is_ch_telecom_supplier(_P('Alpine Hotel AG', country='Švicarska', tax_number='CHE-111.222.333')))
        self.assertFalse(is_ch_supplier(_P('SaM Automobile', country='Njemačka', tax_number='DE355497142')))

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
