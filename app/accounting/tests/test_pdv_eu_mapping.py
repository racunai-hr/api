"""EU box mapping rules (207/307 — II.7/III.7)."""

from decimal import Decimal

from django.test import SimpleTestCase

from accounting.models import VATEntryCategory, VATLedgerEntry
from accounting.services.tax_forms.pdv.mapping import (
    PDV_MAPPING,
    build_mapping_from_registry,
    invoice_eu_outbound_box,
    invoice_rate_to_box,
    is_eu_customer,
    is_eu_outbound_invoice_line,
    is_eu_supplier,
    journal_line_to_box,
    journal_line_to_viii1_box,
    map_eu_goods_base_207,
    map_eu_goods_obveza_207,
    map_eu_goods_pretporez_307,
    map_invoice_eu_goods_101,
    map_invoice_eu_services_103,
    map_viii1_cars_acquisition_612,
    map_viii1_other_di_acquisition_614,
    viii1_box_amounts,
)


class _Supplier:
    def __init__(self, *, country: str = '', tax_number: str = ''):
        self.country = country
        self.tax_number = tax_number


class PdvEuMappingTests(SimpleTestCase):
    def test_build_mapping_includes_eu_boxes(self):
        mapping = build_mapping_from_registry()
        self.assertEqual(
            set(mapping.keys()),
            {
                '101', '103', '204', '214', '215', '201', '202', '203', '207', '209', '210',
                '303', '306', '307', '308', '309',
                '610', '611', '612', '613', '614', '615',
            },
        )

    def test_eu_goods_rules_use_eu_acquisition_category(self):
        rule_207 = map_eu_goods_base_207()
        self.assertEqual(rule_207.vat_box, '207')
        self.assertEqual(rule_207.entry_category, VATEntryCategory.EU_ACQUISITION)
        self.assertEqual(rule_207.ledger_type, VATLedgerEntry.LEDGER_U_RA)
        self.assertEqual(rule_207.rrif_vat_account, '0373')

        rule_obveza = map_eu_goods_obveza_207()
        self.assertEqual(rule_obveza.vat_box, '207')
        self.assertEqual(rule_obveza.rrif_vat_account, '24022')

        rule_307 = map_eu_goods_pretporez_307()
        self.assertEqual(rule_307.vat_box, '307')
        self.assertEqual(rule_307.rrif_vat_account, '14022')

    def test_journal_line_to_box_eu_accounts(self):
        self.assertEqual(
            journal_line_to_box(account_code='14022', debit_amount=Decimal('2000'), credit_amount=Decimal('0')),
            '307',
        )
        self.assertEqual(
            journal_line_to_box(account_code='24022', debit_amount=Decimal('0'), credit_amount=Decimal('2000')),
            '207',
        )
        self.assertEqual(
            journal_line_to_box(account_code='14032', debit_amount=Decimal('100'), credit_amount=Decimal('0')),
            '306',
        )

    def test_is_eu_supplier(self):
        self.assertTrue(is_eu_supplier(_Supplier(country='Germany', tax_number='DE229674882')))
        self.assertFalse(is_eu_supplier(_Supplier(country='Croatia', tax_number='12345678901')))
        self.assertFalse(is_eu_supplier(_Supplier(country='Germany', tax_number='36619131370')))

    def test_is_eu_customer_alias(self):
        self.assertTrue(is_eu_customer(_Supplier(country='Germany', tax_number='DE123456789')))

    def test_invoice_eu_outbound_box(self):
        self.assertEqual(invoice_eu_outbound_box(has_service_date=False), '101')
        self.assertEqual(invoice_eu_outbound_box(has_service_date=True), '103')

    def test_is_eu_outbound_invoice_line(self):
        partner = _Supplier(country='Germany', tax_number='DE123456789')
        self.assertTrue(is_eu_outbound_invoice_line(partner=partner, tax_rate=Decimal('0')))
        self.assertFalse(is_eu_outbound_invoice_line(partner=partner, tax_rate=Decimal('25')))
        self.assertFalse(
            is_eu_outbound_invoice_line(
                partner=_Supplier(country='Croatia', tax_number='12345678901'),
                tax_rate=Decimal('0'),
            )
        )

    def test_eu_outbound_rules_use_eu_supply_category(self):
        rule_101 = map_invoice_eu_goods_101()
        self.assertEqual(rule_101.vat_box, '101')
        self.assertEqual(rule_101.entry_category, VATEntryCategory.EU_SUPPLY)
        self.assertEqual(rule_101.ledger_type, VATLedgerEntry.LEDGER_I_RA)

        rule_103 = map_invoice_eu_services_103()
        self.assertEqual(rule_103.vat_box, '103')
        self.assertEqual(rule_103.entry_category, VATEntryCategory.EU_SUPPLY)

    def test_pdv_mapping_matches_registry(self):
        for code in ('207', '307', '610', '614'):
            self.assertIn(code, PDV_MAPPING)

    def test_journal_line_to_viii1_box(self):
        self.assertEqual(
            journal_line_to_viii1_box(account_code='032001', debit_amount=Decimal('15000'), credit_amount=Decimal('0')),
            '612',
        )
        self.assertEqual(
            journal_line_to_viii1_box(account_code='0310', debit_amount=Decimal('500'), credit_amount=Decimal('0')),
            '614',
        )
        self.assertIsNone(
            journal_line_to_viii1_box(account_code='0373', debit_amount=Decimal('8000'), credit_amount=Decimal('0')),
        )

    def test_viii1_rules_use_adjustment_category(self):
        rule_612 = map_viii1_cars_acquisition_612()
        self.assertEqual(rule_612.entry_category, VATEntryCategory.ADJUSTMENT)
        rule_614 = map_viii1_other_di_acquisition_614()
        self.assertEqual(rule_614.vat_box, '614')

    def test_viii1_box_amounts(self):
        self.assertEqual(viii1_box_amounts('612', Decimal('1000')), (Decimal('1000'), Decimal('0')))
        self.assertEqual(viii1_box_amounts('611', Decimal('1000')), (Decimal('0'), Decimal('1000')))
