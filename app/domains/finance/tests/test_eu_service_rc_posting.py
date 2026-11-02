"""expense_approved EU service reverse-charge posting + tax ledger contract."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from accounting.models import ChartOfAccounts, JournalEntry, JournalEntryLine, VATPeriod
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import build_document_posting_plan, ensure_default_posting_rules
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes, compute_vat_due
from accounting.services.tax_forms.pdv_s.aggregate import aggregate_pdv_s_rows
from accounting.services.tax_projection.rebuild import rebuild_vat_ledger
from accounting.services.tax_projection.switch import set_projection_write_switch
from domains.finance.services.expenses import approve_expense_for_posting
from domains.finance.services.subledger import get_subledger_item_for_source
from expenses.models import Expense, ExpenseCategory
from partners.models import Partner
from settings.models import CompanySettings, VatRegistrationStatus
from tenants.models import Tenant, TenantMembership


class EuServiceReverseChargePostingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        User = get_user_model()
        cls.user = User.objects.create_user(username='eu-rc-post', password='test')

    def _tenant(self, slug, *, status, vat_id=''):
        tenant = Tenant.objects.create(slug=slug, name=slug)
        provision_tenant_chart(tenant)
        ensure_default_posting_rules(tenant)
        CompanySettings.all_objects.create(
            tenant=tenant,
            company_name=slug,
            street='Ulica',
            house_number='1',
            postal_code='22211',
            city='Vodice',
            country='HR',
            company_phone='1',
            company_email=f'{slug}@test.hr',
            vat_number='07155680871',
            vat_id=vat_id,
            vat_registration_status=status,
        )
        TenantMembership.objects.create(user=self.user, tenant=tenant, role='owner')
        set_projection_write_switch(tenant, enabled=True)
        return tenant

    def _booking_partner(self, tenant):
        return Partner.all_objects.create(
            tenant=tenant,
            name='Booking.com B.V.',
            partner_type='supplier',
            status='active',
            country_code='NL',
            country='Netherlands',
            tax_number='',
            vat_number='NL805734958B01',
            address='Oosterdokskade 163',
            city='Amsterdam',
            postal_code='1011 DL',
        )

    def _draft(self, tenant, partner, *, number):
        category = ExpenseCategory.all_objects.get_or_create(
            tenant=tenant,
            name='Provizija',
            defaults={
                'default_account': ChartOfAccounts.all_objects.get(
                    tenant=tenant,
                    account_code='4198',
                ),
            },
        )[0]
        return Expense.all_objects.create(
            tenant=tenant,
            expense_number=number,
            status='draft',
            category=category,
            supplier=partner,
            amount=Decimal('665.85'),
            tax_amount=Decimal('0.00'),
            expense_date=date(2026, 8, 31),
            due_date=date(2026, 9, 16),
            receipt_number='1662290055',
            description='Booking.com commission + service fee',
            created_by=self.user,
        )

    def _je_lines(self, tenant, expense):
        ct = ContentType.objects.get_for_model(Expense)
        je = JournalEntry.all_objects.get(
            tenant=tenant,
            source_content_type=ct,
            source_object_id=expense.pk,
            description__startswith='[expense_approved]',
            status='posted',
        )
        rows = [
            (
                line.account.account_code,
                line.debit_amount,
                line.credit_amount,
            )
            for line in JournalEntryLine.objects.filter(journal_entry=je).select_related('account')
        ]
        return je, rows

    def test_vat_id_approve_books_ap_and_nondeductible_rc(self):
        tenant = self._tenant(
            'eu-rc-vat-id',
            status=VatRegistrationStatus.VAT_ID,
            vat_id='HR07155680871',
        )
        partner = self._booking_partner(tenant)
        expense = self._draft(tenant, partner, number='BK-001')
        plan = build_document_posting_plan(tenant, expense, 'expense_approved')
        pairs = {
            (
                line.debit_account.account_code,
                line.credit_account.account_code.split('-')[0],
                line.amount,
            )
            for line in plan.lines
        }
        self.assertIn(('4198', '2201', Decimal('665.85')), pairs)
        self.assertIn(('4198', '24032', Decimal('166.46')), pairs)

        approve_expense_for_posting(tenant=tenant, expense_id=expense.pk, user=self.user)
        expense.refresh_from_db()
        je, rows = self._je_lines(tenant, expense)
        self.assertTrue(any(code == '4198' and debit == Decimal('665.85') for code, debit, _ in rows))
        self.assertTrue(any(code.startswith('2201') and credit == Decimal('665.85') for code, _, credit in rows))
        self.assertTrue(any(code == '4198' and debit == Decimal('166.46') for code, debit, _ in rows))
        self.assertTrue(any(code == '24032' and credit == Decimal('166.46') for code, _, credit in rows))
        self.assertIn('Račun: 1662290055', je.description)

        item = get_subledger_item_for_source(tenant, expense)
        self.assertIsNotNone(item)
        self.assertEqual(item.direction, 'payable')
        self.assertEqual(item.status, 'open')
        self.assertEqual(item.original_amount, Decimal('665.85'))
        self.assertEqual(item.open_amount, Decimal('665.85'))

        result = rebuild_vat_ledger(tenant, 2026, 8, actor=self.user)
        self.assertTrue(result.ok, result.message)
        period = VATPeriod.all_objects.get(tenant=tenant, year=2026, month=8)
        boxes = aggregate_vat_boxes(period)
        self.assertEqual(boxes['210'].base, Decimal('665.85'))
        self.assertEqual(boxes['210'].vat, Decimal('166.46'))
        self.assertEqual(boxes['306'].base, Decimal('0.00'))
        self.assertEqual(boxes['306'].vat, Decimal('0.00'))
        self.assertEqual(compute_vat_due(boxes), Decimal('166.46'))

        payload = aggregate_pdv_s_rows(period)
        self.assertEqual(len(payload.rows), 1)
        self.assertEqual(payload.rows[0].country_code, 'NL')
        self.assertEqual(payload.rows[0].pdv_id, '805734958B01')
        self.assertEqual(payload.rows[0].services_value, Decimal('665.85'))

    def test_registered_approve_books_deductible_rc_pair(self):
        tenant = self._tenant(
            'eu-rc-reg',
            status=VatRegistrationStatus.REGISTERED,
            vat_id='HR07155680871',
        )
        partner = self._booking_partner(tenant)
        expense = self._draft(tenant, partner, number='BK-002')
        approve_expense_for_posting(tenant=tenant, expense_id=expense.pk, user=self.user)
        _, rows = self._je_lines(tenant, expense)
        self.assertTrue(any(code == '4198' and debit == Decimal('665.85') for code, debit, _ in rows))
        self.assertTrue(any(code == '14032' and debit == Decimal('166.46') for code, debit, _ in rows))
        self.assertTrue(any(code == '24032' and credit == Decimal('166.46') for code, _, credit in rows))
        self.assertFalse(any(code == '4198' and debit == Decimal('166.46') for code, debit, _ in rows))

        result = rebuild_vat_ledger(tenant, 2026, 8, actor=self.user)
        self.assertTrue(result.ok, result.message)
        period = VATPeriod.all_objects.get(tenant=tenant, year=2026, month=8)
        boxes = aggregate_vat_boxes(period)
        self.assertEqual(boxes['210'].base, Decimal('665.85'))
        self.assertEqual(boxes['210'].vat, Decimal('166.46'))
        self.assertEqual(boxes['306'].base, Decimal('665.85'))
        self.assertEqual(boxes['306'].vat, Decimal('166.46'))
        self.assertEqual(compute_vat_due(boxes), Decimal('0.00'))
