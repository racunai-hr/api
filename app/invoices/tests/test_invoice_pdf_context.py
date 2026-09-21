from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings

from invoices.models import Invoice, InvoiceItem
from invoices.services.pdf import invoice_document_context
from partners.models import Partner
from payments.models import BankAccount
from settings.models import CompanySettings, TaxRate
from shared.iban import format_iban_display
from tenants.models import Tenant


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr', TENANT_STAGE_INFIX='')
class InvoicePdfContextTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug='pdf-pay', name='PDF Pay')
        tax = TaxRate.all_objects.create(tenant=self.tenant, name='25%', rate=Decimal('25.00'))
        self.company = CompanySettings.all_objects.create(
            tenant=self.tenant,
            company_name='Fine Star d.o.o.',
            company_address='Ulica 1',
            company_phone='000',
            company_email='office@example.hr',
            default_tax_rate=tax,
        )
        self.user = User.objects.create_user('pdf-pay', password='test')
        self.partner = Partner.all_objects.create(
            tenant=self.tenant,
            name='BAM STORITVE, d.o.o.',
            partner_type='customer',
            tax_number='SI85567736',
            vat_number='SI85567736',
            address='Vrbno 62',
            city='Šentjur',
            postal_code='3230',
            country_code='SI',
            created_by=self.user,
        )
        BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Fine Star EUR — OTP',
            bank_name='OTP banka',
            account_number='0204771',
            iban='HR6124070001100204771',
            swift_code='OTPVHR2X',
            currency='EUR',
            status='active',
            is_active=True,
        )
        self.invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            invoice_number='2026-0003',
            status='draft',
            company_to=self.partner,
            issue_date=date(2026, 9, 21),
            due_date=date(2026, 10, 21),
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=self.invoice,
            item_name='BAM-Build ERP — Modul 1',
            quantity=1,
            unit_price=Decimal('15000.00'),
            tax_rate=Decimal('0.00'),
        )

    def test_format_iban_display_groups_by_four(self):
        self.assertEqual(
            format_iban_display('HR61 2407 0001 1002 0477 1'),
            'HR61 2407 0001 1002 0477 1',
        )

    def test_invoice_html_includes_otp_payment_instructions(self):
        request = RequestFactory().get('/invoices/1/')
        context = invoice_document_context(request, self.invoice)
        html = render_to_string('invoices/invoice_detail.html', context)
        self.assertIn('Upute za uplatu', html)
        self.assertIn('OTP banka', html)
        self.assertIn('HR61 2407 0001 1002 0477 1', html)
        self.assertIn('OTPVHR2X', html)
        self.assertIn('2026-0003', html)
