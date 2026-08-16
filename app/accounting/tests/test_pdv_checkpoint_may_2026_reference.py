"""Mini-checkpoint 7.5b-ref: svibanj 2026 ERP agregat == referentni (ne-submitted) XML.

Referentni fixture: ERP export bez ePorezna potpisa; EU boxovi 610–615 su 0.00.
Za dokaz EU logike koristi se test_pdv_checkpoint_may_2026_synthetic.py.
PR-D (submitted gate s nenultim EU boxovima) ostaje otvoren do prve stvarne predaje.
"""

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
_REFERENCE_MAY_2026 = _FIXTURES / 'archive' / 'PDV_36619131370_20260501-20260531.xml'

_IMPLEMENTED_PAIR_BOXES = ('201', '202', '203', '303')
_EU_SCALAR_BOXES = ('610', '611', '612', '613', '614', '615')


def _pair_to_totals(pair: PdvFieldPair) -> BoxTotals:
    return BoxTotals(pair.vrijednost, pair.porez)


class PdvCheckpointMay2026ReferenceTests(TestCase):
    """ERP matches archived reference Obrazac PDV for May 2026 (domestic boxes; EU zero)."""

    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='finestar-may-reference', name='Fine Star May reference')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_superuser(username='may-reference-admin', password='test')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Domestic dobavljač d.o.o.',
            tax_number='33333333333',
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
            expense_number='MAY-2026-0001',
            status='paid',
            category=cls.category,
            supplier=cls.supplier,
            amount=Decimal('39.90'),
            tax_amount=Decimal('7.98'),
            expense_date=date(2026, 5, 15),
            receipt_number='MAY-476-1',
            description='Mali trošak s pretporezom',
            created_by=cls.user,
        )

        generate_vat_ledger(cls.tenant, 2026, 5, replace=True)
        cls.period = VATPeriod.all_objects.get(tenant=cls.tenant, year=2026, month=5)
        cls.reference = parse_pdv_obrazac_xml(_REFERENCE_MAY_2026.read_bytes())

    def test_implemented_boxes_match_reference_xml(self):
        erp = aggregate_vat_boxes(self.period)

        for code in _IMPLEMENTED_PAIR_BOXES:
            with self.subTest(box=code):
                self.assertEqual(erp[code], _pair_to_totals(self.reference.field_pair(code)))

    def test_eu_boxes_match_reference_xml(self):
        erp = aggregate_vat_boxes(self.period)
        base_boxes = {'610', '612', '614'}
        vat_boxes = {'611', '613', '615'}
        for code in base_boxes:
            with self.subTest(box=code):
                self.assertEqual(erp[code].base, self.reference.field_scalar(code))
        for code in vat_boxes:
            with self.subTest(box=code):
                self.assertEqual(erp[code].vat, self.reference.field_scalar(code))

    def test_podatak_400_matches_reference_xml(self):
        erp = aggregate_vat_boxes(self.period)
        self.assertEqual(compute_vat_due(erp), self.reference.field_scalar('400'))
        self.assertEqual(compute_vat_due(erp), Decimal('-7.98'))

    def test_other_active_boxes_are_zero(self):
        erp = aggregate_vat_boxes(self.period)
        skip_pairs = set(_IMPLEMENTED_PAIR_BOXES) | {'200', '300'}
        skip_scalars = set(_EU_SCALAR_BOXES) | {'400'}
        skip_bools = {'660'}

        for definition in active_boxes():
            with self.subTest(box=definition.code):
                if definition.field_type == 'pair' and definition.code not in skip_pairs:
                    self.assertEqual(erp[definition.code], BoxTotals(Decimal('0.00'), Decimal('0.00')))
                elif definition.field_type == 'scalar' and definition.code not in skip_scalars:
                    self.assertEqual(self.reference.field_scalar(definition.code), Decimal('0.00'))
                elif definition.field_type == 'bool' and definition.code not in skip_bools:
                    self.assertFalse(self.reference.field_bool(definition.code))

    def test_reference_fixture_is_unsigned_with_zero_eu_boxes(self):
        xml_bytes = _REFERENCE_MAY_2026.read_bytes()
        self.assertNotIn(b'SignatureValue', xml_bytes)
        self.assertEqual(
            _pair_to_totals(self.reference.field_pair('303')),
            BoxTotals(Decimal('31.92'), Decimal('7.98')),
        )
        self.assertEqual(self.reference.field_scalar('400'), Decimal('-7.98'))
        for code in _EU_SCALAR_BOXES:
            self.assertEqual(self.reference.field_scalar(code), Decimal('0.00'))
