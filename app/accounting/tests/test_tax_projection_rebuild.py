"""Gate D: rebuild facade, switch, lifecycle, admin ledger guards."""

from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import RequestFactory, TestCase, TransactionTestCase

from accounting.admin import VATLedgerEntryAdmin
from accounting.models import (
    VATLedgerEntry,
    VATLedgerOrigin,
    VATPeriod,
    VATProjectionRun,
    VATProjectionRunStatus,
)
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_projection.rebuild import (
    RebuildOutcome,
    VATPeriodNotWritable,
    VATProjectionWriteEnabled,
    rebuild_vat_ledger,
)
from accounting.services.tax_projection.switch import (
    VAT_PROJECTION_WRITE_KEY,
    SwitchState,
    VATProjectionSwitchInvalid,
    parse_switch_value,
    read_projection_write_switch,
    set_projection_write_switch_locked,
)
from accounting.services.vat import generate_vat_ledger
from expenses.models import Expense, ExpenseCategory
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from settings.models import SystemParameter
from tenants.models import Tenant


class SwitchParseTests(TestCase):
    def test_parse_tokens(self):
        self.assertEqual(parse_switch_value(None), SwitchState.OFF)
        self.assertEqual(parse_switch_value(''), SwitchState.OFF)
        self.assertEqual(parse_switch_value('TRUE'), SwitchState.ON)
        self.assertEqual(parse_switch_value('0'), SwitchState.OFF)
        self.assertEqual(parse_switch_value('maybe'), SwitchState.INVALID)


class ProjectionRebuildTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='proj-rebuild', name='Rebuild Co')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='proj-rebuild', password='test')
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

    def _period(self, *, year=2026, month=4, status='open'):
        period, _ = VATPeriod.all_objects.get_or_create(
            tenant=self.tenant, year=year, month=month, defaults={'status': status},
        )
        if period.status != status:
            period.status = status
            period.save(update_fields=['status'])
        return period

    def _set_switch(self, value: str):
        SystemParameter.all_objects.update_or_create(
            tenant=self.tenant,
            key=VAT_PROJECTION_WRITE_KEY,
            defaults={'value': value, 'data_type': 'boolean'},
        )

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

    def _generic_expense(self):
        return Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='EXP-303',
            status='approved',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('125.00'),
            tax_amount=Decimal('25.00'),
            currency='EUR',
            expense_date=date(2026, 4, 8),
            description='Generic pretporez',
            created_by=self.user,
        )

    def test_missing_param_is_off_legacy_path(self):
        self._sent_invoice()
        self.assertEqual(read_projection_write_switch(self.tenant), SwitchState.OFF)
        called = {'legacy': False}
        real = generate_vat_ledger

        def tracking(*args, **kwargs):
            called['legacy'] = True
            return real(*args, **kwargs)

        with patch('accounting.services.vat.generate_vat_ledger', side_effect=tracking):
            result = rebuild_vat_ledger(self.tenant, 2026, 4, actor=self.user, replace=True)
        self.assertTrue(called['legacy'])
        self.assertEqual(result.outcome, RebuildOutcome.LEGACY)
        self.assertGreater(VATLedgerEntry.all_objects.filter(tenant=self.tenant).count(), 0)

    def test_switch_on_never_calls_legacy_generator(self):
        self._sent_invoice()
        self._set_switch('true')
        with patch('accounting.services.vat.generate_vat_ledger') as legacy:
            result = rebuild_vat_ledger(self.tenant, 2026, 4, actor=self.user, replace=True)
        legacy.assert_not_called()
        self.assertEqual(result.outcome, RebuildOutcome.APPLIED)

    def test_switch_on_generic_303_rejects_without_apply_or_legacy(self):
        self._generic_expense()
        self._set_switch('on')
        period = self._period()
        before = list(
            VATLedgerEntry.all_objects.filter(vat_period=period).values_list('pk', 'origin')
        )
        with patch('accounting.services.vat.generate_vat_ledger') as legacy:
            with patch(
                'accounting.services.tax_projection.rebuild.apply_vat_projection'
            ) as apply_mock:
                result = rebuild_vat_ledger(self.tenant, 2026, 4, actor=self.user)
        legacy.assert_not_called()
        apply_mock.assert_not_called()
        self.assertEqual(result.outcome, RebuildOutcome.REJECTED)
        self.assertEqual(result.rejection_code, 'EXPENSE_GENERIC_303_REMOVED')
        after = list(
            VATLedgerEntry.all_objects.filter(vat_period=period).values_list('pk', 'origin')
        )
        self.assertEqual(before, after)

    def test_invalid_switch_no_write(self):
        self._sent_invoice()
        self._set_switch('banana')
        with patch('accounting.services.vat.generate_vat_ledger') as legacy:
            with patch(
                'accounting.services.tax_projection.rebuild.apply_vat_projection'
            ) as apply_mock:
                result = rebuild_vat_ledger(self.tenant, 2026, 4, actor=self.user)
        legacy.assert_not_called()
        apply_mock.assert_not_called()
        self.assertEqual(result.outcome, RebuildOutcome.SWITCH_INVALID)
        self.assertEqual(result.exit_code(), 2)

    def test_facade_closed_period_zero_mutation(self):
        self._sent_invoice()
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = self._period(status='closed')
        count = VATLedgerEntry.all_objects.filter(vat_period=period).count()
        result = rebuild_vat_ledger(self.tenant, 2026, 4, actor=self.user, replace=True)
        self.assertEqual(result.outcome, RebuildOutcome.NOT_WRITABLE)
        self.assertEqual(VATLedgerEntry.all_objects.filter(vat_period=period).count(), count)

    def test_facade_submitted_period_zero_mutation(self):
        self._sent_invoice(day=6)
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = self._period(status='submitted')
        count = VATLedgerEntry.all_objects.filter(vat_period=period).count()
        result = rebuild_vat_ledger(self.tenant, 2026, 4, actor=self.user)
        self.assertEqual(result.outcome, RebuildOutcome.NOT_WRITABLE)
        self.assertEqual(VATLedgerEntry.all_objects.filter(vat_period=period).count(), count)

    def test_direct_generator_rejects_closed(self):
        period = self._period(status='closed')
        with self.assertRaises(VATPeriodNotWritable):
            generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        self.assertEqual(VATLedgerEntry.all_objects.filter(vat_period=period).count(), 0)

    def test_direct_generator_rejects_when_switch_on(self):
        self._set_switch('true')
        with self.assertRaises(VATProjectionWriteEnabled):
            generate_vat_ledger(self.tenant, 2026, 4, replace=True)

    def test_direct_generator_rejects_invalid_switch(self):
        self._set_switch('wat')
        with self.assertRaises(VATProjectionSwitchInvalid):
            generate_vat_ledger(self.tenant, 2026, 4, replace=True)

    def test_enable_switch_blocked_by_prepared_run(self):
        period = self._period()
        VATProjectionRun.all_objects.create(
            tenant=self.tenant,
            vat_period=period,
            status=VATProjectionRunStatus.PREPARED,
            input_fingerprint='a' * 64,
            output_fingerprint='b' * 64,
            engine_version=1,
            mapping_version=1,
        )
        with self.assertRaises(ValueError) as cm:
            set_projection_write_switch_locked(self.tenant, enabled=True)
        self.assertIn('PREPARED', str(cm.exception))

    def test_enable_switch_ok_without_prepared(self):
        set_projection_write_switch_locked(self.tenant, enabled=True)
        self.assertEqual(read_projection_write_switch(self.tenant), SwitchState.ON)
        set_projection_write_switch_locked(self.tenant, enabled=False)
        self.assertEqual(read_projection_write_switch(self.tenant), SwitchState.OFF)

    def test_verify_pdv_fails_fast_on_rejected_rebuild(self):
        self._generic_expense()
        self._set_switch('true')
        self._period()
        with self.assertRaises(CommandError) as cm:
            call_command(
                'verify_pdv_period',
                tenant='proj-rebuild',
                year=2026,
                month=4,
                regenerate_ledger=True,
                stdout=StringIO(),
                stderr=StringIO(),
            )
        self.assertEqual(cm.exception.returncode, 1)

    def test_admin_forces_manual_origin(self):
        period = self._period()
        admin = VATLedgerEntryAdmin(VATLedgerEntry, AdminSite())
        request = RequestFactory().post('/')
        request.user = self.user
        entry = VATLedgerEntry(
            tenant=self.tenant,
            vat_period=period,
            ledger_type=VATLedgerEntry.LEDGER_I_RA,
            entry_date=date(2026, 4, 1),
            document_number='MAN-1',
            partner_name='X',
            base_amount=Decimal('100.00'),
            vat_rate=Decimal('25.00'),
            vat_amount=Decimal('25.00'),
            origin=VATLedgerOrigin.LEGACY,
            is_manual=False,
        )
        admin.save_model(request, entry, form=None, change=False)
        entry.refresh_from_db()
        self.assertEqual(entry.origin, VATLedgerOrigin.MANUAL)
        self.assertTrue(entry.is_manual)

    def test_admin_rejects_engine_change_and_submitted_period(self):
        period = self._period()
        engine = VATLedgerEntry.all_objects.create(
            tenant=self.tenant,
            vat_period=period,
            ledger_type=VATLedgerEntry.LEDGER_I_RA,
            entry_date=date(2026, 4, 1),
            document_number='ENG-1',
            partner_name='X',
            base_amount=Decimal('100.00'),
            vat_rate=Decimal('25.00'),
            vat_amount=Decimal('25.00'),
            origin=VATLedgerOrigin.ENGINE,
            is_manual=False,
        )
        admin = VATLedgerEntryAdmin(VATLedgerEntry, AdminSite())
        request = RequestFactory().post('/')
        request.user = self.user
        self.assertFalse(admin.has_change_permission(request, engine))
        self.assertFalse(admin.has_delete_permission(request, engine))

        period.status = 'submitted'
        period.save(update_fields=['status'])
        manual = VATLedgerEntry(
            tenant=self.tenant,
            vat_period=period,
            ledger_type=VATLedgerEntry.LEDGER_I_RA,
            entry_date=date(2026, 4, 2),
            document_number='MAN-SUB',
            partner_name='Y',
            base_amount=Decimal('10.00'),
            vat_rate=Decimal('25.00'),
            vat_amount=Decimal('2.50'),
        )
        with self.assertRaises(ValidationError):
            admin.save_model(request, manual, form=None, change=False)


