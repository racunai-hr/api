"""TD-001: Partner/Supplier unifikacija — migracija, posting, analitika."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import AnalyticAccount, JournalEntry
from accounting.services.analytics import get_or_create_analytic_for_partner
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules, post_document
from accounting.services.rrif_import import import_rrif_chart
from expenses.models import Expense, ExpenseCategory, Supplier
from expenses.services.partner_resolver import resolve_partner
from partners.models import Partner
from tenants.models import Tenant


def _create_supplier_partner(*, tenant, **overrides) -> Partner:
    defaults = {
        'name': 'Dobavljač d.o.o.',
        'tax_number': '98765432109',
        'partner_type': 'supplier',
        'status': 'active',
        'address': 'Ulica 1',
        'city': 'Zagreb',
        'postal_code': '10000',
        'country': 'Hrvatska',
    }
    defaults.update(overrides)
    return Partner.all_objects.create(tenant=tenant, **defaults)


class PartnerResolverTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='resolver-co', name='Resolver Co')

    def test_resolve_creates_supplier_partner(self):
        partner = resolve_partner(tenant=self.tenant, oib='63182808929', name='Hardsoft j.d.o.o.')
        self.assertEqual(partner.partner_type, 'supplier')
        self.assertEqual(partner.tax_number, '63182808929')

    def test_resolve_upgrades_customer_to_both(self):
        existing = Partner.all_objects.create(
            tenant=self.tenant,
            name='Kupac d.o.o.',
            tax_number='12345678901',
            partner_type='customer',
            status='active',
            address='A',
            city='B',
            postal_code='10000',
        )
        partner = resolve_partner(tenant=self.tenant, oib='12345678901', name='Kupac d.o.o.')
        self.assertEqual(partner.pk, existing.pk)
        partner.refresh_from_db()
        self.assertEqual(partner.partner_type, 'both')


class SupplierMigrationDedupeTests(TestCase):
    """Simulira logiku data migracije: OIB match, fallback po imenu."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='migrate-co', name='Migrate Co')

    def test_oib_match_reuses_existing_partner(self):
        partner = Partner.all_objects.create(
            tenant=self.tenant,
            name='Existing Partner',
            tax_number='81793146560',
            partner_type='customer',
            status='active',
            address='',
            city='',
            postal_code='',
        )
        supplier = Supplier.all_objects.create(
            tenant=self.tenant,
            name='Hrvatski Telekom d.d.',
            tax_number='81793146560',
        )
        resolved = resolve_partner(tenant=self.tenant, oib=supplier.tax_number, name=supplier.name)
        self.assertEqual(resolved.pk, partner.pk)
        resolved.refresh_from_db()
        self.assertEqual(resolved.partner_type, 'both')

    def test_distinct_oibs_create_distinct_partners(self):
        Supplier.all_objects.create(tenant=self.tenant, name='A', tax_number='11111111111')
        Supplier.all_objects.create(tenant=self.tenant, name='B', tax_number='22222222222')
        p1 = resolve_partner(tenant=self.tenant, oib='11111111111')
        p2 = resolve_partner(tenant=self.tenant, oib='22222222222')
        self.assertNotEqual(p1.pk, p2.pk)


class UnifiedPartnerAnalyticTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='analytic-co', name='Analytic Co')
        provision_tenant_chart(cls.tenant)
        cls.supplier = _create_supplier_partner(tenant=cls.tenant)

    def test_creates_2201_analytic_for_supplier_partner(self):
        analytic = get_or_create_analytic_for_partner(
            self.tenant,
            self.supplier,
            synthetic_code='2201',
        )
        self.assertEqual(analytic.counterparty_type, 'supplier')
        self.assertEqual(analytic.partner_id, self.supplier.pk)
        self.assertTrue(analytic.account_code.startswith('2201-P'))

    def test_reuses_existing_supplier_analytic(self):
        first = get_or_create_analytic_for_partner(
            self.tenant, self.supplier, synthetic_code='2201',
        )
        second = get_or_create_analytic_for_partner(
            self.tenant, self.supplier, synthetic_code='2201',
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            AnalyticAccount.all_objects.filter(
                tenant=self.tenant,
                partner=self.supplier,
                counterparty_type='supplier',
            ).count(),
            1,
        )


class ExpensePostingUnifiedPartnerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='post-co', name='Post Co')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='post-user', password='test')
        cls.supplier = _create_supplier_partner(tenant=cls.tenant)
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

    def test_expense_approved_uses_partner_analytic(self):
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='EXP-TD001-1',
            status='approved',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('125.00'),
            tax_amount=Decimal('25.00'),
            expense_date=date(2026, 4, 15),
            description='Test trošak',
            created_by=self.user,
        )
        entry = post_document(self.tenant, expense, 'expense_approved', self.user)
        self.assertIsNotNone(entry)
        lines = list(entry.lines.select_related('account'))
        payable_codes = [line.account.account_code for line in lines if line.credit_amount]
        self.assertTrue(any(code.startswith('2201-P') for code in payable_codes))
        self.assertFalse(
            JournalEntry.all_objects.filter(tenant=self.tenant, pk=entry.pk, status='draft').exists()
        )
