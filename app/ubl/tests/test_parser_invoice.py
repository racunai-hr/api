"""Unit tests for extended canonical UBL invoice parser (generic semantics)."""

from decimal import Decimal

from django.test import SimpleTestCase

from ubl.parser.invoice import UblMonetaryError, parse_invoice_ubl

CVH_SHAPED_UBL = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:mfin.gov.hr:cius-2025:</cbc:CustomizationID>
  <cbc:ProfileID>P5</cbc:ProfileID>
  <cbc:ID>26210-H120-5154</cbc:ID>
  <cbc:IssueDate>2026-08-06</cbc:IssueDate>
  <cbc:DueDate>2026-08-06</cbc:DueDate>
  <cbc:Note>Reg. oznaka: TEST</cbc:Note>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:OriginatorDocumentReference>
    <cbc:ID>HR03 1201-0262102</cbc:ID>
  </cac:OriginatorDocumentReference>
  <cac:AdditionalDocumentReference>
    <cbc:ID>26210-H120-5154</cbc:ID>
  </cac:AdditionalDocumentReference>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyName><cbc:Name>CVH STP "VODICE"</cbc:Name></cac:PartyName>
      <cac:PostalAddress>
        <cbc:StreetName>Ćirila i Metoda 11</cbc:StreetName>
        <cbc:CityName>VODICE</cbc:CityName>
        <cbc:PostalZone>22211</cbc:PostalZone>
        <cac:Country><cbc:IdentificationCode>HR</cbc:IdentificationCode></cac:Country>
      </cac:PostalAddress>
      <cac:PartyTaxScheme><cbc:CompanyID>HR73294314024</cbc:CompanyID></cac:PartyTaxScheme>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:Delivery>
    <cbc:ActualDeliveryDate>2026-08-06</cbc:ActualDeliveryDate>
  </cac:Delivery>
  <cac:AllowanceCharge>
    <cbc:ChargeIndicator>true</cbc:ChargeIndicator>
    <cbc:AllowanceChargeReason>05100 Posebna naknada za okoliš</cbc:AllowanceChargeReason>
    <cbc:Amount currencyID="EUR">2.20</cbc:Amount>
  </cac:AllowanceCharge>
  <cac:AllowanceCharge>
    <cbc:ChargeIndicator>true</cbc:ChargeIndicator>
    <cbc:AllowanceChargeReason>05000 Naknada za ceste</cbc:AllowanceChargeReason>
    <cbc:Amount currencyID="EUR">254.80</cbc:Amount>
  </cac:AllowanceCharge>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="EUR">7.49</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="EUR">334.75</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="EUR">0</cbc:TaxAmount>
      <cac:TaxCategory>
        <cbc:ID>E</cbc:ID>
        <cbc:Percent>0.00</cbc:Percent>
      </cac:TaxCategory>
    </cac:TaxSubtotal>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="EUR">29.96</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="EUR">7.49</cbc:TaxAmount>
      <cac:TaxCategory>
        <cbc:ID>S</cbc:ID>
        <cbc:Percent>25.00</cbc:Percent>
      </cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="EUR">29.96</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="EUR">364.71</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">372.20</cbc:TaxInclusiveAmount>
    <cbc:ChargeTotalAmount currencyID="EUR">334.75</cbc:ChargeTotalAmount>
    <cbc:PrepaidAmount currencyID="EUR">372.20</cbc:PrepaidAmount>
    <cbc:PayableAmount currencyID="EUR">0</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cbc:ID>5</cbc:ID>
    <cbc:InvoicedQuantity unitCode="H87">1.000</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">7.06</cbc:LineExtensionAmount>
    <cac:Item>
      <cbc:Name>Poslovi registracije</cbc:Name>
      <cac:CommodityClassification>
        <cbc:ItemClassificationCode listID="CG">71.20.04</cbc:ItemClassificationCode>
      </cac:CommodityClassification>
      <cac:ClassifiedTaxCategory>
        <cbc:ID>S</cbc:ID>
        <cbc:Percent>25.00</cbc:Percent>
      </cac:ClassifiedTaxCategory>
    </cac:Item>
    <cac:Price>
      <cbc:PriceAmount currencyID="EUR">7.06</cbc:PriceAmount>
    </cac:Price>
  </cac:InvoiceLine>
