"""Reverse charge mapping rules (209/309 domestic, 210/306 EU B2B services)."""

from decimal import Decimal

from django.test import SimpleTestCase

from accounting.models import VATEntryCategory, VATLedgerEntry
from accounting.services.tax_forms.pdv.mapping import (
    PDV_MAPPING,
    build_mapping_from_registry,
    journal_line_to_box,
    journal_line_vat_rate,
    map_domestic_rc_obveza_209,
    map_domestic_rc_pretporez_309,
    map_eu_services_obveza_210,
    map_eu_services_pretporez_306,
    rc_output_box_for_input,
)


class PdvReverseChargeMappingTests(SimpleTestCase):
    def test_build_mapping_includes_reverse_charge_boxes(self):
        mapping = build_mapping_from_registry()
        self.assertEqual(
            set(mapping.keys()),
            {
                '101', '103', '204', '214', '215', '201', '202', '203', '207', '209', '210',
                '303', '306', '307', '308', '309',
                '610', '611', '612', '613', '614', '615',
            },
        )

    def test_domestic_rc_rules(self):
        rule_209 = map_domestic_rc_obveza_209()
        self.assertEqual(rule_209.vat_box, '209')
        self.assertEqual(rule_209.ledger_type, VATLedgerEntry.LEDGER_I_RA)
        self.assertEqual(rule_209.entry_category, VATEntryCategory.DOMESTIC)
        self.assertEqual(rule_209.rrif_vat_account, '2401')

        rule_309 = map_domestic_rc_pretporez_309()
        self.assertEqual(rule_309.vat_box, '309')
        self.assertEqual(rule_309.ledger_type, VATLedgerEntry.LEDGER_U_RA)
        self.assertEqual(rule_309.rrif_vat_account, '1401')

    def test_eu_services_rc_rules(self):
        rule_210 = map_eu_services_obveza_210()
        self.assertEqual(rule_210.vat_box, '210')
        self.assertEqual(rule_210.entry_category, VATEntryCategory.EU_ACQUISITION)

        rule_306 = map_eu_services_pretporez_306()
        self.assertEqual(rule_306.vat_box, '306')
        self.assertEqual(rule_306.rrif_vat_account, '14032')

    def test_journal_line_to_box_domestic_and_eu_services(self):
        self.assertEqual(
            journal_line_to_box(account_code='1401', debit_amount=Decimal('2500'), credit_amount=Decimal('0')),
            '309',
        )
        self.assertEqual(
            journal_line_to_box(account_code='24011', debit_amount=Decimal('0'), credit_amount=Decimal('2500')),
            '209',
        )
        self.assertEqual(
            journal_line_to_box(account_code='14032', debit_amount=Decimal('500'), credit_amount=Decimal('0')),
            '306',
        )
        self.assertEqual(
            journal_line_to_box(account_code='24032', debit_amount=Decimal('0'), credit_amount=Decimal('500')),
            '210',
        )

    def test_rc_output_box_for_input(self):
        self.assertEqual(rc_output_box_for_input('309'), '209')
        self.assertEqual(rc_output_box_for_input('306'), '210')
        self.assertEqual(rc_output_box_for_input('307'), '207')

    def test_journal_line_vat_rate_rc_accounts(self):
        self.assertEqual(journal_line_vat_rate('1401'), Decimal('25.00'))
        self.assertEqual(journal_line_vat_rate('14031'), Decimal('13.00'))
        self.assertEqual(journal_line_vat_rate('14030'), Decimal('5.00'))

    def test_pdv_mapping_matches_registry(self):
        for code in ('209', '210', '306', '309'):
            self.assertIn(code, PDV_MAPPING)
