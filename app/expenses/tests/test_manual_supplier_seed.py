from django.test import TestCase

from expenses.data.manual_supplier_map import MANUAL_SUPPLIER_MAP
from expenses.services.manual_supplier_seed import seed_manual_suppliers
from partners.models import Partner
from tenants.models import Tenant


class ManualSupplierSeedTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug='manualco', name='Manual Co')

    def test_creates_manual_supplier_partners(self):
        result = seed_manual_suppliers(tenant=self.tenant)
        self.assertEqual(result.created, len(MANUAL_SUPPLIER_MAP))
        supplier = Partner.all_objects.get(tenant=self.tenant, tax_number='DE229674882')
        self.assertEqual(supplier.name, 'Automobile Hadžić')
        self.assertEqual(supplier.city, 'Gelsenkirchen')
        self.assertEqual(supplier.country_code, 'DE')
        self.assertEqual(supplier.partner_type, 'supplier')
        fzoeu = Partner.all_objects.get(tenant=self.tenant, tax_number='85828625994')
        self.assertEqual(fzoeu.name, 'Fond za zaštitu okoliša i energetsku učinkovitost')
        self.assertEqual(fzoeu.partner_type, 'supplier')
        self.assertEqual(fzoeu.status, 'active')
        self.assertIn('HR5424020061100971754', fzoeu.notes)

    def test_idempotent(self):
        seed_manual_suppliers(tenant=self.tenant)
        result = seed_manual_suppliers(tenant=self.tenant)
        self.assertEqual(result.created, 0)
        self.assertEqual(result.skipped, len(MANUAL_SUPPLIER_MAP))
        self.assertEqual(
            Partner.all_objects.filter(tenant=self.tenant).count(),
            len(MANUAL_SUPPLIER_MAP),
        )
