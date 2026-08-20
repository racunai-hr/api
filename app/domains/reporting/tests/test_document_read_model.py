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
    FiscalPeriod,
    JournalEntry,
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
from tenants.models import Tenant, TenantMembership


HOST = 'docread.racunai.hr'
OTHER_HOST = 'docother.racunai.hr'


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

    def _journal(self, source, *, status='posted', number='JE-1'):
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
            description='test',
            created_by=self.user,
            posted_by=self.user if status == 'posted' else None,
            posted_at=timezone.now() if status == 'posted' else None,
            source_content_type=ct,
            source_object_id=source.pk,
            fiscal_period=period,
        )

    def _subledger(self, source, *, status='open', open_amount=None, direction='receivable'):
        je = self._journal(source, number=f'JE-SL-{source.pk}')
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
