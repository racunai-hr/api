"""Gate E0: adopt_legacy_projection and business multiset parity."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import (
    VATLedgerEntry,
    VATLedgerOrigin,
    VATPeriod,
    VATProjectionRunStatus,
)
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_projection.adopt import (
    AMBIGUOUS_LEDGER_ORIGINS,
    LEGACY_CANDIDATE_MISMATCH,
    adopt_legacy_projection,
)
from accounting.services.tax_projection.apply import apply_vat_projection
from accounting.services.tax_projection.business import (
    business_multiset_from_candidate,
    business_multiset_from_ledger,
)
from accounting.services.tax_projection.contracts import VatProjectionStatus
from accounting.services.tax_projection.prepare import prepare_vat_projection
from accounting.services.tax_projection.rebuild import RebuildOutcome, rebuild_vat_ledger
from accounting.services.tax_projection.switch import (
    VAT_PROJECTION_WRITE_KEY,
    set_projection_write_switch_locked,
)
from accounting.services.tax_shadow.runner import ledger_fingerprint
from accounting.services.vat import generate_vat_ledger
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from settings.models import SystemParameter
from tenants.models import Tenant


class AdoptLegacyProjectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='proj-adopt', name='Adopt Co')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='proj-adopt', password='test')
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

    def _period(self, *, status='open'):
        period, _ = VATPeriod.all_objects.get_or_create(
            tenant=self.tenant, year=2026, month=4, defaults={'status': status},
        )
        if period.status != status:
            period.status = status
            period.save(update_fields=['status'])
        return period

    def _sent_invoice(self, *, day=5, price=Decimal('100.00'), rate=Decimal('25.00')):
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

    def _manual(self, period, *, box='611', vat=Decimal('25.00')):
        return VATLedgerEntry.all_objects.create(
            tenant=self.tenant,
            vat_period=period,
            ledger_type=VATLedgerEntry.LEDGER_U_RA,
            entry_date=date(2026, 4, 15),
            document_number='MAN-611',
            partner_name='Ručno',
            partner_oib='',
            base_amount=Decimal('0.00'),
            vat_rate=Decimal('0.00'),
            vat_amount=vat,
            vat_box=box,
            is_manual=True,
            origin=VATLedgerOrigin.MANUAL,
        )

    def test_adopt_matching_legacy_replaces_with_engine_preserves_manual(self):
        self._sent_invoice()
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = self._period()
        manual = self._manual(period)
        # regenerate 610 after manual
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        manual.refresh_from_db()
        self.assertEqual(manual.origin, VATLedgerOrigin.MANUAL)

        legacy_count = VATLedgerEntry.all_objects.filter(
            vat_period=period, origin=VATLedgerOrigin.LEGACY,
        ).count()
        self.assertGreater(legacy_count, 0)

        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.READY)
        self.assertEqual(
            business_multiset_from_candidate(candidate),
            business_multiset_from_ledger(period, origins=(VATLedgerOrigin.LEGACY,)),
        )

        run = adopt_legacy_projection(period, candidate, self.user)
        self.assertEqual(run.status, VATProjectionRunStatus.APPLIED)
        self.assertEqual(
            VATLedgerEntry.all_objects.filter(
                vat_period=period, origin=VATLedgerOrigin.LEGACY, is_manual=False,
            ).count(),
            0,
        )
        self.assertTrue(
            VATLedgerEntry.all_objects.filter(
                vat_period=period, origin=VATLedgerOrigin.ENGINE,
            ).exists()
        )
        self.assertTrue(VATLedgerEntry.all_objects.filter(pk=manual.pk).exists())
        self.assertEqual(
            business_multiset_from_candidate(candidate),
            business_multiset_from_ledger(period, origins=(VATLedgerOrigin.ENGINE,)),
        )

        # Second apply must not resurrect legacy or duplicate
        second = apply_vat_projection(period, prepare_vat_projection(period), self.user)
        self.assertEqual(second.status, VATProjectionRunStatus.APPLIED)
        self.assertEqual(
            VATLedgerEntry.all_objects.filter(
                vat_period=period, origin=VATLedgerOrigin.LEGACY, is_manual=False,
            ).count(),
            0,
        )

    def test_adopt_mismatch_zero_ledger_mutation(self):
        self._sent_invoice()
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = self._period()
        VATLedgerEntry.all_objects.create(
            tenant=self.tenant,
            vat_period=period,
            ledger_type=VATLedgerEntry.LEDGER_I_RA,
            entry_date=date(2026, 4, 1),
            document_number='DRIFT',
            partner_name='X',
            partner_oib='',
            base_amount=Decimal('999.00'),
            vat_rate=Decimal('25.00'),
            vat_amount=Decimal('249.75'),
            vat_box='203',
            is_manual=False,
            origin=VATLedgerOrigin.LEGACY,
        )
        before = (
            VATLedgerEntry.all_objects.filter(vat_period=period).count(),
            ledger_fingerprint(period),
            list(
                VATLedgerEntry.all_objects.filter(vat_period=period)
                .order_by('pk')
                .values_list('pk', 'origin', 'base_amount')
            ),
        )
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.READY)
        run = adopt_legacy_projection(period, candidate, self.user)
        self.assertEqual(run.status, VATProjectionRunStatus.REJECTED)
        self.assertEqual(run.rejection_code, LEGACY_CANDIDATE_MISMATCH)
        after = (
            VATLedgerEntry.all_objects.filter(vat_period=period).count(),
            ledger_fingerprint(period),
            list(
                VATLedgerEntry.all_objects.filter(vat_period=period)
                .order_by('pk')
                .values_list('pk', 'origin', 'vat_box', 'base_amount')
            ),
        )
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1], before[1])
        self.assertEqual(
            list(
                VATLedgerEntry.all_objects.filter(vat_period=period)
                .order_by('pk')
                .values_list('pk', 'origin', 'base_amount')
            ),
            before[2],
        )

    def test_adopt_rejects_closed(self):
        self._sent_invoice(day=6)
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = self._period(status='closed')
        candidate = prepare_vat_projection(period)
        before_fp = ledger_fingerprint(period)
        run = adopt_legacy_projection(period, candidate, self.user)
        self.assertEqual(run.rejection_code, 'VAT_PERIOD_NOT_WRITABLE')
        self.assertEqual(ledger_fingerprint(period), before_fp)

    def test_rebuild_on_path_uses_adopt_when_legacy_present(self):
        self._sent_invoice(day=7)
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        SystemParameter.all_objects.update_or_create(
            tenant=self.tenant,
            key=VAT_PROJECTION_WRITE_KEY,
            defaults={'value': 'true', 'data_type': 'boolean'},
        )
        result = rebuild_vat_ledger(self.tenant, 2026, 4, actor=self.user, replace=True)
        self.assertEqual(result.outcome, RebuildOutcome.APPLIED)
        self.assertEqual(
            VATLedgerEntry.all_objects.filter(
                vat_period_id=result.period_id,
                origin=VATLedgerOrigin.LEGACY,
                is_manual=False,
            ).count(),
            0,
        )
        engine = VATLedgerEntry.all_objects.filter(
            vat_period_id=result.period_id, origin=VATLedgerOrigin.ENGINE,
        ).count()
        self.assertGreater(engine, 0)

        # Idempotent second rebuild
        again = rebuild_vat_ledger(self.tenant, 2026, 4, actor=self.user, replace=True)
        self.assertEqual(again.outcome, RebuildOutcome.APPLIED)
        self.assertEqual(
            VATLedgerEntry.all_objects.filter(
                vat_period_id=result.period_id, origin=VATLedgerOrigin.ENGINE,
            ).count(),
            engine,
        )

    def test_ambiguous_legacy_plus_engine_rejects(self):
        self._sent_invoice(day=8)
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = self._period()
        candidate = prepare_vat_projection(period)
        apply_vat_projection(period, candidate, self.user)
        # Force ambiguous state: leave engine and re-add a legacy row
        VATLedgerEntry.all_objects.create(
            tenant=self.tenant,
            vat_period=period,
            ledger_type=VATLedgerEntry.LEDGER_I_RA,
            entry_date=date(2026, 4, 1),
            document_number='LEGACY-EXTRA',
            partner_name='X',
            partner_oib='',
            base_amount=Decimal('1.00'),
            vat_rate=Decimal('25.00'),
            vat_amount=Decimal('0.25'),
            vat_box='203',
            is_manual=False,
            origin=VATLedgerOrigin.LEGACY,
        )
        set_projection_write_switch_locked(self.tenant, enabled=True)
        result = rebuild_vat_ledger(self.tenant, 2026, 4, actor=self.user)
        self.assertEqual(result.outcome, RebuildOutcome.REJECTED)
        self.assertEqual(result.rejection_code, AMBIGUOUS_LEDGER_ORIGINS)