class ProjectionRebuildLockTests(TransactionTestCase):
    def setUp(self):
        import_rrif_chart(clear=True)
        self.tenant = Tenant.objects.create(slug='proj-rebuild-tx', name='Rebuild TX')
        provision_tenant_chart(self.tenant)

    def test_lock_helper_noops_when_switch_off(self):
        from accounting.services.tax_projection.locks import lock_open_vat_period_for_source_mutation

        VATPeriod.all_objects.create(tenant=self.tenant, year=2026, month=4, status='open')
        self.assertIsNone(lock_open_vat_period_for_source_mutation(self.tenant, date(2026, 4, 5)))

    def test_lock_helper_locks_open_period_when_switch_on(self):
        from accounting.services.tax_projection.locks import lock_open_vat_period_for_source_mutation

        SystemParameter.all_objects.create(
            tenant=self.tenant,
            key=VAT_PROJECTION_WRITE_KEY,
            value='true',
            data_type='boolean',
        )
        period = VATPeriod.all_objects.create(
            tenant=self.tenant, year=2026, month=4, status='open',
        )
        with transaction.atomic():
            locked = lock_open_vat_period_for_source_mutation(self.tenant, date(2026, 4, 5))
            self.assertIsNotNone(locked)
            self.assertEqual(locked.pk, period.pk)
