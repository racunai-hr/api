"""OSS/IOSS ledger generation, payload contract, and ZP isolation."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import ChartOfAccounts, JournalEntry, JournalEntryLine, VATPeriod
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes, compute_vat_due
from accounting.services.tax_forms.pdv.build import build_pdv_payload
from accounting.services.tax_forms.pdv.mapping import PDV_MAPPING_VERSION
from accounting.services.tax_forms.pdv.supply_procedure import VatSupplyProcedure
from accounting.services.tax_forms.zp.aggregate import aggregate_zp_rows
from accounting.services.tax_forms.zp.verify import verify_zp_against_pdv_boxes
from accounting.services.vat import generate_vat_ledger
from expenses.models import Expense, ExpenseCategory
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from settings.models import CompanySettings
from tenants.models import Tenant

_FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'pdv'
_EXPECTED_OSS = _FIXTURES / 'expected_payload_oss_june_2026.json'


class PdvOssLedgerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='oss-ledger', name='OSS Ledger Co')
        provision_tenant_chart(cls.tenant)
        CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Fine Star d.o.o.',
            vat_number='36619131370',
            street='Test',
            house_number='1',
            city='Šibenik',
            postal_code='22000',
        )
        User = get_user_model()
        cls.user = User.objects.create_user(username='oss-ledger', password='test')
        cls.customer_de = Partner.all_objects.create(
            tenant=cls.tenant,
            name='EU Consumer DE',
            tax_number='',
            partner_type='customer',
            status='active',
            address='Berlin',
            city='Berlin',
            postal_code='10115',
            country='Germany',
        )
        cls.supplier_third = Partner.all_objects.create(
            tenant=cls.tenant,
            name='US Marketplace',
            tax_number='US123456789',
            partner_type='supplier',
            status='active',
            address='',
            city='',
            postal_code='',
            country='United States',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Roba')

    def _account(self, code: str) -> ChartOfAccounts:
        return ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code=code)

    def _create_oss_invoice(self) -> None:
        invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            company_to=self.customer_de,
            invoice_number='202606-OSS-001',
            issue_date=date(2026, 6, 18),
            due_date=date(2026, 6, 18),
            status='sent',
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name='Web shop widget',
            quantity=Decimal('2'),
            unit_price=Decimal('50.00'),
            tax_rate=Decimal('19.00'),
            vat_procedure=VatSupplyProcedure.OSS,
        )

    def test_oss_invoice_posts_to_box_215_not_zp(self):
        self._create_oss_invoice()
        generate_vat_ledger(self.tenant, 2026, 6, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=6)
        boxes = aggregate_vat_boxes(period)

        self.assertEqual(boxes['215'].base, Decimal('100.00'))
        self.assertEqual(boxes['215'].vat, Decimal('19.00'))
        self.assertEqual(boxes['101'].base, Decimal('0.00'))
        self.assertEqual(boxes['103'].base, Decimal('0.00'))

        cross = verify_zp_against_pdv_boxes(
            aggregate_zp_rows(period),
            pdv_box_101=boxes['101'].base,
            pdv_box_103=boxes['103'].base,
        )
        self.assertTrue(cross.is_aligned)

    def test_oss_does_not_change_domestic_vat_due(self):
        self._create_oss_invoice()
        generate_vat_ledger(self.tenant, 2026, 6, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=6)
        boxes = aggregate_vat_boxes(period)
        self.assertEqual(compute_vat_due(boxes), Decimal('0.00'))

    def test_ioss_expense_posts_to_box_308(self):
        Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='IOSS-1',
            status='paid',
            category=self.category,
            supplier=self.supplier_third,
            amount=Decimal('119.00'),
            tax_amount=Decimal('19.00'),
            vat_procedure=VatSupplyProcedure.IOSS,
            expense_date=date(2026, 6, 20),
            description='IOSS import purchase',
            created_by=self.user,
        )
        generate_vat_ledger(self.tenant, 2026, 6, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=6)
        boxes = aggregate_vat_boxes(period)

        self.assertEqual(boxes['308'].base, Decimal('100.00'))
        self.assertEqual(boxes['308'].vat, Decimal('19.00'))
        self.assertEqual(boxes['303'].vat, Decimal('0.00'))

    def test_ioss_journal_line_posts_to_box_308(self):
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202606-0200',
            entry_date=date(2026, 6, 21),
            status='posted',
            description='IOSS pretporez',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=self._account('14042'),
            debit_amount=Decimal('25.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=self._account('2200'),
            credit_amount=Decimal('25.00'),
        )

        generate_vat_ledger(self.tenant, 2026, 6, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=6)
        boxes = aggregate_vat_boxes(period)
        self.assertEqual(boxes['308'].base, Decimal('100.00'))
        self.assertEqual(boxes['308'].vat, Decimal('25.00'))

    def test_oss_payload_snapshot_contract(self):
        self._create_oss_invoice()
        generate_vat_ledger(self.tenant, 2026, 6, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=6)
        payload = build_pdv_payload(period)

        self.assertEqual(payload.mapping_version, PDV_MAPPING_VERSION)
        expected = json.loads(_EXPECTED_OSS.read_text(encoding='utf-8'))
        self.assertEqual(expected['mapping_version'], PDV_MAPPING_VERSION)
        pair = payload.field_pair('215')
        exp = expected['fields']['215']
        self.assertEqual(str(pair.vrijednost), exp['base'])
        self.assertEqual(str(pair.porez), exp['vat'])
        self.assertEqual(str(payload.field_scalar('400')), expected['fields']['400'])
