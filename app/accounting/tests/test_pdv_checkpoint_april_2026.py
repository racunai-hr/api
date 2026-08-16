"""Mini-checkpoint 2.5: travanj 2026 ERP agregat == referentni XML."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import VATPeriod
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_forms.pdv.aggregate import BoxTotals, aggregate_vat_boxes, compute_vat_due
from accounting.services.tax_forms.pdv.boxes import active_boxes
from accounting.services.tax_forms.pdv.parse import parse_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.payload import PdvFieldPair
from accounting.services.vat import generate_vat_ledger
from expenses.models import Expense, ExpenseCategory
from partners.models import Partner
from tenants.models import Tenant

_FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'pdv'
_SUBMITTED_APRIL_2026 = _FIXTURES / 'submitted_april_2026.xml'

_IMPLEMENTED_PAIR_BOXES = ('201', '202', '203', '303')


def _pair_to_totals(pair: PdvFieldPair) -> BoxTotals:
    return BoxTotals(pair.vrijednost, pair.porez)


class PdvCheckpointApril2026Tests(TestCase):
    """Gate before Ticket 3: Fine Star travanj 2026 matches submitted Obrazac PDV."""

    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='finestar-checkpoint', name='Fine Star checkpoint')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_superuser(username='checkpoint-admin', password='test')
        cls.supplier_large = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Veliki dobavljač d.o.o.',
            tax_number='11111111111',
            partner_type='supplier',
            status='active',
            address='',
            city='',
            postal_code='',
        )
        cls.supplier_small = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Mali dobavljač d.o.o.',
            tax_number='22222222222',
            partner_type='supplier',
            status='active',
            address='',
            city='',
            postal_code='',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Materijal')

        Expense.all_objects.create(
            tenant=cls.tenant,
            expense_number='T-2026-0007',
            status='paid',
            category=cls.category,
            supplier=cls.supplier_large,
            amount=Decimal('400.55'),
            tax_amount=Decimal('80.11'),
            expense_date=date(2026, 4, 24),
            receipt_number='865-PJ1-1',
            description='Trošak s pretporezom 25%',
            created_by=cls.user,
        )
        Expense.all_objects.create(
            tenant=cls.tenant,
            expense_number='T-2026-0008',
            status='paid',
            category=cls.category,
            supplier=cls.supplier_small,
            amount=Decimal('19.99'),
            tax_amount=Decimal('4.00'),
            expense_date=date(2026, 4, 16),
            receipt_number='476-37-1',
            description='Mali trošak s pretporezom',
            created_by=cls.user,
        )

        generate_vat_ledger(cls.tenant, 2026, 4, replace=True)
        cls.period = VATPeriod.all_objects.get(tenant=cls.tenant, year=2026, month=4)
        cls.reference = parse_pdv_obrazac_xml(_SUBMITTED_APRIL_2026.read_bytes())

    def test_implemented_boxes_match_submitted_xml(self):
        erp = aggregate_vat_boxes(self.period)

        for code in _IMPLEMENTED_PAIR_BOXES:
            with self.subTest(box=code):
                self.assertEqual(erp[code], _pair_to_totals(self.reference.field_pair(code)))

    def test_podatak_400_matches_submitted_xml(self):
        erp = aggregate_vat_boxes(self.period)
        self.assertEqual(compute_vat_due(erp), self.reference.field_scalar('400'))
        self.assertEqual(compute_vat_due(erp), Decimal('-84.11'))

    def test_other_active_boxes_are_zero(self):
        erp = aggregate_vat_boxes(self.period)
        skip_pairs = set(_IMPLEMENTED_PAIR_BOXES) | {'200', '300'}
        skip_scalars = {'400'}
        skip_bools = {'660'}

        for definition in active_boxes():
            with self.subTest(box=definition.code):
                if definition.field_type == 'pair' and definition.code not in skip_pairs:
                    self.assertEqual(erp[definition.code], BoxTotals(Decimal('0.00'), Decimal('0.00')))
                    self.assertEqual(
                        _pair_to_totals(self.reference.field_pair(definition.code)),
                        BoxTotals(Decimal('0.00'), Decimal('0.00')),
                    )
                elif definition.field_type == 'scalar' and definition.code not in skip_scalars:
                    self.assertEqual(self.reference.field_scalar(definition.code), Decimal('0.00'))
                elif definition.field_type == 'bool' and definition.code not in skip_bools:
                    self.assertFalse(self.reference.field_bool(definition.code))

    def test_reference_fixture_matches_known_april_values(self):
        self.assertEqual(
            _pair_to_totals(self.reference.field_pair('303')),
            BoxTotals(Decimal('336.43'), Decimal('84.11')),
        )
        self.assertEqual(self.reference.field_scalar('400'), Decimal('-84.11'))
