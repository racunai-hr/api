"""Mini-checkpoint 7.5a: sintetički svibanj 2026 s EU boxovima 207/307."""

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
from accounting.services.tax_forms.pdv.parse import parse_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.payload import PdvFormHeader
from accounting.services.tax_forms.pdv.render import render_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.validation import validate_pdv_obrazac_xml
from accounting.services.vat import generate_vat_ledger
from expenses.models import Expense, ExpenseCategory
from partners.models import Partner
from settings.models import CompanySettings
from tenants.models import Tenant

_FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'pdv'
_SUBMITTED_APRIL_2026 = _FIXTURES / 'submitted_april_2026.xml'


class PdvCheckpointMay2026SyntheticTests(TestCase):
    """Synthetic gate before first May submission with EU boxes."""

    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='finestar-may-synthetic', name='Fine Star May synthetic')
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
        cls.user = User.objects.create_superuser(username='may-synthetic-admin', password='test')
        cls.supplier_hr = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Domestic dobavljač',
            tax_number='11111111111',
            partner_type='supplier',
            status='active',
            address='',
            city='',
            postal_code='',
            country='Croatia',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Materijal')

        Expense.all_objects.create(
            tenant=cls.tenant,
            expense_number='MAY-303-1',
            status='paid',
            category=cls.category,
            supplier=cls.supplier_hr,
            amount=Decimal('420.43'),
            tax_amount=Decimal('84.11'),
            expense_date=date(2026, 5, 12),
            description='Domestic pretporez',
            created_by=cls.user,
        )

        account = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='0373')
        bank = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='1000')
        pretporez = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='14022')
        obveza = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='24022')

        acquisition = JournalEntry.all_objects.create(
            tenant=cls.tenant,
            entry_number='202605-0011',
            entry_date=date(2026, 5, 22),
            status='posted',
            description='EU stjecanje Hadžić',
            created_by=cls.user,
        )
        JournalEntryLine.objects.create(journal_entry=acquisition, account=account, debit_amount=Decimal('8000.00'))
        JournalEntryLine.objects.create(journal_entry=acquisition, account=bank, credit_amount=Decimal('8000.00'))

        reverse_charge = JournalEntry.all_objects.create(
            tenant=cls.tenant,
            entry_number='202605-0012',
            entry_date=date(2026, 5, 22),
            status='posted',
            description='RC EU stjecanje',
            created_by=cls.user,
        )
        JournalEntryLine.objects.create(journal_entry=reverse_charge, account=pretporez, debit_amount=Decimal('2000.00'))
        JournalEntryLine.objects.create(journal_entry=reverse_charge, account=obveza, credit_amount=Decimal('2000.00'))

        generate_vat_ledger(cls.tenant, 2026, 5, replace=True)
        cls.period = VATPeriod.all_objects.get(tenant=cls.tenant, year=2026, month=5)

    def test_eu_boxes_match_expected(self):
        boxes = aggregate_vat_boxes(self.period)
        self.assertEqual(boxes['207'].base, Decimal('8000.00'))
        self.assertEqual(boxes['207'].vat, Decimal('2000.00'))
        self.assertEqual(boxes['307'].base, Decimal('8000.00'))
        self.assertEqual(boxes['307'].vat, Decimal('2000.00'))
        self.assertEqual(boxes['303'].vat, Decimal('84.11'))
        for code in ('610', '611', '612', '613', '614', '615'):
            self.assertEqual(boxes[code].base, Decimal('0.00'))
            self.assertEqual(boxes[code].vat, Decimal('0.00'))

    def test_invariants_i1_to_i3(self):
        boxes = aggregate_vat_boxes(self.period)
        self.assertGreaterEqual(boxes['207'].base, Decimal('0.00'))
        self.assertEqual(boxes['207'].vat, Decimal('2000.00'))
        self.assertEqual(boxes['207'].vat, boxes['307'].vat)
        self.assertEqual(boxes['207'].base, boxes['307'].base)

    def test_build_render_passes_xsd(self):
        payload = build_pdv_payload(self.period)
        header = PdvFormHeader(
            prepared_by_first_name='Test',
            prepared_by_last_name='User',
            tax_office_code='3566',
        )
        xml_bytes = render_pdv_obrazac_xml(payload, header=header)
        validate_pdv_obrazac_xml(xml_bytes, signed=False)
        pair_207 = payload.field_pair('207')
        self.assertEqual(pair_207.vrijednost, Decimal('8000.00'))
        self.assertEqual(pair_207.porez, Decimal('2000.00'))
        self.assertEqual(payload.field_scalar('400'), Decimal('-84.11'))

    def test_april_reference_fixture_unchanged(self):
        reference = parse_pdv_obrazac_xml(_SUBMITTED_APRIL_2026.read_bytes())
        self.assertEqual(reference.field_scalar('400'), Decimal('-84.11'))
        self.assertEqual(
            reference.field_pair('303').porez,
            Decimal('84.11'),
        )
