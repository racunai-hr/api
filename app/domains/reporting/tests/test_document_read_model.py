"""Acceptance tests for ADR-0020 document read model."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
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
from domains.reporting.documents.projection import collect_controls, vat_amount_check
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
        self.assertNotEqual(body['operational_status']['value'], 'paid')

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

    def test_foreign_attachment_404(self):
        expense = self._expense()
        attachment = ExpenseAttachment.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            uploaded_by=self.user,
            file=_minimal_pdf(),
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
