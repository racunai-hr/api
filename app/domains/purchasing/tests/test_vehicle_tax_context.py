"""ExpenseLine → TaxEvaluationContext mapper. No evaluate(), no category/supplier fallback."""

import inspect
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from domains.purchasing.services.vehicle_tax_context import context_from_expense_line
from domains.tax.vehicle.contracts import (
    DocumentFacts,
    EvidenceType,
    SupplierVatStatus,
    VehicleClass,
    VehicleFacts,
    VatExceptionMode,
)
from expenses.models import Expense, ExpenseCategory, ExpenseLine, VehicleLineKind
from expenses.tests.partner_helpers import create_supplier_partner
from tenants.models import Tenant


class ContextFromExpenseLineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='taxctx', name='Tax Ctx')
        cls.user = User.objects.create_user(username='taxctx', password='test')
        cls.category = ExpenseCategory.all_objects.create(
            tenant=cls.tenant,
            name='Registracija',
            code='vehicle_registration',
        )
        cls.other_category = ExpenseCategory.all_objects.create(
            tenant=cls.tenant,
            name='IT',
            code=None,
        )
        cls.supplier = create_supplier_partner(
            tenant=cls.tenant,
            name='STP',
            tax_number='12345678901',
        )
        cls.other_supplier = create_supplier_partner(
            tenant=cls.tenant,
            name='HT',
            tax_number='27759560625',
        )
        cls.expense = Expense.all_objects.create(
            tenant=cls.tenant,
            expense_number='T-2026-0200',
            status='draft',
            category=cls.category,
            supplier=cls.supplier,
            amount=Decimal('23.36'),
            tax_amount=Decimal('4.67'),
            currency='EUR',
            expense_date='2026-03-08',
            description='STP',
            created_by=cls.user,
        )
        cls.line = ExpenseLine.all_objects.create(
            expense=cls.expense,
            position=1,
            description='Tehnički pregled',
            net_amount=Decimal('18.69'),
            vat_amount=Decimal('4.67'),
            gross_amount=Decimal('23.36'),
            vehicle_line_kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
        )

    def test_maps_only_line_persistence_fields(self):
        ctx = context_from_expense_line(self.line)
        self.assertEqual(
            ctx.line.vehicle_line_kind,
            VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
        )
        self.assertEqual(ctx.line.net_amount, Decimal('18.69'))
        self.assertEqual(ctx.line.vat_amount, Decimal('4.67'))
        self.assertEqual(ctx.line.gross_amount, Decimal('23.36'))

    def test_omitted_vehicle_and_document_are_explicit_nulls(self):
        ctx = context_from_expense_line(self.line)
        self.assertEqual(ctx.vehicle, VehicleFacts())
        self.assertEqual(ctx.document, DocumentFacts())
        self.assertIsNone(ctx.vehicle.vehicle_class)
        self.assertIsNone(ctx.vehicle.vat_exception_mode)
        self.assertIsNone(ctx.vehicle.cit_exception_mode)
        self.assertIsNone(ctx.vehicle.benefit_in_kind)
        self.assertIsNone(ctx.document.evidence_type)
        self.assertIsNone(ctx.document.supplier_vat_status)
        self.assertIsNot(ctx.vehicle.vat_exception_mode, VatExceptionMode.NONE)
        self.assertIsNot(ctx.document.evidence_type, EvidenceType.INVOICE)
        self.assertIsNot(ctx.document.evidence_type, EvidenceType.UNKNOWN)
        self.assertIsNot(ctx.document.supplier_vat_status, SupplierVatStatus.UNKNOWN)

    def test_explicit_vehicle_and_document_are_passed_through(self):
        vehicle = VehicleFacts(vehicle_class=VehicleClass.M1)
        document = DocumentFacts(
            evidence_type=EvidenceType.INVOICE,
            supplier_vat_status=SupplierVatStatus.REGISTERED,
        )
        ctx = context_from_expense_line(self.line, vehicle=vehicle, document=document)
        self.assertIs(ctx.vehicle, vehicle)
        self.assertIs(ctx.document, document)

    def test_category_and_supplier_changes_do_not_change_context(self):
        first = context_from_expense_line(self.line)
        self.expense.category = self.other_category
        self.expense.supplier = self.other_supplier
        self.expense.save(update_fields=['category', 'supplier'])
        self.line.refresh_from_db()
        second = context_from_expense_line(self.line)
        self.assertEqual(first, second)

    def test_mapper_does_not_evaluate_or_read_header_fields(self):
        import domains.purchasing.services.vehicle_tax_context as mapper

        source = inspect.getsource(mapper)
        self.assertNotIn('evaluate', source)
        self.assertNotIn('.save', source)
        self.assertNotIn('category', source)
        self.assertNotIn('supplier', source)
        self.assertNotIn('ocr', source.lower())
