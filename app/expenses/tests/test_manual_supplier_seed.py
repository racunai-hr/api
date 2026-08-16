from django.test import TestCase

from expenses.services.manual_supplier_seed import seed_manual_suppliers
from partners.models import Partner
from tenants.models import Tenant


class ManualSupplierSeedTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug='manualco', name='Manual Co')

    def test_creates_manual_supplier_partners(self):
        result = seed_manual_suppliers(tenant=self.tenant)
        self.assertEqual(result.created, 2)
        supplier = Partner.all_objects.get(tenant=self.tenant, tax_number='DE229674882')
        self.assertEqual(supplier.name, 'Automobile Hadžić')
        self.assertEqual(supplier.city, 'Gelsenkirchen')
        self.assertEqual(supplier.country, 'Germany')
        self.assertEqual(supplier.partner_type, 'supplier')

    def test_idempotent(self):
        seed_manual_suppliers(tenant=self.tenant)
        result = seed_manual_suppliers(tenant=self.tenant)
        self.assertEqual(result.created, 0)
        self.assertEqual(result.skipped, 2)
        self.assertEqual(Partner.all_objects.filter(tenant=self.tenant).count(), 2)
