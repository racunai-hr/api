"""OSS/IOSS PDV mapping rules (boxes 204/214/215/308)."""

from decimal import Decimal

from django.test import SimpleTestCase

from accounting.models import VATEntryCategory, VATLedgerEntry
from accounting.services.tax_forms.pdv.mapping import (
    PDV_MAPPING,
    build_mapping_from_registry,
    journal_line_to_box,
    map_invoice_eu_distance_204,
    map_invoice_eu_electronic_214,
    map_invoice_oss_215,
    map_ioss_pretporez_308,
)
from accounting.services.tax_forms.pdv.supply_procedure import (
    ECOMMERCE_EXCLUDED_FROM_VAT_DUE,
    VatSupplyProcedure,
    invoice_procedure_to_box,
)


class PdvOssMappingTests(SimpleTestCase):
    def test_build_mapping_includes_oss_boxes(self):
        mapping = build_mapping_from_registry()
        self.assertEqual(
            set(mapping.keys()),
            {
                '101', '103', '204', '214', '215', '201', '202', '203',
                '207', '209', '210', '303', '306', '307', '308', '309',
                '610', '611', '612', '613', '614', '615',
            },
        )

    def test_invoice_procedure_to_box(self):
        self.assertEqual(invoice_procedure_to_box(VatSupplyProcedure.OSS), '215')
        self.assertEqual(invoice_procedure_to_box(VatSupplyProcedure.EU_DISTANCE), '204')
        self.assertEqual(invoice_procedure_to_box(VatSupplyProcedure.EU_ELECTRONIC), '214')
        self.assertIsNone(invoice_procedure_to_box(VatSupplyProcedure.STANDARD))
        self.assertIsNone(invoice_procedure_to_box(VatSupplyProcedure.IOSS))

    def test_oss_output_rules_use_oss_supply_category(self):
        for factory in (map_invoice_oss_215, map_invoice_eu_distance_204, map_invoice_eu_electronic_214):
            rule = factory()
            self.assertEqual(rule.ledger_type, VATLedgerEntry.LEDGER_I_RA)
            self.assertEqual(rule.entry_category, VATEntryCategory.OSS_SUPPLY)

    def test_ioss_input_rule(self):
        rule = map_ioss_pretporez_308()
        self.assertEqual(rule.vat_box, '308')
        self.assertEqual(rule.ledger_type, VATLedgerEntry.LEDGER_U_RA)
        self.assertEqual(rule.entry_category, VATEntryCategory.OSS_SUPPLY)
        self.assertEqual(rule.rrif_vat_account, '14042')

    def test_journal_line_to_box_ioss_account(self):
        self.assertEqual(
            journal_line_to_box(account_code='14042', debit_amount=Decimal('19.00'), credit_amount=Decimal('0')),
            '308',
        )

    def test_ecommerce_boxes_excluded_from_vat_due(self):
        self.assertEqual(ECOMMERCE_EXCLUDED_FROM_VAT_DUE, frozenset({'204', '214', '215', '308'}))

    def test_pdv_mapping_matches_registry(self):
        for code in ('204', '214', '215', '308'):
            self.assertIn(code, PDV_MAPPING)
