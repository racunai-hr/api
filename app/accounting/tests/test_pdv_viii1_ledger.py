"""VIII.1 ledger generation for boxes 610–615."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import ChartOfAccounts, JournalEntry, JournalEntryLine, VATPeriod
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes
from accounting.services.tax_forms.pdv.build import build_pdv_payload
from accounting.services.tax_forms.pdv.mapping import PDV_MAPPING_VERSION
from accounting.services.vat import generate_vat_ledger
from settings.models import CompanySettings
from tenants.models import Tenant

_FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'pdv'
_EXPECTED_VIII1 = _FIXTURES / 'expected_payload_viii1_may_2026.json'


class PdvViii1LedgerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='viii1-ledger', name='VIII.1 Ledger Co')
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
        cls.user = User.objects.create_user(username='viii1-ledger', password='test')

    def _account(self, code: str) -> ChartOfAccounts:
        return ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code=code)

    def test_car_activation_posts_612_and_610_total(self):
        activation = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202605-0300',
            entry_date=date(2026, 5, 25),
            status='posted',
            description='Aktivacija vozila 0373 → 032001',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=activation,
            account=self._account('032001'),
            debit_amount=Decimal('23882.35'),
        )
        JournalEntryLine.objects.create(
            journal_entry=activation,
            account=self._account('0373'),
            credit_amount=Decimal('23882.35'),
        )

        generate_vat_ledger(self.tenant, 2026, 5, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=5)
        boxes = aggregate_vat_boxes(period)

        self.assertEqual(boxes['612'].base, Decimal('23882.35'))
        self.assertEqual(boxes['610'].base, Decimal('23882.35'))
        self.assertEqual(boxes['207'].base, Decimal('0.00'))

    def test_office_equipment_posts_614(self):
        acquisition = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202605-0301',
            entry_date=date(2026, 5, 18),
            status='posted',
            description='Nabava uredske opreme',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=acquisition,
            account=self._account('0310'),
            debit_amount=Decimal('1200.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=acquisition,
            account=self._account('1000'),
            credit_amount=Decimal('1200.00'),
        )

        generate_vat_ledger(self.tenant, 2026, 5, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=5)
        boxes = aggregate_vat_boxes(period)

        self.assertEqual(boxes['614'].base, Decimal('1200.00'))
        self.assertEqual(boxes['610'].base, Decimal('1200.00'))

    def test_viii1_payload_snapshot_contract(self):
        acquisition = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202605-0302',
            entry_date=date(2026, 5, 18),
            status='posted',
            description='Snapshot VIII.1',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=acquisition,
            account=self._account('0310'),
            debit_amount=Decimal('1200.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=acquisition,
            account=self._account('1000'),
            credit_amount=Decimal('1200.00'),
        )

        generate_vat_ledger(self.tenant, 2026, 5, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=5)
        payload = build_pdv_payload(period)

        self.assertEqual(payload.mapping_version, PDV_MAPPING_VERSION)
        expected = json.loads(_EXPECTED_VIII1.read_text(encoding='utf-8'))
        self.assertEqual(expected['mapping_version'], PDV_MAPPING_VERSION)
        for code in ('610', '611', '612', '613', '614', '615', '400'):
            self.assertEqual(
                str(payload.field_scalar(code)),
                expected['fields'][code],
                msg=f'box {code}',
            )
