"""Architecture contract: build_pdv_payload uses aggregate_vat_boxes exactly once."""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from accounting.models import VATPeriod
from accounting.services.tax_forms.pdv.aggregate import BoxTotals
from accounting.services.tax_forms.pdv.boxes import active_boxes
from accounting.services.tax_forms.pdv.build import build_pdv_payload
from settings.models import CompanySettings
from tenants.models import Tenant


class BuildPdvPayloadArchitectureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='pdv-build-contract', name='PDV build contract')
        CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Contract d.o.o.',
            company_address='Ulica 1\n10000 Zagreb',
            street='Ulica',
            house_number='1',
            postal_code='10000',
            city='Zagreb',
            company_phone='+385 1 000 000',
            company_email='contract@example.com',
            vat_number='12345678901',
        )
        cls.period = VATPeriod.all_objects.create(tenant=cls.tenant, year=2026, month=4)

    @patch('accounting.services.tax_forms.pdv.build.aggregate_vat_boxes')
    @patch('accounting.models.VATLedgerEntry.all_objects')
    @patch('accounting.models.JournalEntryLine.objects')
    @patch('accounting.models.JournalEntry.objects')
    @patch('expenses.models.Expense.all_objects')
    @patch('invoices.models.Invoice.objects')
    def test_build_pdv_payload_single_aggregate(
        self,
        mock_invoice_objects,
        mock_expense_objects,
        mock_journal_entry_objects,
        mock_journal_line_objects,
        mock_vat_ledger_objects,
        mock_aggregate,
    ):
        boxes = {box.code: BoxTotals(Decimal('0.00'), Decimal('0.00')) for box in active_boxes()}
        boxes['203'] = BoxTotals(Decimal('100.00'), Decimal('25.00'))
        boxes['303'] = BoxTotals(Decimal('336.43'), Decimal('84.11'))
        mock_aggregate.return_value = boxes

        payload = build_pdv_payload(self.period)

        mock_aggregate.assert_called_once_with(self.period)
        mock_invoice_objects.assert_not_called()
        mock_expense_objects.assert_not_called()
        mock_journal_entry_objects.assert_not_called()
        mock_journal_line_objects.assert_not_called()
        mock_vat_ledger_objects.assert_not_called()

        self.assertEqual(payload.field_pair('203').vrijednost, Decimal('100.00'))
        self.assertEqual(payload.field_scalar('400'), Decimal('-59.11'))
