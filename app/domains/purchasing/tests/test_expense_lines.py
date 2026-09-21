"""OCR line_items → ExpenseLine amounts that sum to the header."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from domains.purchasing.services.expense_lines import parsed_ocr_lines, persist_extracted_lines
from expenses.models import Expense, ExpenseCategory, ExpenseLine
from expenses.tests.partner_helpers import create_supplier_partner
from tenants.models import Tenant


class PersistExtractedLinesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='ocrlines', name='OCR Lines')
        cls.user = User.objects.create_user(username='ocrlines', password='test')
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.supplier = create_supplier_partner(
            tenant=cls.tenant,
            name='HAC',
            tax_number='57500462912',
        )

    def _expense(self, **overrides) -> Expense:
        values = {
            'tenant': self.tenant,
            'expense_number': 'T-OCR-1',
            'status': 'draft',
            'category': self.category,
            'supplier': self.supplier,
            'amount': Decimal('130.00'),
            'tax_amount': Decimal('26.00'),
            'currency': 'EUR',
            'expense_date': '2026-09-18',
            'description': 'HAC',
            'created_by': self.user,
        }
        values.update(overrides)
        return Expense.all_objects.create(**values)

    def test_hac_gross_lines_allocate_net_and_vat(self):
        expense = self._expense()
        rows = parsed_ocr_lines(
            expense,
            {
                'line_items': [
                    {'description': 'ENC nadoplata', 'quantity': '1', 'unit_price': '100.00', 'amount': '100.00'},
                    {'description': 'ENC uređaj', 'quantity': '2', 'unit_price': '15.00', 'amount': '30.00'},
                ]
            },
        )
        self.assertEqual(
            [(row['description'], row['net_amount'], row['vat_amount'], row['gross_amount']) for row in rows],
            [
                ('ENC nadoplata', Decimal('80.00'), Decimal('20.00'), Decimal('100.00')),
                ('ENC uređaj', Decimal('24.00'), Decimal('6.00'), Decimal('30.00')),
            ],
        )

    def test_net_lines_keep_header_totals(self):
        expense = self._expense(amount=Decimal('125.00'), tax_amount=Decimal('25.00'))
        created = persist_extracted_lines(
            expense=expense,
            payload={
                'line_items': [
                    {'description': 'Diesel Class Plus', 'quantity': '1', 'unit_price': '100.00', 'amount': '100.00'}
                ]
            },
        )
        self.assertEqual(len(created), 1)
        line = created[0]
        self.assertEqual(line.net_amount, Decimal('100.00'))
        self.assertEqual(line.vat_amount, Decimal('25.00'))
        self.assertEqual(line.gross_amount, Decimal('125.00'))
        self.assertIsNone(line.posting_account_id)

    def test_blank_or_missing_items_are_skipped(self):
        expense = self._expense()
        created = persist_extracted_lines(
            expense=expense,
            payload={'line_items': [{'description': '', 'amount': '10.00'}, 'bad']},
        )
        self.assertEqual(created, [])
        self.assertEqual(ExpenseLine.all_objects.filter(expense=expense).count(), 0)

    def test_real_hac_ocr_three_gross_lines(self):
        expense = self._expense()
        rows = parsed_ocr_lines(
            expense,
            {
                'line_items': [
                    {
                        'amount': '100.00',
                        'quantity': '1',
                        'unit_price': '127.78',
                        'description': 'Uplata iznosa - ENC za kat. I',
                    },
                    {
                        'amount': '15.00',
                        'quantity': '1',
                        'unit_price': '15.00',
                        'description': 'UREDAJ ENC - KOMPLET Prepaid, za kat. I',
                    },
                    {
                        'amount': '15.00',
                        'quantity': '1',
                        'unit_price': '15.00',
                        'description': 'UREDAJ ENC - KOMPLET Prepaid, za kat. I',
                    },
                ]
            },
        )
        self.assertEqual(
            [(row['net_amount'], row['vat_amount'], row['gross_amount']) for row in rows],
            [
                (Decimal('80.00'), Decimal('20.00'), Decimal('100.00')),
                (Decimal('12.00'), Decimal('3.00'), Decimal('15.00')),
                (Decimal('12.00'), Decimal('3.00'), Decimal('15.00')),
            ],
        )

    def test_persist_writes_posting_account_by_position(self):
        from accounting.models import AccountType, ChartOfAccounts

        account_type = AccountType.all_objects.create(tenant=self.tenant, name='asset')
        prepaid = ChartOfAccounts.all_objects.create(
            tenant=self.tenant,
            account_code='1900',
            account_name='Prepaid',
            account_type=account_type,
            account_class='1',
            is_postable=True,
            is_active=True,
        )
        expense = self._expense()
        created = persist_extracted_lines(
            expense=expense,
            payload={
                'line_items': [
                    {'description': 'Uplata ENC', 'amount': '100.00'},
                    {'description': 'ENC uređaj', 'amount': '15.00'},
                    {'description': 'ENC uređaj', 'amount': '15.00'},
                ]
            },
            posting_accounts={1: prepaid, 2: None, 3: None},
        )
        self.assertEqual(created[0].posting_account_id, prepaid.pk)
        self.assertIsNone(created[1].posting_account_id)

    def test_mismatching_allocated_rows_are_rejected(self):
        from domains.purchasing.services.expense_lines import (
            LineAllocationMismatch,
            allocated_rows_match_header,
        )

        self.assertFalse(
            allocated_rows_match_header(
                [
                    {
                        'net_amount': Decimal('80.00'),
                        'vat_amount': Decimal('20.00'),
                        'gross_amount': Decimal('100.00'),
                    }
                ],
                header_net=Decimal('104.00'),
                header_tax=Decimal('26.00'),
            )
        )
        with patch(
            'domains.purchasing.services.expense_lines.parsed_ocr_lines',
            return_value=[
                {
                    'position': 1,
                    'description': 'X',
                    'net_amount': Decimal('10.00'),
                    'vat_amount': Decimal('1.00'),
                    'gross_amount': Decimal('11.00'),
                }
            ],
        ):
            with self.assertRaises(LineAllocationMismatch):
                persist_extracted_lines(expense=self._expense(), payload={'line_items': []})
