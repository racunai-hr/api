from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounting.models import ChartOfAccounts, PostingRule
from accounting.services.rrif_import import import_rrif_chart
from settings.models import CompanySettings, TaxOffice, VatRegistrationStatus
from tenants.models import Tenant
from tenants.services.provisioning import TenantProvisionSpec, provision_tenant


ALMA_OIB = '20501805574'
FINE_STAR_OIB = '36619131370'


def _spec(**overrides) -> TenantProvisionSpec:
    values = dict(
        slug='alma-cizmic-test',
        name='Alma Cizmić',
        oib=ALMA_OIB,
        street='Ulica bribirskih knezova',
        house_number='9',
        postal_code='22211',
        city='Vodice',
        email='almacizmic673@gmail.com',
        vat_status=VatRegistrationStatus.VAT_ID,
        vat_id='HR20501805574',
        vat_year=2026,
        vat_month=7,
    )
    values.update(overrides)
    return TenantProvisionSpec(**values)


class ProvisionTenantCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava, Područni ured', 'city': 'Šibenik'},
        )

    def _call(self, *args, **kwargs):
        out = StringIO()
        call_command('provision_tenant', *args, stdout=out, **kwargs)
        return out.getvalue()

    def _args(self, **overrides):
        args = {
            'slug': 'alma-cizmic-test',
            'name': 'Alma Cizmić',
            'oib': ALMA_OIB,
            'vat_id': 'HR20501805574',
            'vat_status': 'vat_id',
            'street': 'Ulica bribirskih knezova',
            'house_number': '9',
            'postal_code': '22211',
            'city': 'Vodice',
            'email': 'almacizmic673@gmail.com',
            'vat_year': 2026,
            'vat_month': 7,
        }
        args.update(overrides)
        return args

    def test_dry_run_writes_nothing(self):
        before = Tenant.objects.count()
        out = self._call(**self._args(), dry_run=True)
        self.assertIn('dry-run', out)
        self.assertEqual(Tenant.objects.count(), before)
        self.assertFalse(Tenant.objects.filter(slug='alma-cizmic-test').exists())

    def test_vat_id_mode_stores_oib_and_pdv_id_separately(self):
        self._call(**self._args())
        tenant = Tenant.objects.get(slug='alma-cizmic-test')
        company = CompanySettings.all_objects.get(tenant=tenant)
        self.assertEqual(company.vat_number, ALMA_OIB)
        self.assertEqual(company.vat_id, 'HR20501805574')
        self.assertEqual(company.vat_registration_status, VatRegistrationStatus.VAT_ID)
        self.assertFalse(company.input_vat_deductible)
        self.assertGreater(ChartOfAccounts.all_objects.filter(tenant=tenant).count(), 0)
        self.assertGreater(PostingRule.all_objects.filter(tenant=tenant).count(), 0)

    def test_none_mode_forbids_vat_id(self):
        with self.assertRaises(CommandError):
            self._call(**self._args(vat_status='none'))

    def test_none_mode_without_vat_id(self):
        self._call(**self._args(vat_status='none', vat_id=''))
        company = CompanySettings.all_objects.get(tenant__slug='alma-cizmic-test')
        self.assertEqual(company.vat_registration_status, VatRegistrationStatus.NONE)
        self.assertEqual(company.vat_id, '')
        self.assertEqual(company.vat_number, ALMA_OIB)

    def test_registered_mode_requires_vat_id(self):
        self._call(**self._args(vat_status='registered', slug='reg-tenant-test'))
        company = CompanySettings.all_objects.get(tenant__slug='reg-tenant-test')
        self.assertEqual(company.vat_registration_status, VatRegistrationStatus.REGISTERED)
        self.assertEqual(company.vat_id, 'HR20501805574')
        self.assertTrue(company.input_vat_deductible)

    def test_identical_second_run_is_noop(self):
        self._call(**self._args())
        tenant = Tenant.objects.get(slug='alma-cizmic-test')
        accounts = ChartOfAccounts.all_objects.filter(tenant=tenant).count()
        rules = PostingRule.all_objects.filter(tenant=tenant).count()
        out = self._call(**self._args())
        self.assertIn('bez izmjena', out)
        self.assertEqual(ChartOfAccounts.all_objects.filter(tenant=tenant).count(), accounts)
        self.assertEqual(PostingRule.all_objects.filter(tenant=tenant).count(), rules)
        self.assertEqual(CompanySettings.all_objects.filter(tenant=tenant).count(), 1)

    def test_conflict_on_different_oib(self):
        self._call(**self._args())
        with self.assertRaises(CommandError):
            self._call(**self._args(oib=FINE_STAR_OIB, vat_id=f'HR{FINE_STAR_OIB}'))
        company = CompanySettings.all_objects.get(tenant__slug='alma-cizmic-test')
        self.assertEqual(company.vat_number, ALMA_OIB)

    def test_oib_already_used_by_other_tenant(self):
        self._call(**self._args())
        with self.assertRaises(CommandError):
            self._call(**self._args(slug='other-alma', name='Other'))
        self.assertFalse(Tenant.objects.filter(slug='other-alma').exists())

    def test_unknown_tax_office_fails_without_creating(self):
        before = Tenant.objects.count()
        with self.assertRaises(CommandError):
            self._call(**self._args(tax_office_code='9999'))
        self.assertEqual(Tenant.objects.count(), before)

    def test_rollback_when_vat_period_fails(self):
        with patch(
            'tenants.services.provisioning.get_or_create_vat_period',
            side_effect=RuntimeError('period-boom'),
        ):
            with self.assertRaises(RuntimeError):
                provision_tenant(_spec())
        self.assertFalse(Tenant.objects.filter(slug='alma-cizmic-test').exists())

    def test_owner_user_must_exist(self):
        with self.assertRaises(CommandError):
            self._call(**self._args(owner_username='missing-owner'))
        self.assertFalse(Tenant.objects.filter(slug='alma-cizmic-test').exists())

    def test_owner_membership_created(self):
        User.objects.create_user('alma-owner', password='test')
        self._call(**self._args(owner_username='alma-owner'))
        tenant = Tenant.objects.get(slug='alma-cizmic-test')
        self.assertTrue(tenant.memberships.filter(user__username='alma-owner', role='owner').exists())
