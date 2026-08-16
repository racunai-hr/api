from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.models import (
    Expense,
    ExpenseCategory,
    ExpenseImportMetadata,
    ExpensePayer,
    ExpenseSource,
    ImportBatch,
    SettlementMethod,
)
from expenses.tests.partner_helpers import create_supplier_partner
from expenses.parsers.f1_csv import F1CsvParser
from expenses.services.import_service import import_expense_rows
from expenses.services.numbering import assign_expense_number
from tenants.models import Tenant


IMPORT_CSV = """\
Naziv poreznog obveznika: FINE STAR d.o.o.
OIB poreznog obveznika: 36619131370
Razdoblje: 01.06.2026. 00:00:00 - 30.06.2026. 23:59:00
Broj računa;Oznaka poslovnog prostora;Oznaka naplatnog uređaja;JIR;OIB izdavatelja računa;Datum i vrijeme izdavanja računa;Datum fiskalizacije;Osnovica 0%;Porez 0%;Osnovica 5%;Porez 5%;Osnovica 13%;Porez 13%;Osnovica 25%;Porez 25%;Ukupni iznos računa;Način plaćanja
20965;BB12;1;jir-20965;81793146560;30.05.2026. 08:19:26;30.05.2026. 08:19:27;;;;;;;3,20;0,80;4,00;K
20301;BB12;1;jir-20301;81793146560;25.05.2026. 22:58:40;25.05.2026. 22:58:41;;;;;;;9,60;2,40;12,00;K
12721;34;341;jir-12721;93923226222;04.05.2026. 18:07:02;04.05.2026. 18:07:13;;;;;;;6,32;1,58;7,90;K
16573;BB12;1;jir-16573;81793146560;01.05.2026. 08:29:21;01.05.2026. 08:29:22;;;;;;;12,80;3,20;16,00;K
21822;BB12;1;jir-21822-dup;81793146560;04.06.2026. 11:50:55;04.06.2026. 11:50:56;;;;;;;3,20;0,80;4,00;K
21238;BB12;1;jir-21238;81793146560;01.06.2026. 07:57:21;01.06.2026. 07:57:22;;;;;;;4,80;1,20;6,00;K
21823;BB12;1;jir-21823;81793146560;05.06.2026. 11:50:55;05.06.2026. 11:50:56;;;;;;;3,20;0,80;4,00;K
21824;BB12;1;jir-21824;81793146560;06.06.2026. 11:50:55;06.06.2026. 11:50:56;;;;;;;3,20;0,80;4,00;K
"""


class ExpenseNumberingTests(TestCase):
    def test_assign_expense_number_starts_at_one(self):
        tenant = Tenant.objects.create(slug='numco', name='Num Co')
        self.assertEqual(assign_expense_number(tenant, year=2026), 'T-2026-0001')

    def test_assign_expense_number_increments(self):
        tenant = Tenant.objects.create(slug='numco2', name='Num Co 2')
        user = User.objects.create_user(username='numuser', password='test')
        category = ExpenseCategory.all_objects.create(tenant=tenant, name='Ops')
        supplier = create_supplier_partner(tenant=tenant, name='Sup', tax_number='11111111111')
        Expense.all_objects.create(
            tenant=tenant,
            expense_number='T-2026-0001',
            source=ExpenseSource.MANUAL,
            status='approved',
            category=category,
            supplier=supplier,
            amount=Decimal('1.00'),
            currency='EUR',
            expense_date='2026-06-01',
            description='Existing',
            created_by=user,
        )
        self.assertEqual(assign_expense_number(tenant, year=2026), 'T-2026-0002')


class ImportServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='importco', name='Import Co')
        cls.user = User.objects.create_user(username='importer', password='test')
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.supplier = create_supplier_partner(
            tenant=cls.tenant,
            name='Existing Supplier',
            tax_number='81793146560',
        )
        cls.existing = Expense.all_objects.create(
            tenant=cls.tenant,
            expense_number='T-2026-0001',
            source=ExpenseSource.MANUAL,
            status='approved',
            category=cls.category,
            supplier=cls.supplier,
            amount=Decimal('4.00'),
            tax_amount=Decimal('0.80'),
            currency='EUR',
            expense_date='2026-06-04',
            receipt_number='21822-BB12-1',
            description='Postojeći trošak',
            created_by=cls.user,
        )

    def test_dry_run_creates_batch_without_expenses(self):
        parse_result = F1CsvParser().parse(IMPORT_CSV)
        result = import_expense_rows(
            tenant=self.tenant,
            user=self.user,
            rows=parse_result.rows,
            source=ExpenseSource.F1_CSV,
            filename='test.csv',
            dry_run=True,
        )

        self.assertTrue(result.dry_run)
        self.assertEqual(result.rows_total, 8)
        self.assertEqual(result.created, 7)
        self.assertEqual(result.duplicates, 1)
        self.assertIn('21822-BB12-1', result.duplicate_receipts)

        batch = ImportBatch.all_objects.get(pk=result.batch_id)
        self.assertTrue(batch.dry_run)
        self.assertEqual(batch.created_count, 7)
        self.assertEqual(Expense.all_objects.filter(source=ExpenseSource.F1_CSV).count(), 0)

    def test_import_creates_expenses_with_numbering_and_metadata(self):
        parse_result = F1CsvParser().parse(IMPORT_CSV)
        result = import_expense_rows(
            tenant=self.tenant,
            user=self.user,
            rows=parse_result.rows,
            source=ExpenseSource.F1_CSV,
            filename='test.csv',
            dry_run=False,
        )

        self.assertEqual(result.created, 7)
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(ExpenseImportMetadata.all_objects.filter(source=ExpenseSource.F1_CSV).count(), 7)

        imported = Expense.all_objects.filter(source=ExpenseSource.F1_CSV).order_by('expense_number')
        self.assertEqual(imported.first().expense_number, 'T-2026-0002')
        self.assertEqual(imported.last().expense_number, 'T-2026-0008')
        self.assertTrue(all(exp.status == 'approved' for exp in imported))

        batch = ImportBatch.all_objects.get(pk=result.batch_id)
        self.assertFalse(batch.dry_run)
        self.assertEqual(batch.created_count, 7)
        self.assertEqual(batch.duplicates_count, 1)

    def test_import_with_settlement_override_keeps_f1_payment_method(self):
        payer = ExpensePayer.all_objects.create(
            tenant=self.tenant,
            name='Ante Vrcan',
            oib='11528564544',
        )
        parse_result = F1CsvParser().parse(IMPORT_CSV)
        result = import_expense_rows(
            tenant=self.tenant,
            user=self.user,
            rows=parse_result.rows[:1],
            source=ExpenseSource.F1_CSV,
            filename='settlement.csv',
            dry_run=False,
            status='paid',
            settlement_method=SettlementMethod.PRIVATE_CARD,
            paid_by=payer,
        )
        self.assertEqual(result.created, 1)
        expense = Expense.all_objects.filter(source=ExpenseSource.F1_CSV).latest('pk')
        self.assertEqual(expense.payment_method, 'card')
        self.assertEqual(expense.settlement_method, SettlementMethod.PRIVATE_CARD)
        self.assertEqual(expense.paid_by, payer)
        self.assertEqual(expense.status, 'paid')

    def test_metadata_dedup_on_second_import(self):
        parse_result = F1CsvParser().parse(IMPORT_CSV)
        import_expense_rows(
            tenant=self.tenant,
            user=self.user,
            rows=parse_result.rows[:1],
            source=ExpenseSource.F1_CSV,
            filename='first.csv',
            dry_run=False,
        )
        result = import_expense_rows(
            tenant=self.tenant,
            user=self.user,
            rows=parse_result.rows[:1],
            source=ExpenseSource.F1_CSV,
            filename='second.csv',
            dry_run=False,
        )
        self.assertEqual(result.created, 0)
        self.assertEqual(result.duplicates, 1)
