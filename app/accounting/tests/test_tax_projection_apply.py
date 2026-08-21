"""Atomic VAT projection apply (Gate C)."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase

from accounting.models import (
    VATLedgerEntry,
    VATLedgerOrigin,
    VATPeriod,
    VATProjectionRun,
    VATProjectionRunStatus,
)
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_forms.pdv.mapping import PDV_MAPPING_VERSION
from accounting.services.tax_projection.apply import PostWriteFingerprintMismatch, apply_vat_projection
from accounting.services.tax_projection.contracts import PROJECTION_ENGINE_VERSION, VatProjectionStatus
from accounting.services.tax_projection.prepare import prepare_vat_projection
from accounting.services.tax_shadow.runner import ledger_fingerprint
from dataclasses import replace
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from tenants.models import Tenant


class ProjectionApplyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='proj-apply', name='Apply Co')
        cls.other_tenant = Tenant.objects.create(slug='proj-apply-other', name='Other Co')
        provision_tenant_chart(cls.tenant)
        provision_tenant_chart(cls.other_tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='proj-apply', password='test')
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

    def _period(self, *, year=2026, month=4, status='open', tenant=None):
        tenant = tenant or self.tenant
        period, _ = VATPeriod.all_objects.get_or_create(
            tenant=tenant, year=year, month=month, defaults={'status': status},
        )
        if period.status != status:
            period.status = status
            period.save(update_fields=['status'])
        return period

    def _sent_invoice(self, *, price=Decimal('100.00'), rate=Decimal('25.00'), day=5):
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
            item_name='Usluga',
            quantity=1,
            unit_price=price,
            tax_rate=rate,
        )
        return invoice

    def _manual(self, period, *, box, base=Decimal('0.00'), vat=Decimal('0.00'), number='MAN'):
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

    def _legacy(self, period):
        return VATLedgerEntry.all_objects.create(
            tenant=self.tenant,
            vat_period=period,
            ledger_type=VATLedgerEntry.LEDGER_I_RA,
            entry_date=date(2026, 4, 1),
            document_number='LEGACY-1',
            partner_name='Legacy',
            partner_oib='',
            base_amount=Decimal('10.00'),
            vat_rate=Decimal('25.00'),
            vat_amount=Decimal('2.50'),
            vat_box='203',
            is_manual=False,
            origin=VATLedgerOrigin.LEGACY,
        )

    def _guard(self, period):
        return (
            VATLedgerEntry.all_objects.filter(vat_period=period).count(),
            ledger_fingerprint(period),
            list(
                VATLedgerEntry.all_objects.filter(vat_period=period)
                .order_by('pk')
                .values_list('pk', 'origin', 'vat_box', 'base_amount')
            ),
        )

    def test_closed_rejects_without_ledger_mutation(self):
        self._sent_invoice()
        period = self._period(status='closed')
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.READY)
        before = self._guard(period)
        run = apply_vat_projection(period, candidate, self.user)
        self.assertEqual(run.status, VATProjectionRunStatus.REJECTED)
        self.assertEqual(run.rejection_code, 'VAT_PERIOD_NOT_WRITABLE')
        self.assertEqual(self._guard(period)[:2], before[:2])
        self.assertEqual(
            VATLedgerEntry.all_objects.filter(vat_period=period, origin=VATLedgerOrigin.ENGINE).count(),
            0,
        )

    def test_submitted_rejects_without_ledger_mutation(self):
        self._sent_invoice(day=6)
        period = self._period(status='submitted')
        candidate = prepare_vat_projection(period)
        before = self._guard(period)
        run = apply_vat_projection(period, candidate, self.user)
        self.assertEqual(run.rejection_code, 'VAT_PERIOD_NOT_WRITABLE')
        self.assertEqual(self._guard(period)[:2], before[:2])

    def test_ready_open_writes_engine_preserves_manual_and_legacy(self):
        self._sent_invoice()
        period = self._period(status='open')
        manual = self._manual(period, box='611', vat=Decimal('25.00'), number='MAN-611')
        legacy = self._legacy(period)
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.READY)
        run = apply_vat_projection(period, candidate, self.user)
        self.assertEqual(run.status, VATProjectionRunStatus.APPLIED)
        self.assertTrue(
            VATLedgerEntry.all_objects.filter(vat_period=period, origin=VATLedgerOrigin.ENGINE).exists()
        )
        self.assertTrue(VATLedgerEntry.all_objects.filter(pk=manual.pk, origin=VATLedgerOrigin.MANUAL).exists())
        self.assertTrue(VATLedgerEntry.all_objects.filter(pk=legacy.pk, origin=VATLedgerOrigin.LEGACY).exists())
        engine = VATLedgerEntry.all_objects.filter(vat_period=period, origin=VATLedgerOrigin.ENGINE)
        self.assertTrue(all(row.projection_run_id == run.pk for row in engine))

    def test_writable_false_does_not_block_open_period(self):
        self._sent_invoice(day=7)
        period = self._period(status='open')
        candidate = prepare_vat_projection(period)
        candidate = replace(candidate, writable=False)
        run = apply_vat_projection(period, candidate, self.user)
        self.assertEqual(run.status, VATProjectionRunStatus.APPLIED)

    def test_wrong_period_or_tenant_invalid_candidate(self):
        self._sent_invoice(day=8)
        period = self._period(status='open')
        other = self._period(year=2026, month=5, status='open', tenant=self.other_tenant)
        candidate = prepare_vat_projection(period)
        before = self._guard(other)
        run = apply_vat_projection(other, candidate, self.user)
        self.assertEqual(run.status, VATProjectionRunStatus.REJECTED)
        self.assertEqual(run.rejection_code, 'INVALID_CANDIDATE')
        self.assertEqual(self._guard(other)[:2], before[:2])

    def test_tampered_rows_with_stale_output_fingerprint_rejected(self):
        self._sent_invoice(day=9)
        period = self._period(status='open')
        candidate = prepare_vat_projection(period)
        bad_rows = tuple(
            replace(row, base_amount=row.base_amount + Decimal('1.00')) if row.box == '203' else row
            for row in candidate.rows
        )
        candidate = replace(candidate, rows=bad_rows)
        before = self._guard(period)
        run = apply_vat_projection(period, candidate, self.user)
        self.assertEqual(run.rejection_code, 'INVALID_CANDIDATE')
        self.assertEqual(self._guard(period)[:2], before[:2])

    def test_unsupported_mapping_version_rejected(self):
        self._sent_invoice(day=10)
        period = self._period(status='open')
        candidate = prepare_vat_projection(period)
        candidate = replace(candidate, mapping_version=PDV_MAPPING_VERSION + 99)
        run = apply_vat_projection(period, candidate, self.user)
        self.assertEqual(run.rejection_code, 'INVALID_CANDIDATE')

    def test_unsupported_engine_version_rejected(self):
        self._sent_invoice(day=11)
        period = self._period(status='open')
        candidate = prepare_vat_projection(period)
        candidate = replace(candidate, engine_version=PROJECTION_ENGINE_VERSION + 5)
        run = apply_vat_projection(period, candidate, self.user)
        self.assertEqual(run.rejection_code, 'INVALID_CANDIDATE')

    def test_stale_candidate_after_source_change(self):
        invoice = self._sent_invoice(day=12)
        period = self._period(status='open')
        candidate = prepare_vat_projection(period)
        item = invoice.items.get()
        item.unit_price = Decimal('200.00')
        item.save(update_fields=['unit_price'])
        before = self._guard(period)
        run = apply_vat_projection(period, candidate, self.user)
        self.assertEqual(run.status, VATProjectionRunStatus.STALE)
        self.assertEqual(run.rejection_code, 'STALE_PROJECTION')
        self.assertEqual(self._guard(period)[:2], before[:2])

    def test_fresh_prepare_after_source_change_replaces_only_engine(self):
        invoice = self._sent_invoice(day=13, price=Decimal('100.00'))
        period = self._period(status='open')
        legacy = self._legacy(period)
        first = prepare_vat_projection(period)
        apply_vat_projection(period, first, self.user)
        item = invoice.items.get()
        item.unit_price = Decimal('150.00')
        item.save(update_fields=['unit_price'])
        invoice.subtotal = Decimal('150.00')
        invoice.tax_amount = Decimal('37.50')
        invoice.total_amount = Decimal('187.50')
        invoice.save(update_fields=['subtotal', 'tax_amount', 'total_amount'])
        second = prepare_vat_projection(period)
        run = apply_vat_projection(period, second, self.user)
        self.assertEqual(run.status, VATProjectionRunStatus.APPLIED)
        self.assertTrue(VATLedgerEntry.all_objects.filter(pk=legacy.pk).exists())
        engine_203 = VATLedgerEntry.all_objects.get(
            vat_period=period, origin=VATLedgerOrigin.ENGINE, vat_box='203',
        )
        self.assertEqual(engine_203.base_amount, Decimal('150.00'))

    def test_manual_viii1_change_between_prepare_and_apply_is_stale(self):
        period = self._period(status='open')
        manual = self._manual(period, box='611', vat=Decimal('25.00'), number='MAN-611')
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.READY)
        manual.vat_amount = Decimal('40.00')
        manual.save(update_fields=['vat_amount'])
        before = self._guard(period)
        run = apply_vat_projection(period, candidate, self.user)
        self.assertEqual(run.status, VATProjectionRunStatus.STALE)
        self.assertEqual(self._guard(period)[:2], before[:2])

    def test_idempotent_second_apply_returns_same_run(self):
        self._sent_invoice(day=14)
        period = self._period(status='open')
        candidate = prepare_vat_projection(period)
        first = apply_vat_projection(period, candidate, self.user)
        engine_count = VATLedgerEntry.all_objects.filter(
            vat_period=period, origin=VATLedgerOrigin.ENGINE,
        ).count()
        second = apply_vat_projection(period, candidate, self.user)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.status, VATProjectionRunStatus.APPLIED)
        self.assertEqual(
            VATLedgerEntry.all_objects.filter(vat_period=period, origin=VATLedgerOrigin.ENGINE).count(),
            engine_count,
        )
        self.assertEqual(
            VATProjectionRun.all_objects.filter(
                vat_period=period, status=VATProjectionRunStatus.APPLIED,
            ).count(),
            1,
        )

    def test_damaged_engine_ledger_is_not_idempotent_noop(self):
        self._sent_invoice(day=15)
        period = self._period(status='open')
        candidate = prepare_vat_projection(period)
        first = apply_vat_projection(period, candidate, self.user)
        engine = VATLedgerEntry.all_objects.filter(
            vat_period=period, origin=VATLedgerOrigin.ENGINE,
        ).first()
        engine.base_amount = engine.base_amount + Decimal('1.00')
        engine.save(update_fields=['base_amount'])
        second = apply_vat_projection(period, candidate, self.user)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.status, VATProjectionRunStatus.APPLIED)
        writable = [row for row in candidate.rows if row.source_kind != 'manual_ledger']
        self.assertEqual(
            VATLedgerEntry.all_objects.filter(vat_period=period, origin=VATLedgerOrigin.ENGINE).count(),
            len(writable),
        )
        self.assertFalse(VATLedgerEntry.all_objects.filter(pk=engine.pk).exists())


    def test_exception_after_prepared_rolls_back_ledger_and_run(self):
        self._sent_invoice(day=16)
        period = self._period(status='open')
        candidate = prepare_vat_projection(period)
        before_engine = VATLedgerEntry.all_objects.filter(
            vat_period=period, origin=VATLedgerOrigin.ENGINE,
        ).count()

        real_create = VATProjectionRun.all_objects.create

        def create_side_effect(**kwargs):
            obj = real_create(**kwargs)
            if kwargs.get('status') == VATProjectionRunStatus.PREPARED:
                raise RuntimeError('boom-after-prepared')
            return obj

        with patch(
            'accounting.services.tax_projection.apply.VATProjectionRun.all_objects.create',
            side_effect=create_side_effect,
        ):
            with self.assertRaises(RuntimeError):
                apply_vat_projection(period, candidate, self.user)

        self.assertEqual(
            VATLedgerEntry.all_objects.filter(vat_period=period, origin=VATLedgerOrigin.ENGINE).count(),
            before_engine,
        )
        self.assertFalse(
            VATProjectionRun.all_objects.filter(
                vat_period=period, status=VATProjectionRunStatus.PREPARED,
            ).exists()
        )
        self.assertTrue(
            VATProjectionRun.all_objects.filter(
                vat_period=period, status=VATProjectionRunStatus.FAILED, rejection_code='APPLY_FAILED',
            ).exists()
        )

    def test_post_write_fingerprint_mismatch_full_rollback(self):
        self._sent_invoice(day=17)
        period = self._period(status='open')
        candidate = prepare_vat_projection(period)
        before = self._guard(period)
        with patch(
            'accounting.services.tax_projection.apply.engine_effect_fingerprint_from_ledger',
            return_value='0' * 64,
        ):
            with self.assertRaises(PostWriteFingerprintMismatch):
                apply_vat_projection(period, candidate, self.user)
        self.assertEqual(self._guard(period)[:2], before[:2])
        self.assertFalse(
            VATLedgerEntry.all_objects.filter(vat_period=period, origin=VATLedgerOrigin.ENGINE).exists()
        )
        self.assertTrue(
            VATProjectionRun.all_objects.filter(
                vat_period=period,
                status=VATProjectionRunStatus.FAILED,
                rejection_code='POST_WRITE_FINGERPRINT_MISMATCH',
            ).exists()
        )

    def test_exception_after_delete_rolls_back_ledger(self):
        self._sent_invoice(day=18)
        period = self._period(status='open')
        candidate = prepare_vat_projection(period)
        apply_vat_projection(period, candidate, self.user)
        engine = VATLedgerEntry.all_objects.filter(
            vat_period=period, origin=VATLedgerOrigin.ENGINE,
        ).first()
        engine.base_amount = engine.base_amount + Decimal('1.00')
        engine.save(update_fields=['base_amount'])
        before_fp = ledger_fingerprint(period)

        with patch(
            'accounting.services.tax_projection.apply._insert_engine_rows',
            side_effect=RuntimeError('boom-after-delete'),
        ):
            with self.assertRaises(RuntimeError):
                apply_vat_projection(period, candidate, self.user)
        self.assertEqual(ledger_fingerprint(period), before_fp)
        self.assertTrue(VATLedgerEntry.all_objects.filter(pk=engine.pk).exists())


class ProjectionApplyConcurrencyTests(TransactionTestCase):
    def setUp(self):
        import_rrif_chart(clear=True)
        self.tenant = Tenant.objects.create(slug='proj-apply-conc', name='Apply Conc')
        provision_tenant_chart(self.tenant)
        User = get_user_model()
        self.user = User.objects.create_user(username='proj-apply-conc', password='test')
        self.partner = Partner.all_objects.create(
            tenant=self.tenant,
            name='Kupac',
            tax_number='12345678901',
            partner_type='customer',
            status='active',
            address='',
            city='',
            postal_code='',
        )
        invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            company_to=self.partner,
            issue_date=date(2026, 4, 20),
            due_date=date(2026, 4, 30),
            status='sent',
            subtotal=Decimal('100.00'),
            tax_amount=Decimal('25.00'),
            total_amount=Decimal('125.00'),
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name='Usluga',
            quantity=1,
            unit_price=Decimal('100.00'),
            tax_rate=Decimal('25.00'),
        )
        self.period = VATPeriod.all_objects.create(
            tenant=self.tenant, year=2026, month=4, status='open',
        )
        self.candidate = prepare_vat_projection(self.period)

    def test_parallel_applies_serialize_without_duplicate_engine_rows(self):
        import threading

        results = []
        errors = []

        def worker():
            try:
                results.append(apply_vat_projection(self.period, self.candidate, self.user))
            except Exception as exc:  # noqa: BLE001 — collect for assertion
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual({run.pk for run in results}, {results[0].pk})
        self.assertEqual(
            VATProjectionRun.all_objects.filter(
                vat_period=self.period, status=VATProjectionRunStatus.APPLIED,
            ).count(),
            1,
        )
        writable = [row for row in self.candidate.rows if row.source_kind != 'manual_ledger']
        self.assertEqual(
            VATLedgerEntry.all_objects.filter(
                vat_period=self.period, origin=VATLedgerOrigin.ENGINE,
            ).count(),
            len(writable),
        )
