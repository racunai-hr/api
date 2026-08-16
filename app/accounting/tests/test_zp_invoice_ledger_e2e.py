"""E2E: Invoice (EU outbound) → generate_vat_ledger → aggregate_zp_rows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import VATPeriod
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes
from accounting.services.tax_forms.zp.aggregate import aggregate_zp_rows
from accounting.services.tax_forms.zp.canonical import payload_to_dict
from accounting.services.tax_forms.zp.verify import verify_zp_against_pdv_boxes
from accounting.services.vat import generate_vat_ledger
from accounting.tests.fixtures.tax.zp.load import load_scenario
from accounting.tests.test_zp_aggregate import _expected_aggregate_dict
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice
from tenants.models import Tenant

_COUNTRY_NAMES = {
    'DE': 'Germany',
    'FR': 'France',
    'IT': 'Italy',
}


class ZpInvoiceLedgerE2ETests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='zp-invoice-e2e', name='ZP Invoice E2E Co')
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        settings = CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='FINE STAR D.O.O.',
            street='Bana Josipa Jelačića',
            house_number='58',
            postal_code='22000',
            city='Šibenik',
            vat_number='36619131370',
            tax_office=tax_office,
        )
        ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Toni',
            last_name='Šupe',
        )
        User = get_user_model()
        cls.user = User.objects.create_user(username='zp-invoice-e2e', password='test')

    def _partner_for_vat_id(self, vat_id: str) -> Partner:
        country_code = vat_id[:2]
        partner, _ = Partner.all_objects.get_or_create(
            tenant=self.tenant,
            tax_number=vat_id,
            defaults={
                'name': f'EU Partner {country_code}',
                'partner_type': 'customer',
                'status': 'active',
                'address': 'EU street 1',
                'city': 'EU City',
                'postal_code': '00000',
                'country': _COUNTRY_NAMES.get(country_code, country_code),
            },
        )
        return partner

    def _create_invoice_from_ledger_entry(self, *, entry: dict, index: int) -> None:
        partner = self._partner_for_vat_id(entry['partner_vat_id'])
        entry_date = date.fromisoformat(entry['entry_date'])
        is_service = entry['vat_box'] == '103'
        invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            company_to=partner,
            invoice_number=f'EU-{entry_date:%Y%m}-{index:03d}',
            issue_date=entry_date,
            due_date=entry_date,
            service_date=entry_date if is_service else None,
            status='sent',
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name=entry.get('description', 'EU supply'),
            quantity=Decimal('1'),
            unit_price=Decimal(entry['net_amount']),
            tax_rate=Decimal('0.00'),
        )

    def _seed_invoices_from_ledger(self, ledger: dict) -> None:
        for index, entry in enumerate(ledger['entries']):
            self._create_invoice_from_ledger_entry(entry=entry, index=index)

    def _assert_scenario_e2e(self, scenario_id: str) -> None:
        data = load_scenario(scenario_id)
        ledger = data['ledger']
        period_info = ledger['period']
        self._seed_invoices_from_ledger(ledger)

        generate_vat_ledger(self.tenant, period_info['year'], period_info['month'], replace=True)
        period = VATPeriod.all_objects.get(
            tenant=self.tenant,
            year=period_info['year'],
            month=period_info['month'],
        )

        payload = aggregate_zp_rows(period)
        self.assertEqual(payload_to_dict(payload), _expected_aggregate_dict(data['expected']))

        boxes = aggregate_vat_boxes(period)
        result = verify_zp_against_pdv_boxes(
            payload,
            pdv_box_101=boxes['101'].base,
            pdv_box_103=boxes['103'].base,
        )
        self.assertTrue(result.is_aligned)

    def test_single_eu_partner_invoice_to_zp(self):
        self._assert_scenario_e2e('single_eu_partner')

    def test_multiple_eu_partners_invoice_to_zp(self):
        self._assert_scenario_e2e('multiple_eu_partners')
