"""Reverse charge ledger generation and payload snapshot contract."""

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
from accounting.services.vat import generate_vat_ledger
from settings.models import CompanySettings
from tenants.models import Tenant

_FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'pdv'
_EXPECTED_RC = _FIXTURES / 'expected_payload_reverse_charge_may_2026.json'


class PdvReverseChargeLedgerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='rc-ledger', name='RC Ledger Co')
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
        cls.user = User.objects.create_user(username='rc-ledger', password='test')

    def _account(self, code: str) -> ChartOfAccounts:
        return ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code=code)

    def _post_domestic_construction_rc(self, *, base: Decimal = Decimal('10000.00')):
        vat = (base * Decimal('0.25')).quantize(Decimal('0.01'))
        acquisition = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202606-0100',
            entry_date=date(2026, 6, 12),
            status='posted',
            description='Građevinske usluge RC',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=acquisition,
            account=self._account('4120'),
            debit_amount=base,
        )
        JournalEntryLine.objects.create(
            journal_entry=acquisition,
            account=self._account('2200'),
            credit_amount=base,
        )
        rc = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202606-0101',
            entry_date=date(2026, 6, 12),
            status='posted',
            description='RC tuzemstvo građevina',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=rc,
            account=self._account('1401'),
            debit_amount=vat,
        )
        JournalEntryLine.objects.create(
            journal_entry=rc,
            account=self._account('2401'),
            credit_amount=vat,
        )
        return base, vat

    def _post_eu_services_rc(self, *, base: Decimal = Decimal('4000.00')):
        vat = (base * Decimal('0.25')).quantize(Decimal('0.01'))
        rc = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202606-0102',
            entry_date=date(2026, 6, 15),
            status='posted',
            description='RC EU B2B usluge',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=rc,
            account=self._account('14032'),
            debit_amount=vat,
        )
        JournalEntryLine.objects.create(
            journal_entry=rc,
            account=self._account('24032'),
            credit_amount=vat,
        )
        return base, vat

    def test_domestic_construction_rc_posts_209_and_309(self):
        base, vat = self._post_domestic_construction_rc()
        generate_vat_ledger(self.tenant, 2026, 6, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=6)
        boxes = aggregate_vat_boxes(period)

        self.assertEqual(boxes['209'].base, base)
        self.assertEqual(boxes['209'].vat, vat)
        self.assertEqual(boxes['309'].base, base)
        self.assertEqual(boxes['309'].vat, vat)

    def test_eu_b2b_services_rc_posts_210_and_306(self):
        base, vat = self._post_eu_services_rc()
        generate_vat_ledger(self.tenant, 2026, 6, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=6)
        boxes = aggregate_vat_boxes(period)

        self.assertEqual(boxes['210'].base, base)
        self.assertEqual(boxes['210'].vat, vat)
        self.assertEqual(boxes['306'].base, base)
        self.assertEqual(boxes['306'].vat, vat)

    def test_rc_pairs_are_vat_due_neutral(self):
        self._post_domestic_construction_rc()
        self._post_eu_services_rc()
        generate_vat_ledger(self.tenant, 2026, 6, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=6)
        boxes = aggregate_vat_boxes(period)
        vat_due = compute_vat_due(boxes)

        zero_rc = dict(boxes)
        for code in ('209', '210', '306', '309'):
            zero_rc[code] = type(boxes[code])(Decimal('0.00'), Decimal('0.00'))
        self.assertEqual(vat_due, compute_vat_due(zero_rc))

    def test_reverse_charge_payload_snapshot_contract(self):
        self._post_domestic_construction_rc()
        self._post_eu_services_rc()
        generate_vat_ledger(self.tenant, 2026, 6, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=6)
        payload = build_pdv_payload(period)

        self.assertEqual(payload.mapping_version, PDV_MAPPING_VERSION)
        expected = json.loads(_EXPECTED_RC.read_text(encoding='utf-8'))
        self.assertEqual(expected['mapping_version'], PDV_MAPPING_VERSION)
        for code in ('209', '210', '306', '309'):
            pair = payload.field_pair(code)
            exp = expected['fields'][code]
            self.assertEqual(str(pair.vrijednost), exp['base'], msg=f'box {code} base')
            self.assertEqual(str(pair.porez), exp['vat'], msg=f'box {code} vat')
        self.assertEqual(str(payload.field_scalar('400')), expected['fields']['400'])
