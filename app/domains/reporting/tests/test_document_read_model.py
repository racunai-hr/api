"""Acceptance tests for ADR-0020 document read model."""

import csv
import io
import os
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import (
    ChartOfAccounts,
    FiscalPeriod,
    JournalEntry,
    JournalEntryLine,
    PrivateFundsClaim,
    SubledgerAllocation,
    SubledgerItem,
    SubmissionDestination,
    SubmissionEvent,
    SubmissionSource,
    VATEntryCategory,
    VATLedgerEntry,
    VATPeriod,
    VATReturn,
    VATReturnStatus,
)
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from domains.reporting.documents.export import escape_spreadsheet_cell
from domains.reporting.documents.filters import parse_filters
from domains.reporting.documents.pdf import INVOICE_PDF_UNAVAILABLE
from domains.reporting.documents.query import materialize_union, union_rows
from domains.reporting.documents.projection import (
    collect_controls,
    operational_incoming,
    operational_outgoing,
    vat_amount_check,
)
from expenses.models import Expense, ExpenseAttachment, ExpenseCategory
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner, PartnerBankAccount
from payments.models import BankAccount
from banking.models import BankStatement, BankTransaction
from tenants.models import Tenant, TenantMembership


HOST = 'docread.racunai.hr'
OTHER_HOST = 'docother.racunai.hr'

# Minimal CVH-shaped UBL for PR A incoming detail contract (generic UBL semantics).
_PR_A_UBL = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ProfileID>P5</cbc:ProfileID>
  <cbc:ID>26210-H120-5154</cbc:ID>
  <cbc:IssueDate>2026-08-06</cbc:IssueDate>
  <cbc:DueDate>2026-08-06</cbc:DueDate>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:OriginatorDocumentReference><cbc:ID>HR03 1201-0262102</cbc:ID></cac:OriginatorDocumentReference>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyName><cbc:Name>CVH STP</cbc:Name></cac:PartyName>
      <cac:PostalAddress>
        <cbc:StreetName>Street 1</cbc:StreetName>
        <cbc:CityName>Vodice</cbc:CityName>
        <cbc:PostalZone>22211</cbc:PostalZone>
        <cac:Country><cbc:IdentificationCode>HR</cbc:IdentificationCode></cac:Country>
      </cac:PostalAddress>
      <cac:PartyTaxScheme><cbc:CompanyID>HR73294314024</cbc:CompanyID></cac:PartyTaxScheme>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:Delivery><cbc:ActualDeliveryDate>2026-08-06</cbc:ActualDeliveryDate></cac:Delivery>
  <cac:AllowanceCharge>
    <cbc:ChargeIndicator>true</cbc:ChargeIndicator>
    <cbc:AllowanceChargeReason>05100 Posebna naknada za okoliš</cbc:AllowanceChargeReason>
    <cbc:Amount currencyID="EUR">2.20</cbc:Amount>
  </cac:AllowanceCharge>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="EUR">7.49</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="EUR">29.96</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="EUR">7.49</cbc:TaxAmount>
      <cac:TaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>25.00</cbc:Percent></cac:TaxCategory>
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
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity unitCode="H87">1</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">7.06</cbc:LineExtensionAmount>
    <cac:Item>
      <cbc:Name>Poslovi registracije</cbc:Name>
      <cac:CommodityClassification>
        <cbc:ItemClassificationCode listID="CG">71.20.04</cbc:ItemClassificationCode>
      </cac:CommodityClassification>
      <cac:ClassifiedTaxCategory><cbc:Percent>25.00</cbc:Percent></cac:ClassifiedTaxCategory>
    </cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="EUR">7.06</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>
