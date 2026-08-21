"""Read-only VAT projection prepare. Does not write ledger or VATProjectionRun."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from accounting.models import (
    ChartOfAccounts,
    JournalEntry,
    JournalEntryLine,
    VATLedgerEntry,
    VATLedgerOrigin,
    VATPeriod,
    VATProjectionRun,
)
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_projection.contracts import ProjectedLedgerRow, VatProjectionStatus
from accounting.services.tax_projection.identity import identity_of
from accounting.services.tax_projection.prepare import prepare_vat_projection
from accounting.services.tax_shadow.runner import ledger_fingerprint
from accounting.services.vat import generate_vat_ledger
from domains.tax.classification.engine import reconcile_rc_bases
from expenses.models import Expense, ExpenseCategory
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from tenants.models import Tenant


class RowIdentityTests(SimpleTestCase):
    def test_same_source_different_row_role_is_not_duplicate(self):
        def row(role: str) -> ProjectedLedgerRow:
            return ProjectedLedgerRow(
                source_kind='journal_line',
                source_id=11,
                source_line_kind='journal_line',
                source_line_id=22,
                row_role=role,
                event_kind='original',
                box='207',
                category='eu_acquisition',
                direction='input',
                ledger_type='I-RA',
                base_amount=Decimal('8000.00'),
                tax_amount=Decimal('0.00') if role == 'rc_acquisition' else Decimal('2000.00'),
                sign=Decimal('1'),
                rule_code='JE_207',
                mapping_version=7,
            )

        first = identity_of(row('rc_acquisition'))
        second = identity_of(row('rc_output'))
        self.assertEqual(first[:4], second[:4])
        self.assertNotEqual(first, second)


class ProjectionPrepareTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='proj-prep', name='Projection Co')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='proj-prep', password='test')
        cls.partner = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Kupac d.o.o.',
            tax_number='12345678901',
            partner_type='customer',
            status='active',
            address='Ulica 1',
            city='Šibenik',
            postal_code='22000',
        )
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Dobavljač d.o.o.',
            tax_number='98765432109',
            partner_type='supplier',
            status='active',
            address='Ulica 1',
            city='Zagreb',
            postal_code='10000',
            country='Croatia',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

    def _account(self, code):
        return ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code=code)

    def _period(self, year=2026, month=4, status='open'):
        period, _created = VATPeriod.all_objects.get_or_create(
            tenant=self.tenant, year=year, month=month, defaults={'status': status},
        )
        if period.status != status:
            period.status = status
            period.save(update_fields=['status'])
        return period

    def _sent_invoice(self, *, rate=Decimal('25.00'), price=Decimal('100.00'), day=5, item_name='Usluga'):
        invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            company_to=self.partner,
            issue_date=date(2026, 4, day),
            due_date=date(2026, 4, 20),
            status='sent',
            subtotal=price,
            tax_amount=(price * rate / Decimal('100')).quantize(Decimal('0.01')),
            total_amount=price + (price * rate / Decimal('100')).quantize(Decimal('0.01')),
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name=item_name,
            quantity=1,
            unit_price=price,
            tax_rate=rate,
        )
        return invoice

    def _generic_expense(self, *, number='EXP-001', amount=Decimal('125.00'), tax=Decimal('25.00')):
        return Expense.all_objects.create(
            tenant=self.tenant,
            expense_number=number,
            status='approved',
            category=self.category,
            supplier=self.supplier,
            amount=amount,
            tax_amount=tax,
            expense_date=date(2026, 4, 12),
            description='Generic pretporez',
            created_by=self.user,
        )

    def _manual(self, period, *, box, base=Decimal('0.00'), vat=Decimal('0.00'), number='MANUAL'):
        return VATLedgerEntry.all_objects.create(
            tenant=self.tenant,
            vat_period=period,
            ledger_type=VATLedgerEntry.LEDGER_U_RA,
            entry_date=date(2026, 4, 15),
            document_number=number,
            partner_name='Ručno',
            partner_oib='',
            base_amount=base,
            vat_rate=Decimal('0.00'),
            vat_amount=vat,
            vat_box=box,
            is_manual=True,
        )

    def _ledger_guard(self, period):
        return (
            VATLedgerEntry.all_objects.filter(vat_period=period).count(),
            ledger_fingerprint(period),
            VATProjectionRun.all_objects.filter(vat_period=period).count(),
        )

    def _assert_no_writes(self, period, before):
        self.assertEqual(self._ledger_guard(period), before)

    def test_origin_manual_sync_and_legacy_default(self):
        period = self._period()
        auto = VATLedgerEntry.all_objects.create(
            tenant=self.tenant,
            vat_period=period,
            ledger_type=VATLedgerEntry.LEDGER_I_RA,
            entry_date=date(2026, 4, 1),
            document_number='AUTO-1',
            partner_name='X',
            partner_oib='',
            base_amount=Decimal('10.00'),
            vat_rate=Decimal('25.00'),
            vat_amount=Decimal('2.50'),
            vat_box='203',
        )
        manual = self._manual(period, box='611', vat=Decimal('5.00'))
        auto.refresh_from_db()
        manual.refresh_from_db()
        self.assertEqual(auto.origin, VATLedgerOrigin.LEGACY)
        self.assertEqual(manual.origin, VATLedgerOrigin.MANUAL)

    def test_two_prepares_same_snapshot_same_fingerprints(self):
        self._sent_invoice()
        period = self._period()
        before = self._ledger_guard(period)
        first = prepare_vat_projection(period)
        second = prepare_vat_projection(period)
        self.assertEqual(first.status, VatProjectionStatus.READY)
        self.assertEqual(first.input_fingerprint, second.input_fingerprint)
        self.assertEqual(first.output_fingerprint, second.output_fingerprint)
        self.assertTrue(first.writable)
        self._assert_no_writes(period, before)

    def test_review_rejects_whole_candidate(self):
        self._sent_invoice(rate=Decimal('10.00'), price=Decimal('100.00'))
        period = self._period()
        before = self._ledger_guard(period)
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.REJECTED)
        self.assertFalse(candidate.writable)
        self.assertEqual(candidate.primary_rejection_code, 'UNKNOWN_RATE_NO_LONGER_SKIPPED')
        self._assert_no_writes(period, before)

    def test_invalid_reversal_rejects_whole_candidate(self):
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202604-rev-src',
            entry_date=date(2026, 4, 10),
            status='posted',
            description='Pretporez',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1400'), debit_amount=Decimal('25.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('2201'), credit_amount=Decimal('25.00'),
        )
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        reversal = entry.reverse(self.user)
        reversal.entry_date = date(2026, 4, 12)
        reversal.save(update_fields=['entry_date'])
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        before = self._ledger_guard(period)
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.REJECTED)
        self.assertIn('reversal_missing_ledger_evidence', [issue.code for issue in candidate.issues])
        self._assert_no_writes(period, before)

    def test_not_tax_relevant_has_no_row(self):
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202604-bank',
            entry_date=date(2026, 4, 8),
            status='posted',
            description='Banka',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1000'), debit_amount=Decimal('50.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('2201'), credit_amount=Decimal('50.00'),
        )
        period = self._period()
        before = self._ledger_guard(period)
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.READY)
        self.assertEqual(candidate.not_relevant_count, 2)
        self.assertEqual(candidate.rows, ())
        self._assert_no_writes(period, before)

    def test_reversal_inverts_origin_tax_effect(self):
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202604-keep',
            entry_date=date(2026, 4, 11),
            status='posted',
            description='Pretporez keep',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1400'), debit_amount=Decimal('10.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('2201'), credit_amount=Decimal('10.00'),
        )
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        reversal = entry.reverse(self.user)
        reversal.entry_date = date(2026, 4, 12)
        reversal.save(update_fields=['entry_date'])
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        before = self._ledger_guard(period)
        candidate = prepare_vat_projection(period)
        reversals = [row for row in candidate.rows if row.event_kind == 'reversal']
        self.assertEqual(candidate.status, VatProjectionStatus.READY)
        self.assertEqual(len(reversals), 1)
        self.assertEqual(reversals[0].sign, Decimal('-1'))
        self.assertEqual(reversals[0].box, '303')
        self._assert_no_writes(period, before)

    def test_610_once_from_generated_plus_manual_viii1(self):
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202604-car',
            entry_date=date(2026, 4, 9),
            status='posted',
            description='Auto',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('032001'), debit_amount=Decimal('100.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1000'), credit_amount=Decimal('100.00'),
        )
        period = self._period()
        self._manual(period, box='611', vat=Decimal('25.00'), number='MAN-611')
        before = self._ledger_guard(period)
        candidate = prepare_vat_projection(period)
        boxes_610 = [row for row in candidate.rows if row.box == '610']
        self.assertEqual(candidate.status, VatProjectionStatus.READY)
        self.assertEqual(len(boxes_610), 1)
        self.assertEqual(boxes_610[0].row_role, 'period_610')
        self.assertEqual(boxes_610[0].base_amount, Decimal('125.00'))
        self._assert_no_writes(period, before)

    def test_changing_manual_viii1_changes_fingerprints_and_610(self):
        period = self._period()
        manual = self._manual(period, box='611', vat=Decimal('25.00'), number='MAN-611')
        first = prepare_vat_projection(period)
        first_610 = next(row for row in first.rows if row.box == '610')
        manual.vat_amount = Decimal('40.00')
        manual.save(update_fields=['vat_amount'])
        second = prepare_vat_projection(period)
        second_610 = next(row for row in second.rows if row.box == '610')
        self.assertNotEqual(first.input_fingerprint, second.input_fingerprint)
        self.assertNotEqual(first.output_fingerprint, second.output_fingerprint)
        self.assertEqual(first_610.base_amount, Decimal('25.00'))
        self.assertEqual(second_610.base_amount, Decimal('40.00'))
        self.assertEqual(VATProjectionRun.all_objects.filter(vat_period=period).count(), 0)

    def test_manuals_never_enter_reconcile_rc_bases(self):
        period = self._period()
        self._manual(period, box='612', base=Decimal('50.00'), number='MAN-612')
        seen = []
        real = reconcile_rc_bases

        def wrapper(rows):
            seen.append(rows)
            return real(rows)

        with patch('accounting.services.tax_projection.prepare.reconcile_rc_bases', side_effect=wrapper):
            candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.READY)
        self.assertEqual(len(seen), 1)
        self.assertFalse(any(row.source_kind == 'manual_ledger' for row in seen[0]))

    def test_manual_610_conflict(self):
        period = self._period()
        self._manual(period, box='612', base=Decimal('50.00'), number='MAN-612')
        conflict = self._manual(period, box='610', base=Decimal('50.00'), number='MAN-610')
        before = self._ledger_guard(period)
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.REJECTED)
        self.assertEqual(candidate.primary_rejection_code, 'MANUAL_610_CONFLICT')
        self.assertTrue(VATLedgerEntry.all_objects.filter(pk=conflict.pk).exists())
        self._assert_no_writes(period, before)

    def test_generic_expense_303_rejected(self):
        self._generic_expense()
        period = self._period()
        before = self._ledger_guard(period)
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.REJECTED)
        self.assertEqual(candidate.primary_rejection_code, 'EXPENSE_GENERIC_303_REMOVED')
        self.assertFalse(candidate.writable)
        self._assert_no_writes(period, before)

    def test_clean_submitted_period_ready_not_writable(self):
        self._sent_invoice()
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        period.status = 'submitted'
        period.save(update_fields=['status'])
        before = self._ledger_guard(period)
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.READY)
        self.assertFalse(candidate.writable)
        self.assertEqual(candidate.primary_rejection_code, '')
        self._assert_no_writes(period, before)

    def test_clean_closed_period_ready_not_writable(self):
        self._sent_invoice(day=6, item_name='Closed')
        period = self._period(status='closed')
        before = self._ledger_guard(period)
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.READY)
        self.assertFalse(candidate.writable)
        self._assert_no_writes(period, before)

    def test_submitted_generic_303_rejected_for_review_not_lifecycle(self):
        self._generic_expense(number='EXP-sub')
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        period.status = 'submitted'
        period.save(update_fields=['status'])
        before = self._ledger_guard(period)
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.REJECTED)
        self.assertEqual(candidate.primary_rejection_code, 'EXPENSE_GENERIC_303_REMOVED')
        codes = [issue.code for issue in candidate.issues]
        self.assertNotIn('submitted', codes)
        self.assertFalse(any('lifecycle' in issue.code.lower() for issue in candidate.issues))
        self._assert_no_writes(period, before)

    def test_source_change_during_prepare_is_stale(self):
        self._sent_invoice()
        period = self._period()
        before = self._ledger_guard(period)
        hashes = iter(['a' * 64, 'b' * 64])
        with patch(
            'accounting.services.tax_projection.prepare.snapshot_fingerprint',
            side_effect=lambda *args, **kwargs: next(hashes),
        ):
            candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.STALE)
        self.assertEqual(candidate.primary_rejection_code, 'STALE_PROJECTION')
        self.assertFalse(candidate.writable)
        self.assertEqual(candidate.rows, ())
        self._assert_no_writes(period, before)

    def test_two_issues_sorted_primary_is_first(self):
        self._sent_invoice(rate=Decimal('10.00'), price=Decimal('80.00'), day=7)
        self._generic_expense(number='EXP-two')
        period = self._period()
        before = self._ledger_guard(period)
        candidate = prepare_vat_projection(period)
        codes = [issue.code for issue in candidate.issues]
        self.assertEqual(codes, sorted(codes))
        self.assertEqual(candidate.primary_rejection_code, codes[0])
        self.assertEqual(
            set(codes),
            {'EXPENSE_GENERIC_303_REMOVED', 'UNKNOWN_RATE_NO_LONGER_SKIPPED'},
        )
        self.assertEqual(candidate.primary_rejection_code, 'EXPENSE_GENERIC_303_REMOVED')
        self._assert_no_writes(period, before)

    def test_rc_pair_different_row_roles_not_duplicate(self):
        asset = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202604-0373',
            entry_date=date(2026, 4, 22),
            status='posted',
            description='EU stjecanje',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=asset, account=self._account('0373'), debit_amount=Decimal('8000.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=asset, account=self._account('1000'), credit_amount=Decimal('8000.00'),
        )
        rc = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202604-rc',
            entry_date=date(2026, 4, 22),
            status='posted',
            description='RC EU',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=rc, account=self._account('14022'), debit_amount=Decimal('2000.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=rc, account=self._account('24022'), credit_amount=Decimal('2000.00'),
        )
        period = self._period()
        before = self._ledger_guard(period)
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.READY)
        roles = {row.row_role for row in candidate.rows if row.box in {'207', '307'}}
        self.assertIn('rc_input', roles)
        self.assertTrue({'rc_output', 'rc_acquisition'} & roles)
        identities = [identity_of(row) for row in candidate.rows]
        self.assertEqual(len(identities), len(set(identities)))
        self._assert_no_writes(period, before)
