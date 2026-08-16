from datetime import date

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from settings.models import CompanySettings, TaxOffice, TaxRate
from ubl.builder.invoice import build_invoice_ubl
from tenants.models import Tenant
from ubl.serializers.from_invoice import _company_address_for_ubl


class ParseLegacyAddressTests(TestCase):
    def test_empty_address(self):
        result = CompanySettings.parse_legacy_address('')
        self.assertEqual(result, {
            'street': '',
            'house_number': '',
            'postal_code': '',
            'city': '',
            'country': 'HR',
        })

    def test_none_address(self):
        result = CompanySettings.parse_legacy_address(None)
        self.assertEqual(result['country'], 'HR')
        self.assertEqual(result['street'], '')

    def test_whitespace_only(self):
        result = CompanySettings.parse_legacy_address('   \n  \n  ')
        self.assertEqual(result['street'], '')
        self.assertEqual(result['city'], '')

    def test_address_without_house_number(self):
        result = CompanySettings.parse_legacy_address('Ilica\n10000 Zagreb')
        self.assertEqual(result['street'], 'Ilica')
        self.assertEqual(result['house_number'], '')
        self.assertEqual(result['postal_code'], '10000')
        self.assertEqual(result['city'], 'Zagreb')

    def test_standard_two_line_address(self):
        result = CompanySettings.parse_legacy_address('Bana Josipa Jelačića 58\n22000 Šibenik')
        self.assertEqual(result['street'], 'Bana Josipa Jelačića')
        self.assertEqual(result['house_number'], '58')
        self.assertEqual(result['postal_code'], '22000')
        self.assertEqual(result['city'], 'Šibenik')
        self.assertEqual(result['country'], 'HR')

    def test_house_number_with_letter_suffix(self):
        result = CompanySettings.parse_legacy_address('Ulica 12A\n10000 Zagreb')
        self.assertEqual(result['street'], 'Ulica')
        self.assertEqual(result['house_number'], '12A')

    def test_multi_line_address_uses_first_two_lines(self):
        result = CompanySettings.parse_legacy_address(
            'Prva ulica 1\n21000 Split\nTreći red — ignorirati'
        )
        self.assertEqual(result['street'], 'Prva ulica')
        self.assertEqual(result['house_number'], '1')
        self.assertEqual(result['postal_code'], '21000')
        self.assertEqual(result['city'], 'Split')

    def test_unexpected_format_single_line(self):
        result = CompanySettings.parse_legacy_address('Stara adresa')
        self.assertEqual(result['street'], 'Stara adresa')
        self.assertEqual(result['house_number'], '')
        self.assertEqual(result['postal_code'], '')
        self.assertEqual(result['city'], '')

    def test_unexpected_format_no_postal_on_second_line(self):
        result = CompanySettings.parse_legacy_address('Ulica 5\nGrad bez poštanskog')
        self.assertEqual(result['street'], 'Ulica')
        self.assertEqual(result['house_number'], '5')
        self.assertEqual(result['postal_code'], '')
        self.assertEqual(result['city'], 'Grad bez poštanskog')

    def test_unexpected_format_garbage_input(self):
        result = CompanySettings.parse_legacy_address('!!!! ???')
        self.assertEqual(result['street'], '!!!! ???')
        self.assertEqual(result['country'], 'HR')


class CompanySettingsUblFallbackTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug='ubl-fallback', name='Fallback Co')
        self.user = User.objects.create_user('ubl-fallback', password='test')
        tax = TaxRate.all_objects.create(tenant=self.tenant, name='25', rate='25', is_default=True)
        self.company = CompanySettings.all_objects.create(
            tenant=self.tenant,
            company_name='Fallback Co',
            company_address='Stara adresa',
            street='',
            house_number='',
            postal_code='',
            city='',
            country='HR',
            company_phone='1',
            company_email='a@b.hr',
            default_tax_rate=tax,
        )
        self.partner = Partner.all_objects.create(
            tenant=self.tenant,
            name='Kupac',
            partner_type='customer',
            tax_number='12345678901',
            address='Adresa',
            city='Split',
            postal_code='21000',
            created_by=self.user,
        )

    def test_formatted_street_falls_back_to_legacy_address(self):
        self.assertEqual(self.company.formatted_street, 'Stara adresa')

    def test_ubl_uses_legacy_address_when_normalized_fields_empty(self):
        street, city, postal = _company_address_for_ubl(self.company)
        self.assertEqual(street, 'Stara adresa')
        self.assertEqual(city, 'Zagreb')
        self.assertEqual(postal, '10000')

        invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            invoice_number='2026-0001',
            status='draft',
            company_to=self.partner,
            issue_date=date(2026, 4, 1),
            due_date=date(2026, 4, 15),
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name='Usluga',
            quantity=1,
            unit_price=100,
            tax_rate=25,
        )
        invoice.recalculate_totals()
        ubl = build_invoice_ubl(invoice, self.company, self.partner)
        self.assertIn('Stara adresa', ubl)
        self.assertNotIn('Fallback Co', ubl.split('StreetName')[1].split('CityName')[0])


class ProvisionNewTenantTests(TestCase):
    def test_fresh_tenant_has_country_tax_office_and_working_ubl(self):
        slug = 'ticket0-fresh-tenant-test'
        Tenant.objects.filter(slug=slug).delete()

        call_command('provision_finestar', tenant=slug)

        company = CompanySettings.all_objects.get(tenant__slug=slug)
        self.assertEqual(company.country, 'HR')
        self.assertIsNotNone(company.tax_office)
        self.assertEqual(company.tax_office.code, '3566')
        self.assertEqual(company.street, 'Bana Josipa Jelačića')
        self.assertEqual(company.house_number, '58')
        self.assertEqual(company.postal_code, '22000')
        self.assertEqual(company.city, 'Šibenik')

        tax_office_count = TaxOffice.objects.filter(code='3566').count()
        self.assertEqual(tax_office_count, 1)

        user = User.objects.create_user('ticket0-fresh', password='test')
        partner = Partner.all_objects.create(
            tenant=company.tenant,
            name='Kupac',
            partner_type='customer',
            tax_number='98765432109',
            address='Adresa 1',
            city='Split',
            postal_code='21000',
            created_by=user,
        )
        invoice = Invoice.all_objects.create(
            tenant=company.tenant,
            invoice_number='2026-0001',
            status='draft',
            company_to=partner,
            issue_date=date(2026, 4, 1),
            due_date=date(2026, 4, 15),
            created_by=user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name='Usluga',
            quantity=1,
            unit_price=100,
            tax_rate=25,
        )
        invoice.recalculate_totals()

        ubl = build_invoice_ubl(invoice, company, partner)
        self.assertIn('Bana Josipa Jelačića 58', ubl)
        self.assertIn('Šibenik', ubl)
        self.assertIn('22000', ubl)

    def test_reprovision_does_not_duplicate_tax_office_or_change_address(self):
        slug = 'ticket0-reprovision-test'
        Tenant.objects.filter(slug=slug).delete()

        call_command('provision_finestar', tenant=slug)
        company = CompanySettings.all_objects.get(tenant__slug=slug)
        before = {
            'street': company.street,
            'house_number': company.house_number,
            'postal_code': company.postal_code,
            'city': company.city,
            'tax_office_id': company.tax_office_id,
        }

        call_command('provision_finestar', tenant=slug)
        company.refresh_from_db()

        self.assertEqual(TaxOffice.objects.filter(code='3566').count(), 1)
        self.assertEqual(company.street, before['street'])
        self.assertEqual(company.house_number, before['house_number'])
        self.assertEqual(company.postal_code, before['postal_code'])
        self.assertEqual(company.city, before['city'])
        self.assertEqual(company.tax_office_id, before['tax_office_id'])