</Invoice>
"""


class ExtendedUblParserTests(SimpleTestCase):
    def test_structured_lines_charges_refs_totals(self):
        parsed = parse_invoice_ubl(CVH_SHAPED_UBL)
        self.assertEqual(parsed.profile_id, 'P5')
        self.assertEqual(parsed.delivery_date, '2026-08-06')
        self.assertEqual(parsed.notes, ['Reg. oznaka: TEST'])
        self.assertEqual(parsed.supplier_vat_id, 'HR73294314024')
        self.assertEqual(parsed.supplier_country_code, 'HR')
        self.assertEqual(parsed.supplier_postal_code, '22211')
        self.assertEqual(parsed.line_extension_amount, Decimal('29.96'))
        self.assertEqual(parsed.charge_total_amount, Decimal('334.75'))
        self.assertEqual(parsed.total_amount, Decimal('372.20'))
        self.assertEqual(parsed.prepaid_amount, Decimal('372.20'))
        self.assertEqual(parsed.payable_amount, Decimal('0'))

        self.assertEqual(len(parsed.lines), 1)
        line = parsed.lines[0]
        self.assertEqual(line.classification.scheme, 'CG')
        self.assertEqual(line.classification.code, '71.20.04')
        self.assertEqual(line.quantity, Decimal('1.000'))
        self.assertEqual(line.unit_code, 'H87')
        self.assertEqual(line.vat_rate, Decimal('25.00'))
        self.assertEqual(line.line_extension_amount, Decimal('7.06'))
        self.assertEqual(parsed.line_descriptions[0], 'Poslovi registracije')

        self.assertEqual(len(parsed.charges), 2)
        self.assertEqual(parsed.charges[0].code, '05100')
        self.assertEqual(parsed.charges[0].amount, Decimal('2.20'))
        self.assertEqual(parsed.charges[1].code, '05000')

        self.assertEqual(len(parsed.tax_summary), 2)
        self.assertEqual(parsed.tax_summary[0].rate, Decimal('0.00'))
        self.assertEqual(parsed.tax_summary[1].vat, Decimal('7.49'))

        types = {ref.type for ref in parsed.references}
        self.assertIn('originator', types)
        self.assertIn('additional', types)
        originator = next(r for r in parsed.references if r.type == 'originator')
        self.assertEqual(originator.value, 'HR03 1201-0262102')

    def test_missing_optional_blocks_are_empty(self):
        minimal = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>MIN-1</cbc:ID>
  <cbc:IssueDate>2026-01-01</cbc:IssueDate>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party><cac:PartyName><cbc:Name>X</cbc:Name></cac:PartyName></cac:Party>
  </cac:AccountingSupplierParty>
  <cac:TaxTotal><cbc:TaxAmount currencyID="EUR">0</cbc:TaxAmount></cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:TaxExclusiveAmount currencyID="EUR">10.00</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">10.00</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="EUR">10.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</Invoice>
"""
        parsed = parse_invoice_ubl(minimal)
        self.assertEqual(parsed.lines, [])
        self.assertEqual(parsed.charges, [])
        self.assertEqual(parsed.tax_summary, [])
        self.assertEqual(parsed.references, [])
        self.assertIsNone(parsed.profile_id)
        self.assertIsNone(parsed.charge_total_amount)

    def test_malformed_monetary_still_raises(self):
        bad = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>BAD</cbc:ID>
  <cbc:IssueDate>2026-01-01</cbc:IssueDate>
  <cac:AccountingSupplierParty>
    <cac:Party><cac:PartyName><cbc:Name>X</cbc:Name></cac:PartyName></cac:Party>
  </cac:AccountingSupplierParty>
  <cac:LegalMonetaryTotal>
    <cbc:PayableAmount currencyID="EUR">0</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</Invoice>
"""
        with self.assertRaises(UblMonetaryError):
            parse_invoice_ubl(bad)
