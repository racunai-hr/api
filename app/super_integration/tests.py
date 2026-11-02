from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from settings.models import CompanySettings, TaxRate
from super_integration.models import SuperTenantConfig
from super_integration.ubl.parser import parse_invoice_ubl
from tenants.models import Tenant


SAMPLE_UBL = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>HR-2026-001</cbc:ID>
  <cbc:IssueDate>2026-06-01</cbc:IssueDate>
  <cbc:DueDate>2026-06-15</cbc:DueDate>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyName><cbc:Name>Hardsoft j.d.o.o.</cbc:Name></cac:PartyName>
      <cac:PostalAddress>
        <cbc:StreetName>Ulica 1</cbc:StreetName>
        <cbc:CityName>Zagreb</cbc:CityName>
      </cac:PostalAddress>
      <cac:PartyTaxScheme><cbc:CompanyID>12345678901</cbc:CompanyID></cac:PartyTaxScheme>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:TaxTotal><cbc:TaxAmount currencyID="EUR">25.00</cbc:TaxAmount></cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:TaxExclusiveAmount currencyID="EUR">100.00</cbc:TaxExclusiveAmount>
    <cbc:PayableAmount currencyID="EUR">125.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cac:Item><cbc:Name>Hosting</cbc:Name></cac:Item>
  </cac:InvoiceLine>
</Invoice>
"""

PREPAID_UBL = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>26210-H120-5154</cbc:ID>
  <cbc:IssueDate>2026-08-06</cbc:IssueDate>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyName><cbc:Name>Centar za vozila Hrvatske d.d.</cbc:Name></cac:PartyName>
      <cac:PartyTaxScheme><cbc:CompanyID>73294314024</cbc:CompanyID></cac:PartyTaxScheme>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:TaxTotal><cbc:TaxAmount currencyID="EUR">7.49</cbc:TaxAmount></cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:TaxExclusiveAmount currencyID="EUR">364.71</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">372.20</cbc:TaxInclusiveAmount>
    <cbc:PrepaidAmount currencyID="EUR">372.20</cbc:PrepaidAmount>
    <cbc:PayableAmount currencyID="EUR">0</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</Invoice>
"""

MALFORMED_UBL = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>BAD-1</cbc:ID>
  <cbc:IssueDate>2026-08-06</cbc:IssueDate>
  <cac:AccountingSupplierParty>
    <cac:Party><cac:PartyName><cbc:Name>X</cbc:Name></cac:PartyName></cac:Party>
  </cac:AccountingSupplierParty>
  <cac:LegalMonetaryTotal>
    <cbc:PayableAmount currencyID="EUR">0</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</Invoice>
"""


class UBLParserTests(TestCase):
    def test_parse_invoice_ubl_fallback_exclusive_plus_tax(self):
        parsed = parse_invoice_ubl(SAMPLE_UBL)
        self.assertEqual(parsed.invoice_number, 'HR-2026-001')
        self.assertEqual(parsed.supplier_name, 'Hardsoft j.d.o.o.')
        self.assertEqual(parsed.supplier_oib, '12345678901')
        self.assertEqual(parsed.subtotal, Decimal('100.00'))
        self.assertEqual(parsed.tax_amount, Decimal('25.00'))
        self.assertEqual(parsed.total_amount, Decimal('125.00'))
        self.assertEqual(parsed.payable_amount, Decimal('125.00'))
        self.assertEqual(parsed.prepaid_amount, Decimal('0'))

    def test_parse_prepaid_invoice_uses_tax_inclusive_gross(self):
        parsed = parse_invoice_ubl(PREPAID_UBL)
        self.assertEqual(parsed.invoice_number, '26210-H120-5154')
        self.assertEqual(parsed.subtotal, Decimal('364.71'))
        self.assertEqual(parsed.tax_amount, Decimal('7.49'))
        self.assertEqual(parsed.total_amount, Decimal('372.20'))
        self.assertEqual(parsed.prepaid_amount, Decimal('372.20'))
        self.assertEqual(parsed.payable_amount, Decimal('0'))

    def test_parse_malformed_monetary_totals_raises(self):
        from ubl.parser.invoice import UblMonetaryError

        with self.assertRaises(UblMonetaryError):
            parse_invoice_ubl(MALFORMED_UBL)


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class InvoiceNumberingTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.get_or_create(slug='finestar-test', defaults={'name': 'Fine Star d.o.o.'})[0]
        self.user = User.objects.create_user('tester', password='test')
        tax = TaxRate.all_objects.create(tenant=self.tenant, name='25', rate='25', is_default=True)
        CompanySettings.all_objects.create(
            tenant=self.tenant,
            company_name='Fine Star d.o.o.',
            company_address='Test',
            street='Test',
            postal_code='10000',
            city='Zagreb',
            country='HR',
            company_phone='1',
            company_email='a@b.hr',
            default_tax_rate=tax,
        )
        self.partner = Partner.all_objects.create(
            tenant=self.tenant,
            name='Kupac d.o.o.',
            partner_type='customer',
            tax_number='98765432109',
            address='Adresa 1',
            city='Split',
            postal_code='21000',
            created_by=self.user,
        )

    def test_assign_invoice_number_on_sent(self):
        invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            status='sent',
            company_to=self.partner,
            issue_date=date(2026, 6, 9),
            due_date=date(2026, 6, 23),
            created_by=self.user,
        )
        self.assertTrue(invoice.invoice_number.startswith('2026-'))

    def test_build_invoice_ubl(self):
        from super_integration.ubl.builder import build_invoice_ubl

        invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            invoice_number='2026-0001',
            status='draft',
            company_to=self.partner,
            issue_date=date(2026, 6, 9),
            due_date=date(2026, 6, 23),
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name='IT usluga',
            quantity=1,
            unit_price=100,
            tax_rate=25,
        )
        invoice.recalculate_totals()
        company = CompanySettings.all_objects.get(tenant=self.tenant)
        ubl = build_invoice_ubl(invoice, company, self.partner)
        self.assertIn('2026-0001', ubl)
        self.assertIn('Fine Star d.o.o.', ubl)
