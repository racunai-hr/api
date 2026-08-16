"""Business validation rules V001–V005 for Obrazac PDV ↔ PDV-S."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import ChartOfAccounts, JournalEntry, JournalEntryLine, VATPeriod
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_forms.pdv.business_validation import (
    assert_pdv_business_rules,
    validate_v002_pdvs_total_not_exceed_pdv,
)
from accounting.services.tax_forms.pdv.build import build_pdv_payload
from accounting.services.vat import generate_vat_ledger
from expenses.models import Expense, ExpenseCategory
from partners.models import Partner
from settings.models import CompanySettings
from tenants.models import Tenant


class PdvBusinessValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='pdv-v002', name='PDV V002 Co')
        provision_tenant_chart(cls.tenant)
        CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Test d.o.o.',
            vat_number='36619131370',
            street='Ulica',
            house_number='1',
            city='Zagreb',
            postal_code='10000',
        )
        User = get_user_model()
        cls.user = User.objects.create_user(username='pdv-v002', password='test')
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Materijal')
        cls.supplier_hr = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Domestic',
            tax_number='11111111111',
            partner_type='supplier',
            status='active',
            address='',
            city='',
            postal_code='',
            country='Croatia',
        )

    def _account(self, code: str) -> ChartOfAccounts:
        return ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code=code)

    def _seed_may_2026_eu_and_domestic(self):
        Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='MAY-303-1',
            status='paid',
            category=self.category,
            supplier=self.supplier_hr,
            amount=Decimal('39.90'),
            tax_amount=Decimal('7.98'),
            expense_date=date(2026, 5, 15),
            description='Domestic pretporez',
            created_by=self.user,
        )

        acquisition = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202605-0100',
            entry_date=date(2026, 5, 22),
            status='posted',
            description='EU stjecanje Hadžić Račun: 70025237',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=acquisition,
            account=self._account('0373'),
            debit_amount=Decimal('23882.35'),
        )
        JournalEntryLine.objects.create(
            journal_entry=acquisition,
            account=self._account('1000'),
            credit_amount=Decimal('23882.35'),
        )

        reverse_charge = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202605-0101',
            entry_date=date(2026, 5, 22),
            status='posted',
            description='RC EU stjecanje',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=reverse_charge,
            account=self._account('14022'),
            debit_amount=Decimal('5970.59'),
        )
        JournalEntryLine.objects.create(
            journal_entry=reverse_charge,
            account=self._account('24022'),
            credit_amount=Decimal('5970.59'),
        )

        generate_vat_ledger(self.tenant, 2026, 5, replace=True)
        return VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=5)

    def test_v002_pdvs_total_not_exceed_pdv(self):
        period = self._seed_may_2026_eu_and_domestic()
        payload = build_pdv_payload(period)
        pair_207 = payload.field_pair('207')
        pair_307 = payload.field_pair('307')

        self.assertEqual(pair_207.vrijednost, Decimal('23882.35'))
        self.assertEqual(pair_207.porez, Decimal('5970.59'))
        self.assertEqual(pair_307.vrijednost, Decimal('23882.35'))
        self.assertEqual(pair_307.porez, Decimal('5970.59'))
        for code in ('610', '611', '612', '613', '614', '615'):
            self.assertEqual(payload.field_scalar(code), Decimal('0.00'))
        self.assertEqual(payload.field_scalar('400'), Decimal('-7.98'))

        self.assertIsNone(validate_v002_pdvs_total_not_exceed_pdv(period))
        assert_pdv_business_rules(period)