</Invoice>
"""



def _minimal_pdf(name='test.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 minimal', content_type='application/pdf')


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class DocumentReadModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='docread', name='Doc Read Co')
        cls.other = Tenant.objects.create(slug='docother', name='Other Co')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='doc-viewer', password='test')
        cls.outsider = User.objects.create_user(username='doc-outsider', password='test')
        TenantMembership.objects.create(user=cls.user, tenant=cls.tenant, role='viewer')
        cls.partner = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Kupac Read',
            tax_number='11111111111',
            partner_type='customer',
            status='active',
            address='Ulica 1',
            city='Zagreb',
            postal_code='10000',
        )
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Dobavljač Read',
            tax_number='22222222222',
            partner_type='supplier',
            status='active',
            address='Ulica 2',
            city='Zagreb',
            postal_code='10000',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.other_partner = Partner.all_objects.create(
            tenant=cls.other,
            name='Other partner',
            tax_number='33333333333',
            partner_type='customer',
            status='active',
            address='X',
            city='Split',
            postal_code='21000',
        )

    def _auth_client(self, user=None, host=HOST):
        user = user or self.user
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = host
        return client

    def _invoice(self, **overrides):
        with_item = overrides.pop('with_item', True)
        defaults = {
            'tenant': self.tenant,
            'company_to': self.partner,
            'invoice_number': f'2026-{Invoice.all_objects.filter(tenant=self.tenant).count() + 1:04d}',
            'issue_date': date(2026, 5, 15),
            'due_date': date(2026, 5, 30),
            'status': 'sent',
            'subtotal': Decimal('100.00'),
            'tax_amount': Decimal('25.00'),
            'total_amount': Decimal('125.00'),
            'created_by': self.user,
        }
        defaults.update(overrides)
        invoice = Invoice.all_objects.create(**defaults)
        if with_item:
            InvoiceItem.objects.create(
                invoice=invoice,
                item_name='Usluga',
                quantity=1,
                unit_price=Decimal('100.00'),
                tax_rate=Decimal('25.00'),
            )
        return invoice

    def _expense(self, **overrides):
        defaults = {
            'tenant': self.tenant,
            'expense_number': f'T-{Expense.all_objects.filter(tenant=self.tenant).count() + 1}',
            'status': 'approved',
            'category': self.category,
            'supplier': self.supplier,
            'amount': Decimal('50.00'),
            'tax_amount': Decimal('10.00'),
            'currency': 'EUR',
            'expense_date': date(2026, 5, 10),
            'description': 'Trošak',
            'created_by': self.user,
        }
        defaults.update(overrides)
        return Expense.all_objects.create(**defaults)

    def _journal(self, source, *, status='posted', number='JE-1', description='test'):
        ct = ContentType.objects.get_for_model(source)
        period, _ = FiscalPeriod.all_objects.get_or_create(
            tenant=source.tenant,
            year=2026,
            month=5,
            defaults={'status': 'open'},
        )
        return JournalEntry.all_objects.create(
            tenant=source.tenant,
            entry_number=number,
            entry_date=date(2026, 5, 15),
            status=status,
            description=description,
            created_by=self.user,
            posted_by=self.user if status == 'posted' else None,
            posted_at=timezone.now() if status == 'posted' else None,
            source_content_type=ct,
            source_object_id=source.pk,
            fiscal_period=period,
        )

    def _subledger(self, source, *, status='open', open_amount=None, direction='receivable', journal_entry=None):
        je = journal_entry or self._journal(source, number=f'JE-SL-{source.pk}')
        amount = getattr(source, 'total_amount', None) or source.amount
        if open_amount is None:
            open_amount = amount if status != 'closed' else Decimal('0')
        if status == 'partial':
            open_amount = amount / 2
        ct = ContentType.objects.get_for_model(source)
        partner = getattr(source, 'company_to', None) or source.supplier
        return SubledgerItem.all_objects.create(
            tenant=source.tenant,
            partner=partner,
            direction=direction,
            source_content_type=ct,
            source_object_id=source.pk,
            journal_entry=je,
            original_amount=amount,
            open_amount=open_amount,
            due_date=source.due_date or date(2026, 5, 30),
            status=status,
        )

    def _ledger(self, source, *, category=VATEntryCategory.DOMESTIC, period_status='open'):
        period, _ = VATPeriod.all_objects.get_or_create(
            tenant=source.tenant,
            year=2026,
            month=5,
            defaults={'status': period_status},
        )
        if period.status != period_status:
            period.status = period_status
            period.save(update_fields=['status'])
        ct = ContentType.objects.get_for_model(source)
        if isinstance(source, Invoice):
            base, vat, number, ledger_type = source.subtotal, source.tax_amount, source.invoice_number, 'I-RA'
        else:
            base, vat, number, ledger_type = source.amount - source.tax_amount, source.tax_amount, source.expense_number, 'U-RA'
        return VATLedgerEntry.all_objects.create(
            tenant=source.tenant,
            vat_period=period,
            ledger_type=ledger_type,
            entry_date=date(2026, 5, 15),
            document_number=number,
            partner_name='P',
            base_amount=base,
            vat_rate=Decimal('25.00'),
            vat_amount=vat,
            source_content_type=ct,
            source_object_id=source.pk,
            vat_box='201' if ledger_type == 'I-RA' else '303',
            entry_category=category,
        )

    def test_anonymous_list_returns_401_bearer(self):
        client = APIClient()
        client.defaults['HTTP_HOST'] = HOST
        response = client.get('/api/documents/')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['WWW-Authenticate'], 'Bearer')
        self.assertEqual(
            response.json(),
            {'detail': 'Authentication credentials were not provided.'},
        )

    def test_paid_document_open_subledger_is_not_paid(self):
        invoice = self._invoice(status='paid')
        self._subledger(invoice, status='open')
        client = self._auth_client()
        response = client.get(f'/api/documents/outgoing/{invoice.pk}/')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['document_status']['value'], 'paid')
        self.assertNotEqual(body['subledger']['state']['value'], 'closed')
        self.assertNotEqual(body['subledger']['state']['value'], 'paid')
        self.assertIn('paid_status_subledger_open', body['controls'])
        self.assertNotIn('paid_status_subledger_missing', body['controls'])
        self.assertNotEqual(body['operational_status']['value'], 'paid')

    def test_paid_document_missing_subledger_is_not_provable(self):
        invoice = self._invoice(status='paid')
        body = self._auth_client().get(f'/api/documents/outgoing/{invoice.pk}/').json()
        self.assertEqual(body['document_status']['value'], 'paid')
        self.assertIsNone(body['subledger']['state']['value'])
        self.assertEqual(body['subledger']['state']['reason'], 'not_recorded')
        self.assertIsNone(body['operational_status']['value'])
        self.assertEqual(body['operational_status']['reason'], 'not_provable')
        self.assertIn('paid_status_subledger_missing', body['controls'])
        self.assertNotIn('paid_status_subledger_open', body['controls'])
        alerts, _ = collect_controls({
            'document': invoice,
            'direction': 'outgoing',
            'partner': self.partner,
            'subledger': None,
            'ledger_rows': [],
            'as_of_day': date(2026, 8, 19),
            'bank_matched': True,
            'payments': [SimpleNamespace(payment_method='bank_transfer')],
            'has_pdf_xml': True,
            'items': list(invoice.items.all()),
        })
        self.assertIn('paid_status_subledger_missing', alerts)
        self.assertNotIn('paid_status_subledger_open', alerts)
        operational = operational_outgoing(
            document=invoice,
            subledger=None,
            as4_status=None,
            as_of_day=date(2026, 8, 19),
        )
        self.assertIsNone(operational['value'])
        self.assertEqual(operational['reason'], 'not_provable')

    def test_incoming_paid_missing_subledger_is_not_disputed(self):
        expense = self._expense(status='paid')
        body = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/').json()
        self.assertEqual(body['document_status']['value'], 'paid')
        self.assertIsNone(body['subledger']['state']['value'])
        self.assertEqual(body['subledger']['state']['reason'], 'not_recorded')
        self.assertIsNone(body['operational_status']['value'])
        self.assertEqual(body['operational_status']['reason'], 'not_provable')
        self.assertIsNone(body['operational_status']['source'])
        self.assertNotEqual(body['operational_status']['value'], 'disputed')
        self.assertNotEqual(body['operational_status']['value'], 'paid')
        self.assertIn('paid_status_subledger_missing', body['controls'])
        self.assertNotIn('paid_status_subledger_open', body['controls'])
        mismatch = operational_incoming(
            document=expense,
            subledger=None,
            disputed=False,
            as_of_day=date(2026, 8, 19),
            has_receipt_evidence=False,
        )
        self.assertIsNone(mismatch['value'])
        self.assertEqual(mismatch['reason'], 'not_provable')
        explicit = operational_incoming(
            document=expense,
            subledger=None,
            disputed=True,
            as_of_day=date(2026, 8, 19),
            has_receipt_evidence=False,
        )
        self.assertEqual(explicit['value'], 'disputed')
        self.assertEqual(explicit['source'], 'as4_document_link')

    def test_incoming_paid_bank_matched_without_subledger_is_paid(self):
        """Manual JE + bank match (e.g. uvoz vozila) — no AP subledger, still operational paid."""
        expense = self._expense(status='paid', amount=Decimal('8000.00'), tax_amount=Decimal('0.00'))
        purchase_je = self._journal(expense, number='JE-ASSET-1', description='Nabava OS')
        vat_je = self._journal(expense, number='JE-ASSET-VAT', description='PDV reverse charge')
        account = ChartOfAccounts.all_objects.filter(tenant=self.tenant).first()
        self.assertIsNotNone(account)
        JournalEntryLine.objects.create(
            journal_entry=purchase_je,
            account=account,
            description='duguje',
            debit_amount=Decimal('8000.00'),
            credit_amount=Decimal('0.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=purchase_je,
            account=account,
            description='potražuje',
            debit_amount=Decimal('0.00'),
            credit_amount=Decimal('8000.00'),
        )
        bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='EUR',
            bank_name='OTP',
            account_number='110',
            iban='HR6124070001100204771',
        )
        statement = BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number='ST-ASSET-1',
            bank_account=bank_account,
            statement_date=date(2026, 5, 22),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('8000.00'),
            imported_by=self.user,
        )
        tx = BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=statement,
            transaction_date=date(2026, 5, 22),
            amount=Decimal('8000.00'),
            currency='EUR',
            transaction_type='debit',
            description='VW-CROSS',
            match_status='matched',
            matched_journal_entry=purchase_je,
        )

        body = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/').json()
        self.assertEqual(body['operational_status']['value'], 'paid')
        self.assertEqual(body['operational_status']['source'], 'bank_transaction')
        self.assertNotIn('paid_status_subledger_missing', body['controls'])
        self.assertEqual(body['status']['payment'], 'matched')
        self.assertEqual(body['status']['posting'], 'posted')
        # Prefer JE with bank match over sibling VAT JE as primary posting.
        self.assertEqual(body['accounting']['journal_entry_id'], purchase_je.pk)
        self.assertEqual(body['payment']['bank_transaction_id'], tx.pk)
        self.assertTrue(body['payment']['matched'])
        # vat_je remains linked as sibling (no crash / still detail OK)
        self.assertEqual(vat_je.source_object_id, expense.pk)

        proven = operational_incoming(
            document=expense,
            subledger=None,
            disputed=False,
            as_of_day=date(2026, 8, 19),
            has_receipt_evidence=True,
            posting=purchase_je,
            bank_matched=True,
        )
        self.assertEqual(proven['value'], 'paid')
        self.assertEqual(proven['source'], 'bank_transaction')

    def test_closed_subledger_with_allocation_no_bank_alarm(self):
        invoice = self._invoice(status='sent')
        item = self._subledger(invoice, status='closed', open_amount=Decimal('0'))
        SubledgerAllocation.all_objects.create(
            tenant=self.tenant,
            subledger_item=item,
            journal_entry=item.journal_entry,
            amount=Decimal('125.00'),
        )
        client = self._auth_client()
        body = client.get(f'/api/documents/outgoing/{invoice.pk}/').json()
        self.assertNotIn('bank_unmatched', body['controls'])
        self.assertNotIn('settlement_evidence_missing', body['controls'])

    def test_submitted_vat_period_membership_not_provable(self):
        invoice = self._invoice()
        entry = self._ledger(invoice, period_status='submitted')
        vat_return = VATReturn.all_objects.create(
            tenant=self.tenant,
            vat_period=entry.vat_period,
            version=1,
            status=VATReturnStatus.SUBMITTED,
            payload_snapshot={'fields': {}},
            payload_hash='a' * 64,
            payload_json=ContentFile(b'{}', name='payload.json'),
        )
        SubmissionEvent.all_objects.create(
            tenant=self.tenant,
            content_type=ContentType.objects.get_for_model(VATReturn),
            object_id=vat_return.pk,
            submission_no=1,
            destination=SubmissionDestination.EPOREZNA,
            external_identifier=uuid4(),
            submitted_at=timezone.now(),
            source=SubmissionSource.MANUAL,
        )
        body = self._auth_client().get(f'/api/documents/outgoing/{invoice.pk}/').json()
        membership = body['vat']['document_in_submitted_return']
        self.assertIsNone(membership['value'])
        self.assertEqual(membership['reason'], 'not_provable')
        self.assertNotEqual(body['vat']['lifecycle']['value'], 'in_submitted_return')
        self.assertIn('nije pojedinačno potvrđen', body['vat']['disclaimer'] or '')

    def test_never_emits_in_submitted_return_lifecycle(self):
        invoice = self._invoice()
        self._ledger(invoice, period_status='submitted')
        body = self._auth_client().get(f'/api/documents/outgoing/{invoice.pk}/').json()
        self.assertNotEqual(body['vat']['lifecycle']['value'], 'in_submitted_return')

    def test_superseded_and_submitted_versions_without_membership(self):
        invoice = self._invoice()
        entry = self._ledger(invoice, period_status='submitted')
        old = VATReturn.all_objects.create(
            tenant=self.tenant,
            vat_period=entry.vat_period,
            version=1,
            status=VATReturnStatus.SUPERSEDED,
            payload_snapshot={'fields': {}},
            payload_hash='b' * 64,
            payload_json=ContentFile(b'{}', name='v1.json'),
        )
        new = VATReturn.all_objects.create(
            tenant=self.tenant,
            vat_period=entry.vat_period,
            version=2,
            status=VATReturnStatus.SUBMITTED,
            payload_snapshot={'fields': {}},
            payload_hash='c' * 64,
            payload_json=ContentFile(b'{}', name='v2.json'),
            superseded_by=None,
        )
        old.superseded_by = new
        old.save(update_fields=['superseded_by'])
        SubmissionEvent.all_objects.create(
            tenant=self.tenant,
            content_type=ContentType.objects.get_for_model(VATReturn),
            object_id=new.pk,
            submission_no=1,
            destination=SubmissionDestination.EPOREZNA,
            external_identifier=uuid4(),
            submitted_at=timezone.now(),
            source=SubmissionSource.MANUAL,
        )
        body = self._auth_client().get(f'/api/documents/outgoing/{invoice.pk}/').json()
        versions = {row['version']: row['status'] for row in body['vat']['period_returns']}
        self.assertEqual(versions[1], 'superseded')
        self.assertEqual(versions[2], 'submitted')
        self.assertIsNone(body['vat']['document_in_submitted_return']['value'])

    def test_reverse_charge_not_generic_mismatch(self):
        invoice = self._invoice()
        self._ledger(invoice, category=VATEntryCategory.EU_ACQUISITION)
        result = vat_amount_check(
            direction='outgoing',
            document=invoice,
            ledger_rows=list(VATLedgerEntry.all_objects.filter(source_object_id=invoice.pk)),
            items=list(invoice.items.all()),
        )
        self.assertEqual(result, 'not_provable')
        body = self._auth_client().get(f'/api/documents/outgoing/{invoice.pk}/').json()
        self.assertNotIn('vat_amount_mismatch', body['controls'])

    def test_secondary_iban_no_warning(self):
        invoice = self._invoice()
        PartnerBankAccount.objects.create(
            partner=self.partner, bank_name='A', bic='AAA', iban='HR1111111111111111111', is_primary=True,
        )
        PartnerBankAccount.objects.create(
            partner=self.partner, bank_name='B', bic='BBB', iban='HR2222222222222222222', is_primary=False,
        )
        alerts, _ = collect_controls({
            'document': invoice,
            'direction': 'outgoing',
            'partner': self.partner,
            'subledger': None,
            'ledger_rows': [],
            'as_of_day': date(2026, 5, 1),
            'payment_order': SimpleNamespace(creditor_iban='HR2222222222222222222'),
            'partner_ibans': {'HR1111111111111111111', 'HR2222222222222222222'},
            'has_pdf_xml': True,
            'items': list(invoice.items.all()),
        })
        self.assertNotIn('iban_mismatch', alerts)

    def test_partner_without_iban_not_mismatch(self):
        invoice = self._invoice()
        alerts, _ = collect_controls({
            'document': invoice,
            'direction': 'outgoing',
            'partner': self.partner,
            'subledger': None,
            'ledger_rows': [],
            'as_of_day': date(2026, 5, 1),
            'payment_order': SimpleNamespace(creditor_iban='HR999'),
            'partner_ibans': set(),
            'has_pdf_xml': True,
            'items': list(invoice.items.all()),
        })
        self.assertNotIn('iban_mismatch', alerts)

    def test_foreign_partner_with_vat_not_missing_oib(self):
        eu = Partner.all_objects.create(
            tenant=self.tenant,
            name='SaM DE',
            tax_number='',
            vat_number='DE355497142',
            partner_type='supplier',
            status='active',
            address='X',
            city='Sinsheim',
            postal_code='74889',
            country_code='DE',
        )
        expense = self._expense(supplier=eu, due_date=date(2026, 7, 30), status='draft')
        alerts, _ = collect_controls({
            'document': expense,
            'direction': 'incoming',
            'partner': eu,
            'subledger': None,
            'ledger_rows': [],
            'as_of_day': date(2026, 8, 20),
            'has_pdf_xml': True,
        })
        self.assertNotIn('missing_partner_or_oib', alerts)
        self.assertNotIn('missing_due_date', alerts)

    def test_hr_partner_without_oib_is_missing(self):
        hr = Partner.all_objects.create(
            tenant=self.tenant,
            name='HR bez OIB',
            tax_number='',
            vat_number='',
            partner_type='supplier',
            status='active',
            address='X',
            city='Zagreb',
            postal_code='10000',
            country_code='HR',
        )
        expense = self._expense(supplier=hr, due_date=date(2026, 7, 30))
        alerts, _ = collect_controls({
            'document': expense,
            'direction': 'incoming',
            'partner': hr,
            'subledger': None,
            'ledger_rows': [],
            'as_of_day': date(2026, 8, 20),
            'has_pdf_xml': True,
        })
        self.assertIn('missing_partner_or_oib', alerts)

    def test_missing_due_date_still_alerts(self):
        expense = self._expense(due_date=None, status='draft')
        alerts, _ = collect_controls({
            'document': expense,
            'direction': 'incoming',
            'partner': self.supplier,
            'subledger': None,
            'ledger_rows': [],
            'as_of_day': date(2026, 8, 20),
            'has_pdf_xml': True,
        })
        self.assertIn('missing_due_date', alerts)

    def test_missing_due_date_suppressed_when_paid(self):
        expense = self._expense(due_date=None, status='paid')
        alerts, _ = collect_controls({
            'document': expense,
            'direction': 'incoming',
            'partner': self.supplier,
            'subledger': None,
            'ledger_rows': [],
            'as_of_day': date(2026, 8, 20),
            'has_pdf_xml': True,
        })
        self.assertNotIn('missing_due_date', alerts)

    def test_missing_due_date_suppressed_when_subledger_closed(self):
        expense = self._expense(due_date=None, status='approved')
        sub = SimpleNamespace(status='closed', open_amount=Decimal('0.00'))
        alerts, _ = collect_controls({
            'document': expense,
            'direction': 'incoming',
            'partner': self.supplier,
            'subledger': sub,
            'ledger_rows': [],
            'as_of_day': date(2026, 8, 20),
            'has_pdf_xml': True,
        })
        self.assertNotIn('missing_due_date', alerts)

    def test_missing_pdf_xml_alerts_without_evidence(self):
        expense = self._expense(due_date=date(2026, 7, 30), status='draft')
        alerts, _ = collect_controls({
            'document': expense,
            'direction': 'incoming',
            'partner': self.supplier,
            'subledger': None,
            'ledger_rows': [],
            'as_of_day': date(2026, 8, 20),
            'has_pdf_xml': False,
        })
        self.assertIn('missing_pdf_xml', alerts)

    def test_as4_inbound_suppresses_missing_pdf_xml(self):
        """Fiskalizacija 2 (AS4) is the e-invoice — PDF/XML control must not fire."""
        expense = self._expense(due_date=None, status='draft')
        self._as4_inbound_link(expense, ubl_xml='')
        body = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/').json()
        self.assertNotIn('missing_pdf_xml', body['controls'])
        # Unpaid AS4 without DueDate in UBL still alerts on due date.
        self.assertIn('missing_due_date', body['controls'])

    def test_f1_csv_paid_suppresses_missing_pdf_and_due_date(self):
        """F1 / prior intermediary import + paid: no PDF/XML or due-date alerts."""
        expense = self._expense(due_date=None, status='paid', source='f1_csv')
        body = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/').json()
        self.assertNotIn('missing_pdf_xml', body['controls'])
        self.assertNotIn('missing_due_date', body['controls'])

    def test_attachment_after_posting_is_notice_not_mismatch(self):
        invoice = self._invoice()
        je = self._journal(invoice)
        je.posted_at = timezone.now() - timedelta(days=1)
        je.save(update_fields=['posted_at'])
        Invoice.all_objects.filter(pk=invoice.pk).update(updated_at=timezone.now())
        invoice.refresh_from_db()
        body = self._auth_client().get(f'/api/documents/outgoing/{invoice.pk}/').json()
        self.assertIn('updated_after_posting', body['notices'])
        self.assertNotIn('updated_after_posting', body['controls'])

    def test_posted_je_in_closed_fiscal_period_is_info(self):
        invoice = self._invoice()
        je = self._journal(invoice)
        period = je.fiscal_period
        period.status = 'closed'
        period.save(update_fields=['status'])
        body = self._auth_client().get(f'/api/documents/outgoing/{invoice.pk}/').json()
        self.assertTrue(body['posting']['fiscal_locked']['value'])
        self.assertNotIn('fiscal_period_closed', body['controls'])

    def test_stable_pagination_same_date(self):
        first = self._invoice(invoice_number='2026-0001', issue_date=date(2026, 5, 15))
        second = self._invoice(invoice_number='2026-0002', issue_date=date(2026, 5, 15))
        client = self._auth_client()
        page1 = client.get('/api/documents/?page_size=1&page=1&direction=outgoing').json()
        page2 = client.get('/api/documents/?page_size=1&page=2&direction=outgoing').json()
        ids = {page1['results'][0]['id'], page2['results'][0]['id']}
        self.assertEqual(ids, {first.pk, second.pk})
        self.assertEqual(page1['count'], 2)

    def test_other_tenant_unknown_id_is_404(self):
        invoice = self._invoice()
        other_invoice = Invoice.all_objects.create(
            tenant=self.other,
            company_to=self.other_partner,
            invoice_number='X-1',
            issue_date=date(2026, 5, 1),
            due_date=date(2026, 5, 10),
            status='sent',
            subtotal=Decimal('10.00'),
            tax_amount=Decimal('2.50'),
            total_amount=Decimal('12.50'),
            created_by=self.user,
        )
        client = self._auth_client()
        response = client.get(f'/api/documents/outgoing/{other_invoice.pk}/')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(client.get(f'/api/documents/outgoing/{invoice.pk}/').status_code, 200)

    def _assert_safe_pdf(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF-'))
        disposition = response['Content-Disposition']
        self.assertIn('filename', disposition.lower())
        self.assertNotIn('/', disposition)
        self.assertNotIn('\\', disposition)
        self.assertNotIn('\r', disposition)
        self.assertNotIn('\n', disposition)

    def test_outgoing_pdf_valid_invoice_is_pdf(self):
        invoice = self._invoice()
        response = self._auth_client().get(f'/api/documents/outgoing/{invoice.pk}/pdf/')
        self._assert_safe_pdf(response)
        self.assertIn(f'R-{invoice.invoice_number}.pdf', response['Content-Disposition'])

    def test_outgoing_pdf_viewer_can_download(self):
        invoice = self._invoice()
        membership = TenantMembership.objects.get(user=self.user, tenant=self.tenant)
        self.assertEqual(membership.role, 'viewer')
        response = self._auth_client().get(f'/api/documents/outgoing/{invoice.pk}/pdf/')
        self._assert_safe_pdf(response)

    def test_outgoing_pdf_foreign_tenant_or_id_is_404(self):
        invoice = self._invoice()
        other_invoice = Invoice.all_objects.create(
            tenant=self.other,
            company_to=self.other_partner,
            invoice_number='PDF-X-1',
            issue_date=date(2026, 5, 1),
            due_date=date(2026, 5, 10),
            status='sent',
            created_by=self.user,
        )
        client = self._auth_client()
        self.assertEqual(client.get(f'/api/documents/outgoing/{other_invoice.pk}/pdf/').status_code, 404)
        self.assertEqual(client.get(f'/api/documents/outgoing/{invoice.pk + 99999}/pdf/').status_code, 404)
        expense = self._expense()
        self.assertEqual(client.get(f'/api/documents/incoming/{expense.pk}/pdf/').status_code, 404)
        outsider = self._auth_client(self.outsider, host=OTHER_HOST)
        TenantMembership.objects.create(user=self.outsider, tenant=self.other, role='viewer')
        self.assertEqual(
            outsider.get(f'/api/documents/outgoing/{invoice.pk}/pdf/').status_code,
            404,
        )

    def test_outgoing_pdf_draft_cancelled_incomplete_still_generate(self):
        """On-demand preview, same Admin renderer; no numbering or status write."""
        draft = self._invoice(status='draft', invoice_number='DRAFT-PREVIEW')
        cancelled = self._invoice(status='cancelled')
        incomplete = self._invoice(
            status='draft',
            invoice_number='',
            with_item=False,
            subtotal=Decimal('0.00'),
            tax_amount=Decimal('0.00'),
            total_amount=Decimal('0.00'),
        )
        client = self._auth_client()
        for invoice in (draft, cancelled, incomplete):
            before = (
                invoice.status,
                invoice.invoice_number,
                invoice.updated_at,
                invoice.total_amount,
            )
            response = client.get(f'/api/documents/outgoing/{invoice.pk}/pdf/')
            self._assert_safe_pdf(response)
            invoice.refresh_from_db()
            self.assertEqual(
                (invoice.status, invoice.invoice_number, invoice.updated_at, invoice.total_amount),
                before,
            )
        self.assertEqual(draft.items.count(), 1)
        self.assertEqual(incomplete.items.count(), 0)

    def test_outgoing_pdf_renderer_failure_is_controlled_json(self):
        invoice = self._invoice()
        client = self._auth_client()
        cases = (
            patch(
                'invoices.services.pdf.render_invoice_pdf_bytes',
                side_effect=RuntimeError('disk /secret/path'),
            ),
            patch('invoices.services.pdf.render_invoice_pdf_bytes', return_value=b'not-a-pdf'),
            patch('invoices.services.pdf.render_invoice_pdf_bytes', new=None),
        )
        for mocked in cases:
            with mocked:
                response = client.get(f'/api/documents/outgoing/{invoice.pk}/pdf/')
            self.assertEqual(response.status_code, 503)
            self.assertIn('application/json', response['Content-Type'])
            self.assertEqual(response.json(), INVOICE_PDF_UNAVAILABLE)
            raw = response.content.decode()
            self.assertNotIn('<html', raw.lower())
            self.assertNotIn('Traceback', raw)
            self.assertNotIn('/secret/path', raw)
            self.assertNotIn('RuntimeError', raw)

    def test_outgoing_pdf_filename_strips_path_and_crlf(self):
        invoice = self._invoice(invoice_number='../dir/evil\r\n2026-0099')
        response = self._auth_client().get(f'/api/documents/outgoing/{invoice.pk}/pdf/')
        self._assert_safe_pdf(response)
        self.assertIn('filename="R-evil2026-0099.pdf"', response['Content-Disposition'])

    def test_outgoing_pdf_repeat_download_does_not_mutate(self):
        invoice = self._invoice()
        invoices_before = Invoice.all_objects.filter(tenant=self.tenant).count()
        items_before = InvoiceItem.objects.filter(invoice=invoice).count()
        snapshot = (
            invoice.status,
            invoice.invoice_number,
            invoice.updated_at,
            invoice.subtotal,
            invoice.tax_amount,
            invoice.total_amount,
        )
        client = self._auth_client()
        first = client.get(f'/api/documents/outgoing/{invoice.pk}/pdf/')
        second = client.get(f'/api/documents/outgoing/{invoice.pk}/pdf/')
        self._assert_safe_pdf(first)
        self._assert_safe_pdf(second)
        invoice.refresh_from_db()
        self.assertEqual(
            (
                invoice.status,
                invoice.invoice_number,
                invoice.updated_at,
                invoice.subtotal,
                invoice.tax_amount,
                invoice.total_amount,
            ),
            snapshot,
        )
        self.assertEqual(Invoice.all_objects.filter(tenant=self.tenant).count(), invoices_before)
        self.assertEqual(InvoiceItem.objects.filter(invoice=invoice).count(), items_before)

    def test_existing_attachment_blob_downloads(self):
        expense = self._expense()
        payload = b'%PDF-1.4 minimal'
        attachment = ExpenseAttachment.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            uploaded_by=self.user,
            original_filename='../../dir/invoice.pdf\r\n',
            file=SimpleUploadedFile('invoice.pdf', payload, content_type='application/pdf'),
        )
        client = self._auth_client()
        response = client.get(
            f'/api/documents/incoming/{expense.pk}/attachments/{attachment.pk}/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('json', (response['Content-Type'] or '').lower())
        disposition = response['Content-Disposition']
        self.assertIn('filename', disposition.lower())
        self.assertNotIn('/', disposition)
        self.assertNotIn('\\', disposition)
        self.assertNotIn('\r', disposition)
        self.assertNotIn('\n', disposition)
        self.assertNotIn('..', disposition)
        self.assertIn('invoice.pdf', disposition)
        self.assertEqual(b''.join(response.streaming_content), payload)
        detail = client.get(f'/api/documents/incoming/{expense.pk}/').json()
        self.assertEqual(
            detail['attachments'][0]['download_available'],
            {'value': True, 'reason': None, 'source': 'storage'},
        )

    def test_missing_attachment_blob_is_410(self):
        expense = self._expense()
        attachment = ExpenseAttachment.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            uploaded_by=self.user,
            file=_minimal_pdf(),
        )
        stored_path = attachment.file.path
        os.remove(stored_path)
        self.assertTrue(ExpenseAttachment.all_objects.filter(pk=attachment.pk).exists())
        client = self._auth_client()
        response = client.get(
            f'/api/documents/incoming/{expense.pk}/attachments/{attachment.pk}/',
        )
        self.assertEqual(response.status_code, 410)
        self.assertIn('application/json', response['Content-Type'])
        self.assertEqual(
            response.json(),
            {
                'code': 'attachment_content_unavailable',
                'detail': 'Attachment content is unavailable.',
            },
        )
        raw = response.content.decode()
        self.assertNotIn(stored_path, raw)
        self.assertNotIn('FileNotFoundError', raw)
        self.assertNotIn('<html', raw.lower())
        detail = client.get(f'/api/documents/incoming/{expense.pk}/').json()
        self.assertEqual(
            detail['attachments'][0]['download_available'],
            {'value': False, 'reason': 'not_recorded', 'source': 'storage'},
        )

    def test_foreign_attachment_404(self):
        expense = self._expense()
        other_expense = self._expense()
        attachment = ExpenseAttachment.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            uploaded_by=self.user,
            file=_minimal_pdf(),
        )
        client = self._auth_client()
        self.assertEqual(
            client.get(
                f'/api/documents/incoming/{expense.pk}/attachments/{attachment.pk + 999}/',
            ).status_code,
            404,
        )
        self.assertEqual(
            client.get(
                f'/api/documents/incoming/{other_expense.pk}/attachments/{attachment.pk}/',
            ).status_code,
            404,
        )
        outsider = self._auth_client(self.outsider, host=OTHER_HOST)
        TenantMembership.objects.create(user=self.outsider, tenant=self.other, role='viewer')
        response = outsider.get(
            f'/api/documents/incoming/{expense.pk}/attachments/{attachment.pk}/',
        )
        self.assertEqual(response.status_code, 404)

    def test_export_has_as_of_and_escapes_formulas(self):
        self._invoice(invoice_number='=CMD')
        client = self._auth_client()
        response = client.get('/api/documents/export/?format=csv')
        self.assertEqual(response.status_code, 200)
        text = response.content.decode('utf-8-sig')
        self.assertIn('as_of', text)
        self.assertIn("'=CMD", text)
        self.assertEqual(escape_spreadsheet_cell('=1+1'), "'=1+1")
        self.assertEqual(escape_spreadsheet_cell('+x'), "'+x")
        self.assertEqual(escape_spreadsheet_cell('-x'), "'-x")
        self.assertEqual(escape_spreadsheet_cell('@x'), "'@x")

    def _export_id_order_from_csv(self, content: bytes) -> list[tuple[str, int]]:
        rows = list(csv.reader(io.StringIO(content.decode('utf-8-sig'))))
        header = rows[1]
        data = rows[2:]
        direction = header.index('direction')
        doc_id = header.index('id')
        return [(row[direction], int(row[doc_id])) for row in data]

    def _export_id_order_from_xlsx(self, content: bytes) -> list[tuple[str, int]]:
        ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            shared = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
                for item in root.findall('m:si', ns):
                    shared.append(''.join(
                        node.text or ''
                        for node in item.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')
                    ))
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))

            def cell_value(cell):
                value = cell.find('m:v', ns)
                if cell.get('t') == 's' and value is not None:
                    return shared[int(value.text)]
                if value is not None:
                    return value.text or ''
                inline = cell.find('m:is', ns)
                if inline is None:
                    return ''
                return ''.join(
                    node.text or ''
                    for node in inline.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')
                )

            parsed = []
            for row in sheet.findall('m:sheetData/m:row', ns):
                parsed.append([cell_value(cell) for cell in row.findall('m:c', ns)])
        header = parsed[1]
        direction = header.index('direction')
        doc_id = header.index('id')
        return [(row[direction], int(row[doc_id])) for row in parsed[2:]]

    def test_union_and_exports_materialize_full_combined_queryset(self):
        first = self._invoice(invoice_number='2026-0101', issue_date=date(2026, 6, 1))
        second = self._invoice(invoice_number='2026-0102', issue_date=date(2026, 5, 15))
        incoming = self._expense(expense_date=date(2026, 6, 1))
        filters = parse_filters({'direction': None})
        union = union_rows(self.tenant, filters, date(2026, 8, 19))
        materialized = materialize_union(union)
        self.assertGreaterEqual(len(materialized), 3)
        keys = [(row['_direction'], row['_doc_id']) for row in materialized]
        self.assertEqual(len(keys), len(set(keys)))
        client = self._auth_client()
        csv_response = client.get('/api/documents/export/?format=csv')
        xlsx_response = client.get('/api/documents/export/?format=xlsx')
        self.assertEqual(csv_response.status_code, 200)
        self.assertEqual(xlsx_response.status_code, 200)
        self.assertIn('text/csv', csv_response['Content-Type'])
        self.assertIn(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            xlsx_response['Content-Type'],
        )
        csv_ids = self._export_id_order_from_csv(csv_response.content)
        xlsx_ids = self._export_id_order_from_xlsx(xlsx_response.content)
        self.assertEqual(csv_ids, keys)
        self.assertEqual(xlsx_ids, keys)
        self.assertIn(('outgoing', first.pk), csv_ids)
        self.assertIn(('outgoing', second.pk), csv_ids)
        self.assertIn(('incoming', incoming.pk), csv_ids)

    def test_list_csv_xlsx_parity_same_set_and_order(self):
        self._invoice(invoice_number='2026-0201', issue_date=date(2026, 6, 2))
        self._invoice(invoice_number='2026-0202', issue_date=date(2026, 5, 10))
        self._expense(expense_date=date(2026, 6, 2))
        self._expense(expense_date=date(2026, 5, 10))
        client = self._auth_client()
        listed = []
        page = 1
        while True:
            body = client.get(f'/api/documents/?page={page}&page_size=2').json()
            listed.extend((row['direction'], row['id']) for row in body['results'])
            if page * 2 >= body['count']:
                break
            page += 1
        csv_ids = self._export_id_order_from_csv(
            client.get('/api/documents/export/?format=csv').content,
        )
        xlsx_ids = self._export_id_order_from_xlsx(
            client.get('/api/documents/export/?format=xlsx').content,
        )
        self.assertEqual(listed, csv_ids)
        self.assertEqual(listed, xlsx_ids)
        self.assertEqual(set(listed), set(csv_ids))
        self.assertGreaterEqual(len(listed), 4)

    def test_query_count_does_not_scale_with_page_size(self):
        for i in range(12):
            self._invoice(invoice_number=f'N-{i:04d}', issue_date=date(2026, 5, 1) + timedelta(days=i % 3))
        client = self._auth_client()
        with CaptureQueriesContext(connection) as small:
            client.get('/api/documents/?page_size=5&direction=outgoing')
        for i in range(12, 50):
            self._invoice(invoice_number=f'N-{i:04d}', issue_date=date(2026, 4, 1))
        with CaptureQueriesContext(connection) as large:
            client.get('/api/documents/?page_size=50&direction=outgoing')
        self.assertLess(len(large), len(small) + 8)
        self.assertLess(len(large), 80)

    def test_list_includes_incoming_and_outgoing(self):
        inv = self._invoice()
        exp = self._expense()
        body = self._auth_client().get('/api/documents/').json()
        ids = {(row['direction'], row['id']) for row in body['results']}
        self.assertIn(('outgoing', inv.pk), ids)
        self.assertIn(('incoming', exp.pk), ids)
        self.assertIn('as_of', body)
        self.assertIn('EUR', body['summary']['by_currency'])


    def _super_inbound_link(self, expense, *, pdf_path='', ubl_xml='', guid=None):
        from super_integration.models import SuperDocumentLink

        return SuperDocumentLink.all_objects.create(
            tenant=expense.tenant,
            direction=SuperDocumentLink.DIRECTION_INBOUND,
            super_guid=guid or str(uuid4()).replace('-', ''),
            ubl_xml=ubl_xml,
            pdf_path=pdf_path,
            content_type=ContentType.objects.get_for_model(Expense),
            object_id=expense.pk,
        )

    def _as4_inbound_link(self, expense, *, ubl_xml=''):
        from fiscal_gateway.models import As4DocumentLink

        return As4DocumentLink.all_objects.create(
            tenant=expense.tenant,
            direction=As4DocumentLink.DIRECTION_INBOUND,
            message_id=f'msg-{uuid4()}',
            ubl_xml=ubl_xml,
            content_type=ContentType.objects.get_for_model(Expense),
            object_id=expense.pk,
        )

    def test_incoming_pdf_200_and_detail_pdf_available(self):
        import tempfile
        from pathlib import Path

        expense = self._expense()
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp)
            rel = f'expenses/super/{expense.tenant_id}/{expense.pk}.pdf'
            abs_path = media / rel
            abs_path.parent.mkdir(parents=True)
            abs_path.write_bytes(b'%PDF-1.4 evidence')
            self._super_inbound_link(expense, pdf_path=rel, ubl_xml='<Invoice/>')
            with override_settings(MEDIA_ROOT=str(media)):
                client = self._auth_client()
                detail = client.get(f'/api/documents/incoming/{expense.pk}/').json()
                self.assertTrue(detail['pdf_available'])
                self.assertTrue(detail['ubl_available'])
                response = client.get(f'/api/documents/incoming/{expense.pk}/pdf/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'application/pdf')
            body = b''.join(response.streaming_content)
            self.assertTrue(body.startswith(b'%PDF-'))
            self.assertIn('attachment', response['Content-Disposition'].lower())
            self.assertIn(f'{expense.pk}.pdf', response['Content-Disposition'])

    def test_incoming_pdf_missing_link_is_404_flag_false(self):
        expense = self._expense()
        client = self._auth_client()
        detail = client.get(f'/api/documents/incoming/{expense.pk}/').json()
        self.assertFalse(detail['pdf_available'])
        self.assertEqual(client.get(f'/api/documents/incoming/{expense.pk}/pdf/').status_code, 404)

    def test_incoming_pdf_link_without_file_is_410_flag_false(self):
        import tempfile

        expense = self._expense()
        rel = f'expenses/super/{expense.tenant_id}/missing-{expense.pk}.pdf'
        self._super_inbound_link(expense, pdf_path=rel)
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                client = self._auth_client()
                detail = client.get(f'/api/documents/incoming/{expense.pk}/').json()
                self.assertFalse(detail['pdf_available'])
                response = client.get(f'/api/documents/incoming/{expense.pk}/pdf/')
            self.assertEqual(response.status_code, 410)
            self.assertEqual(response.json()['code'], 'document_pdf_content_unavailable')

    def test_incoming_pdf_path_traversal_is_404(self):
        import tempfile

        expense = self._expense()
        self._super_inbound_link(expense, pdf_path='../../etc/passwd')
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                self.assertEqual(
                    self._auth_client().get(f'/api/documents/incoming/{expense.pk}/pdf/').status_code,
                    404,
                )

    def test_incoming_ubl_prefers_super_over_as4(self):
        expense = self._expense()
        self._as4_inbound_link(expense, ubl_xml='<As4>second</As4>')
        self._super_inbound_link(expense, ubl_xml='<Super>first</Super>')
        response = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/ubl/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/xml')
        self.assertIn(b'<Super>first</Super>', response.content)
        self.assertNotIn(b'<As4>', response.content)
        detail = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/').json()
        self.assertTrue(detail['ubl_available'])

    def test_incoming_ubl_accept_application_xml_is_not_406(self):
        """UI sends Accept: application/xml; DRF must not content-negotiate to 406."""
        expense = self._expense()
        self._super_inbound_link(expense, ubl_xml='<Invoice>ok</Invoice>')
        response = self._auth_client().get(
            f'/api/documents/incoming/{expense.pk}/ubl/',
            HTTP_ACCEPT='application/xml',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/xml')
        self.assertIn(b'<Invoice>ok</Invoice>', response.content)

    def test_outgoing_pdf_accept_application_pdf_is_not_406(self):
        invoice = self._invoice()
        response = self._auth_client().get(
            f'/api/documents/outgoing/{invoice.pk}/pdf/',
            HTTP_ACCEPT='application/pdf',
        )
        self._assert_safe_pdf(response)

    def test_incoming_ubl_falls_back_to_as4(self):
        expense = self._expense()
        self._super_inbound_link(expense, ubl_xml='')
        self._as4_inbound_link(expense, ubl_xml='<As4>only</As4>')
        response = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/ubl/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<As4>only</As4>', response.content)

    def test_incoming_ubl_absent_is_404(self):
        expense = self._expense()
        self.assertEqual(
            self._auth_client().get(f'/api/documents/incoming/{expense.pk}/ubl/').status_code,
            404,
        )
        detail = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/').json()
        self.assertFalse(detail['ubl_available'])

    def test_outgoing_and_deposit_ubl_are_404(self):
        invoice = self._invoice()
        client = self._auth_client()
        self.assertEqual(client.get(f'/api/documents/outgoing/{invoice.pk}/ubl/').status_code, 404)
        self.assertEqual(client.get('/api/documents/deposit/1/ubl/').status_code, 404)

    def test_incoming_evidence_cross_tenant_is_404(self):
        import tempfile
        from pathlib import Path

        expense = self._expense()
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp)
            rel = f'expenses/super/{expense.tenant_id}/{expense.pk}.pdf'
            abs_path = media / rel
            abs_path.parent.mkdir(parents=True)
            abs_path.write_bytes(b'%PDF-1.4 secret')
            self._super_inbound_link(expense, pdf_path=rel, ubl_xml='<Invoice/>')
            other_category = ExpenseCategory.all_objects.create(tenant=self.other, name='Ostalo-X')
            other_expense = Expense.all_objects.create(
                tenant=self.other,
                expense_number='OTHER-1',
                status='draft',
                category=other_category,
                supplier=self.other_partner,
                amount=Decimal('1.00'),
                tax_amount=Decimal('0.00'),
                currency='EUR',
                expense_date=date(2026, 5, 1),
                description='x',
                created_by=self.user,
            )
            TenantMembership.objects.get_or_create(
                user=self.outsider, tenant=self.other, defaults={'role': 'viewer'}
            )
            outsider = self._auth_client(self.outsider, host=OTHER_HOST)
            with override_settings(MEDIA_ROOT=str(media)):
                self.assertEqual(
                    outsider.get(f'/api/documents/incoming/{expense.pk}/pdf/').status_code,
                    404,
                )
                self.assertEqual(
                    outsider.get(f'/api/documents/incoming/{expense.pk}/ubl/').status_code,
                    404,
                )
                self.assertEqual(
                    outsider.get(f'/api/documents/incoming/{expense.pk}/').status_code,
                    404,
                )
                self.assertEqual(
                    self._auth_client().get(
                        f'/api/documents/incoming/{other_expense.pk}/pdf/'
                    ).status_code,
                    404,
                )

    def test_incoming_detail_cvh_shaped_ubl_contract(self):
        expense = self._expense(
            receipt_number='26210-H120-5154',
            amount=Decimal('372.20'),
            tax_amount=Decimal('7.49'),
            status='draft',
        )
        Expense.all_objects.filter(pk=expense.pk).update(source='super')
        expense.refresh_from_db()
        guid = '7a8e295b-5859-4a39-a289-3b92b1ae7469'
        self._super_inbound_link(expense, ubl_xml=_PR_A_UBL, guid=guid)
        with override_settings(SUPER_PORTAL_BASE_URL='https://moj.super.hr'):
            detail = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/').json()

        self.assertEqual(detail['number'], '26210-H120-5154')
        self.assertTrue(detail['ubl_available'])
        self.assertFalse(detail['pdf_available'])
        self.assertEqual(detail['document']['business_process'], 'P5')
        self.assertEqual(detail['document']['delivery_date'], '2026-08-06')
        self.assertEqual(detail['status']['document'], 'received')
        self.assertEqual(detail['status']['workflow'], 'pending')
        self.assertEqual(detail['status']['integration'], 'received')
        self.assertEqual(detail['status']['posting'], 'unposted')
        self.assertEqual(detail['status']['vat'], 'absent')
        self.assertIsNone(detail['status']['subledger'])
        self.assertIsNone(detail['status']['payment'])
        self.assertEqual(len(detail['lines']), 1)
        self.assertEqual(detail['lines'][0]['classification']['scheme'], 'CG')
        self.assertEqual(detail['lines'][0]['classification']['code'], '71.20.04')
        self.assertIsNone(detail['lines'][0]['vat_amount'])
        self.assertEqual(len(detail['charges']), 1)
        self.assertEqual(detail['charges'][0]['code'], '05100')
        self.assertEqual(detail['totals']['grand_total'], '372.20')
        self.assertEqual(detail['totals']['prepaid'], '372.20')
        self.assertEqual(detail['totals']['payable'], '0.00')
        self.assertEqual(detail['totals']['charges_total'], '334.75')
        self.assertEqual(detail['totals']['line_net'], '29.96')
        self.assertTrue(any(r['type'] == 'originator' for r in detail['references']))
        self.assertEqual(detail['integration']['source'], 'super')
        self.assertEqual(detail['integration']['external_id'], guid)
        self.assertEqual(
            detail['integration']['external_view_url'],
            f'https://moj.super.hr/hr/Client/Invoice/ViewDetails/{guid}',
        )
        self.assertEqual(detail['accounting']['status'], 'unposted')
        self.assertFalse(detail['vat_context']['recorded'])
        self.assertIsNone(detail['subledger_context']['item_id'])
        self.assertFalse(detail['payment']['matched'])

    def test_incoming_detail_prc_accounting_context_present_and_absent(self):
        unposted = self._expense(status='draft')
        absent = self._auth_client().get(f'/api/documents/incoming/{unposted.pk}/').json()
        self.assertEqual(absent['status']['posting'], 'unposted')
        self.assertEqual(absent['status']['vat'], 'absent')
        self.assertIsNone(absent['status']['subledger'])
        self.assertIsNone(absent['status']['payment'])
        self.assertEqual(absent['accounting']['journal_entry_id'], None)
        self.assertEqual(absent['accounting']['lines'], [])
        self.assertFalse(absent['vat_context']['recorded'])
        self.assertEqual(absent['vat_context']['rates'], [])
        self.assertIsNone(absent['subledger_context']['state'])
        self.assertFalse(absent['payment']['matched'])
        self.assertIsNone(absent['payment']['reconcile_status'])

        expense = self._expense(status='approved', amount=Decimal('50.00'), tax_amount=Decimal('10.00'))
        je = self._journal(expense, status='posted', number='JE-PRC-1')
        account = ChartOfAccounts.all_objects.filter(tenant=self.tenant).first()
        self.assertIsNotNone(account)
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=account,
            description='trošak',
            debit_amount=Decimal('50.00'),
            credit_amount=Decimal('0.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=account,
            description='obveza',
            debit_amount=Decimal('0.00'),
            credit_amount=Decimal('50.00'),
        )
        sub = self._subledger(
            expense,
            status='closed',
            direction='payable',
            open_amount=Decimal('0.00'),
            journal_entry=je,
        )
        SubledgerAllocation.all_objects.create(
            tenant=self.tenant,
            subledger_item=sub,
            journal_entry=je,
            amount=Decimal('50.00'),
        )
        self._ledger(expense)
        bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Glavni',
            bank_name='PBZ',
            account_number='110',
            iban='HR6124070001100204771',
        )
        statement = BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number='ST-PRC-1',
            bank_account=bank_account,
            statement_date=date(2026, 5, 20),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('50.00'),
            imported_by=self.user,
        )
        BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=statement,
            transaction_date=date(2026, 5, 20),
            amount=Decimal('50.00'),
            currency='EUR',
            transaction_type='debit',
            description='plaćanje',
            reference='HR03-PRC',
            match_status='matched',
            matched_journal_entry=je,
        )

        detail = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/').json()
        self.assertEqual(detail['status']['posting'], 'posted')
        self.assertEqual(detail['status']['vat'], 'recorded')
        self.assertEqual(detail['status']['subledger'], 'closed')
        self.assertEqual(detail['status']['payment'], 'matched')
        self.assertEqual(detail['accounting']['journal_entry_id'], je.pk)
        self.assertEqual(detail['accounting']['entry_number'], 'JE-PRC-1')
        self.assertEqual(detail['accounting']['status'], 'posted')
        self.assertEqual(detail['accounting']['debit_total'], '50.00')
        self.assertEqual(detail['accounting']['credit_total'], '50.00')
        self.assertEqual(len(detail['accounting']['lines']), 2)
        self.assertTrue(detail['vat_context']['recorded'])
        self.assertEqual(detail['vat_context']['period'], '2026-05')
        self.assertEqual(detail['vat_context']['total_vat'], '10.00')
        self.assertEqual(detail['subledger_context']['item_id'], sub.pk)
        self.assertEqual(detail['subledger_context']['state'], 'closed')
        self.assertEqual(detail['subledger_context']['open_amount'], '0.00')
        self.assertEqual(detail['subledger_context']['allocated_amount'], '50.00')
        self.assertEqual(len(detail['subledger_context']['allocations']), 1)
        self.assertEqual(detail['subledger_context']['allocations'][0]['journal_entry_id'], je.pk)
        self.assertTrue(detail['payment']['matched'])
        self.assertEqual(detail['payment']['reconcile_status'], 'matched')
        self.assertEqual(detail['payment']['amount'], '50.00')
        self.assertEqual(detail['payment']['account_mask'], '…4771')
        self.assertEqual(detail['payment']['reference'], 'HR03-PRC')

    def test_incoming_detail_without_super_portal_url_null(self):
        expense = self._expense()
        Expense.all_objects.filter(pk=expense.pk).update(source='super')
        expense.refresh_from_db()
        self._super_inbound_link(expense, ubl_xml=_PR_A_UBL, guid='abc-guid')
        with override_settings(SUPER_PORTAL_BASE_URL=''):
            detail = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/').json()
        self.assertIsNone(detail['integration']['external_view_url'])
        self.assertEqual(detail['integration']['external_id'], 'abc-guid')

    def test_incoming_detail_non_super_empty_structured_blocks(self):
        expense = self._expense()
        detail = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/').json()
        self.assertEqual(detail['lines'], [])
        self.assertEqual(detail['charges'], [])
        self.assertEqual(detail['references'], [])
        self.assertIsNone(detail['integration']['external_view_url'])
        self.assertFalse(detail['ubl_available'])
        self.assertFalse(detail['pdf_available'])
        self.assertIsNone(detail['totals']['prepaid'])
        self.assertEqual(detail['supplier']['id'], expense.supplier_id)

    def test_incoming_detail_cross_tenant_is_404(self):
        expense = self._expense()
        self._super_inbound_link(expense, ubl_xml=_PR_A_UBL)
        TenantMembership.objects.get_or_create(
            user=self.outsider, tenant=self.other, defaults={'role': 'viewer'}
        )
        outsider = self._auth_client(self.outsider, host=OTHER_HOST)
        self.assertEqual(
            outsider.get(f'/api/documents/incoming/{expense.pk}/').status_code,
            404,
        )

    def test_settlement_trail_bank_pfc_possible_duplicate_expense_paid(self):
        expense = self._expense(amount=Decimal('33000.00'), tax_amount=Decimal('0.00'))
        approved = self._journal(
            expense,
            number='JE-APP',
            description='[expense_approved] T — 33000.00 EUR',
        )
        bank_je = self._journal(
            expense,
            number='JE-BANK',
            description='[bank_reconcile:btx-1] expense Bank reconcile',
        )
        pfc_je = self._journal(
            expense,
            number='JE-PFC',
            description='[private_funds:1] supplier_payment',
        )
        # Real private-funds JEs may omit expense GFK; allocation alone must classify.
        JournalEntry.all_objects.filter(pk=pfc_je.pk).update(
            source_content_type=None,
            source_object_id=None,
        )
        paid_je = self._journal(
            expense,
            number='JE-PAID',
            description='[expense_paid] T — 33000.00 EUR',
        )
        account = ChartOfAccounts.all_objects.filter(tenant=self.tenant).first()
        JournalEntryLine.objects.create(
            journal_entry=paid_je,
            account=account,
            debit_amount=Decimal('33000.00'),
            credit_amount=Decimal('0.00'),
        )
        sub = self._subledger(
            expense,
            status='closed',
            direction='payable',
            open_amount=Decimal('0.00'),
            journal_entry=approved,
        )
        SubledgerAllocation.all_objects.create(
            tenant=self.tenant,
            subledger_item=sub,
            journal_entry=bank_je,
            amount=Decimal('23100.00'),
        )
        SubledgerAllocation.all_objects.create(
            tenant=self.tenant,
            subledger_item=sub,
            journal_entry=pfc_je,
            amount=Decimal('9900.00'),
        )
        bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Glavni',
            bank_name='PBZ',
            account_number='111',
            iban='HR6124070001100204771',
        )
        statement = BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number='ST-TRAIL-1',
            bank_account=bank_account,
            statement_date=date(2026, 5, 20),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('23100.00'),
            imported_by=self.user,
        )
        tx = BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=statement,
            transaction_date=date(2026, 5, 20),
            amount=Decimal('23100.00'),
            currency='EUR',
            transaction_type='debit',
            description='doznaka',
            counterparty_name='SaM Automobile',
            match_status='matched',
            matched_journal_entry=bank_je,
        )
        ante = Partner.all_objects.create(
            tenant=self.tenant,
            name='Ante Vrcan',
            tax_number='11528564544',
            partner_type='other',
            status='active',
            address='X',
            city='Zagreb',
            postal_code='10000',
            country_code='HR',
        )
        expense_ct = ContentType.objects.get_for_model(Expense)
        PrivateFundsClaim.all_objects.create(
            tenant=self.tenant,
            number='PFC-202605-0001',
            claim_type=PrivateFundsClaim.CLAIM_SUPPLIER_PAYMENT,
            partner=ante,
            amount=Decimal('9900.00'),
            currency='EUR',
            claim_date=date(2026, 5, 20),
            status=PrivateFundsClaim.STATUS_POSTED,
            related_content_type=expense_ct,
            related_object_id=expense.pk,
            journal_entry=pfc_je,
            created_by=self.user,
        )

        detail = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/').json()
        trail = detail['settlement_trail']
        self.assertEqual(trail['totals']['obligation'], '33000.00')
        self.assertEqual(trail['totals']['allocated'], '33000.00')
        self.assertEqual(trail['totals']['open'], '0.00')
        self.assertEqual(trail['obligation']['journal_entry_id'], approved.pk)
        kinds = [row['kind'] for row in trail['closings']]
        self.assertEqual(kinds, ['bank', 'private_funds'])
        bank_row = trail['closings'][0]
        self.assertEqual(bank_row['bank_transaction_id'], tx.pk)
        self.assertEqual(bank_row['bank_statement_id'], statement.pk)
        self.assertEqual(bank_row['amount'], '23100.00')
        pfc_row = trail['closings'][1]
        self.assertEqual(pfc_row['claim_number'], 'PFC-202605-0001')
        self.assertEqual(pfc_row['partner_id'], ante.pk)
        self.assertEqual(pfc_row['amount'], '9900.00')
        self.assertEqual(len(trail['system_entries']), 1)
        self.assertEqual(trail['system_entries'][0]['journal_entry_id'], paid_je.pk)
        self.assertEqual(trail['system_entries'][0]['amount'], '33000.00')
        codes = [w['code'] for w in trail['warnings']]
        self.assertIn('possible_duplicate_expense_paid', codes)

    def test_settlement_trail_partial_close_no_duplicate_warning(self):
        expense = self._expense(amount=Decimal('100.00'), tax_amount=Decimal('0.00'))
        approved = self._journal(
            expense,
            number='JE-P-APP',
            description='[expense_approved] partial',
        )
        bank_je = self._journal(
            expense,
            number='JE-P-BANK',
            description='[bank_reconcile:btx-2] partial',
        )
        self._journal(
            expense,
            number='JE-P-PAID',
            description='[expense_paid] still open',
        )
        sub = self._subledger(
            expense,
            status='open',
            direction='payable',
            open_amount=Decimal('60.00'),
            journal_entry=approved,
        )
        SubledgerAllocation.all_objects.create(
            tenant=self.tenant,
            subledger_item=sub,
            journal_entry=bank_je,
            amount=Decimal('40.00'),
        )
        bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Glavni2',
            bank_name='PBZ',
            account_number='112',
            iban='HR6124070001100204772',
        )
        statement = BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number='ST-TRAIL-2',
            bank_account=bank_account,
            statement_date=date(2026, 5, 20),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('40.00'),
            imported_by=self.user,
        )
        BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=statement,
            transaction_date=date(2026, 5, 20),
            amount=Decimal('40.00'),
            currency='EUR',
            transaction_type='debit',
            description='partial',
            match_status='matched',
            matched_journal_entry=bank_je,
        )

        detail = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/').json()
        trail = detail['settlement_trail']
        self.assertEqual(trail['totals']['open'], '60.00')
        self.assertEqual(trail['totals']['allocated'], '40.00')
        self.assertEqual([c['kind'] for c in trail['closings']], ['bank'])
        self.assertEqual(trail['warnings'], [])
        self.assertEqual(len(trail['system_entries']), 1)

    def test_settlement_trail_other_kind_allocation_no_crash(self):
        expense = self._expense(amount=Decimal('80.00'), tax_amount=Decimal('0.00'))
        approved = self._journal(
            expense,
            number='JE-O-APP',
            description='[expense_approved] other',
        )
        other_je = self._journal(
            expense,
            number='JE-O-MAN',
            description='Manual settlement without bank or PFC',
        )
        sub = self._subledger(
            expense,
            status='closed',
            direction='payable',
            open_amount=Decimal('0.00'),
            journal_entry=approved,
        )
        SubledgerAllocation.all_objects.create(
            tenant=self.tenant,
            subledger_item=sub,
            journal_entry=other_je,
            amount=Decimal('80.00'),
        )

        detail = self._auth_client().get(f'/api/documents/incoming/{expense.pk}/').json()
        trail = detail['settlement_trail']
        self.assertEqual(len(trail['closings']), 1)
        self.assertEqual(trail['closings'][0]['kind'], 'other')
        self.assertEqual(trail['closings'][0]['journal_entry_id'], other_je.pk)
        self.assertIsNone(trail['closings'][0]['bank_transaction_id'])
        self.assertIsNone(trail['closings'][0]['private_funds_claim_id'])
        self.assertEqual(trail['warnings'], [])
