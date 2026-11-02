from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import (
    ChartOfAccounts,
    JournalEntry,
    JournalEntryLine,
    VATLedgerEntry,
    VATPeriod,
)
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes, compute_vat_due
from accounting.services.tax_forms.pdv_s.aggregate import aggregate_pdv_s_rows
from accounting.services.tax_projection.apply import apply_vat_projection
from accounting.services.tax_projection.contracts import VatProjectionStatus
from accounting.services.tax_projection.prepare import prepare_vat_projection
from expenses.models import Expense, ExpenseCategory
from partners.models import Partner
from settings.models import CompanySettings, VatRegistrationStatus
from tenants.models import Tenant


class PdvSEuServicesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        User = get_user_model()
        cls.user = User.objects.create_user(username='pdvs-eu', password='test')

    def _tenant(self, slug, *, status, vat_id=''):
        tenant = Tenant.objects.create(slug=slug, name=slug)
        provision_tenant_chart(tenant)
        CompanySettings.all_objects.create(
            tenant=tenant,
            company_name=slug,
            company_address='Ulica 1\n22211 Vodice',
            street='Ulica',
            house_number='1',
            postal_code='22211',
            city='Vodice',
            country='HR',
            company_phone='1',
            company_email=f'{slug}@test.hr',
            vat_number='20501805574',
            vat_id=vat_id,
            vat_registration_status=status,
        )
        return tenant

    def _booking_partner(self, tenant):
        return Partner.all_objects.create(
            tenant=tenant,
            name='Booking.com B.V.',
            partner_type='supplier',
            status='active',
            country_code='NL',
            tax_number='',
            vat_number='NL805734958B01',
            address='Herengracht',
            city='Amsterdam',
            postal_code='1017',
        )

    def _expense(self, tenant, partner, *, number, amount, description, day=15):
        category = ExpenseCategory.all_objects.get_or_create(
            tenant=tenant, name='Provizija', defaults={},
        )[0]
        return Expense.all_objects.create(
            tenant=tenant,
            expense_number=number,
            status='approved',
            category=category,
            supplier=partner,
            amount=amount,
            tax_amount=Decimal('0.00'),
            expense_date=date(2026, 7, day),
            description=description,
            created_by=self.user,
        )

    def _apply(self, tenant, *, year=2026, month=7):
        period, _ = VATPeriod.all_objects.get_or_create(
            tenant=tenant, year=year, month=month, defaults={'status': 'open'},
        )
        candidate = prepare_vat_projection(period)
        self.assertEqual(candidate.status, VatProjectionStatus.READY, candidate.primary_rejection_code)
        apply_vat_projection(period, candidate, self.user)
        return period

    def test_vat_id_eu_service_pdv_and_pdvs(self):
        tenant = self._tenant(
            'pdvs-vat-id',
            status=VatRegistrationStatus.VAT_ID,
            vat_id='HR20501805574',
        )
        partner = self._booking_partner(tenant)
        self._expense(
            tenant,
            partner,
            number='BK-001',
            amount=Decimal('550.95'),
            description='Booking.com commission + service fee',
        )
        period = self._apply(tenant)
        boxes = aggregate_vat_boxes(period)
        self.assertEqual(boxes['210'].base, Decimal('550.95'))
        self.assertEqual(boxes['210'].vat, Decimal('137.74'))
        self.assertEqual(boxes['306'].base, Decimal('0.00'))
        self.assertEqual(boxes['306'].vat, Decimal('0.00'))
        self.assertEqual(compute_vat_due(boxes), Decimal('137.74'))

        payload = aggregate_pdv_s_rows(period)
        self.assertEqual(len(payload.rows), 1)
        row = payload.rows[0]
        self.assertEqual(row.country_code, 'NL')
        self.assertEqual(row.pdv_id, '805734958B01')
        self.assertEqual(row.services_value, Decimal('550.95'))
        self.assertEqual(row.goods_value, Decimal('0.00'))

        ledger = VATLedgerEntry.all_objects.get(vat_period=period, vat_box='210')
        self.assertEqual(ledger.partner_oib, 'NL805734958B01')

    def test_registered_eu_service_keeps_pretporez(self):
        tenant = self._tenant(
            'pdvs-registered',
            status=VatRegistrationStatus.REGISTERED,
            vat_id='HR20501805574',
        )
        partner = self._booking_partner(tenant)
        self._expense(
            tenant,
            partner,
            number='BK-002',
            amount=Decimal('550.95'),
            description='Booking.com commission + service fee',
        )
        period = self._apply(tenant)
        boxes = aggregate_vat_boxes(period)
        self.assertEqual(boxes['210'].base, Decimal('550.95'))
        self.assertEqual(boxes['210'].vat, Decimal('137.74'))
        self.assertEqual(boxes['306'].base, Decimal('550.95'))
        self.assertEqual(boxes['306'].vat, Decimal('137.74'))
        self.assertEqual(compute_vat_due(boxes), Decimal('0.00'))

    def test_eu_goods_acquisition_still_feeds_pdvs_goods(self):
        tenant = self._tenant(
            'pdvs-goods',
            status=VatRegistrationStatus.REGISTERED,
            vat_id='HR20501805574',
        )
        partner = Partner.all_objects.create(
            tenant=tenant,
            name='SaM Automobile',
            partner_type='supplier',
            status='active',
            country_code='DE',
            tax_number='',
            vat_number='DE355497142',
            address='',
            city='',
            postal_code='',
        )
        self._expense(
            tenant,
            partner,
            number='CAR-001',
            amount=Decimal('33000.00'),
            description='Audi A8 Lang 50 TDI WAUZZZF86RN003268',
        )
        period = self._apply(tenant)
        boxes = aggregate_vat_boxes(period)
        self.assertEqual(boxes['207'].base, Decimal('33000.00'))
        self.assertGreater(boxes['207'].vat, Decimal('0.00'))
        payload = aggregate_pdv_s_rows(period)
        self.assertEqual(len(payload.rows), 1)
        self.assertEqual(payload.rows[0].country_code, 'DE')
        self.assertEqual(payload.rows[0].pdv_id, '355497142')
        self.assertEqual(payload.rows[0].goods_value, Decimal('33000.00'))
        self.assertEqual(payload.rows[0].services_value, Decimal('0.00'))

    def test_viii1_box_612_is_not_pdvs_service(self):
        tenant = self._tenant(
            'pdvs-viii1',
            status=VatRegistrationStatus.REGISTERED,
            vat_id='HR20501805574',
        )
        period, _ = VATPeriod.all_objects.get_or_create(
            tenant=tenant, year=2026, month=7, defaults={'status': 'open'},
        )
        VATLedgerEntry.all_objects.create(
            tenant=tenant,
            vat_period=period,
            ledger_type=VATLedgerEntry.LEDGER_U_RA,
            entry_date=date(2026, 7, 1),
            document_number='VIII1-CAR',
            partner_name='Auto',
            partner_oib='DE355497142',
            base_amount=Decimal('23882.35'),
            vat_rate=Decimal('0.00'),
            vat_amount=Decimal('0.00'),
            vat_box='612',
            is_manual=True,
        )
        payload = aggregate_pdv_s_rows(period)
        self.assertEqual(payload.rows, ())

    def test_journal_032_still_classifies_612_not_pdvs(self):
        tenant = self._tenant(
            'pdvs-je-612',
            status=VatRegistrationStatus.REGISTERED,
            vat_id='HR20501805574',
        )
        provision_tenant_chart(tenant)
        account = ChartOfAccounts.all_objects.get(tenant=tenant, account_code='032001')
        entry = JournalEntry.all_objects.create(
            tenant=tenant,
            entry_number='202607-032',
            entry_date=date(2026, 7, 3),
            status='posted',
            description='Nabava auta VIII.1',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=account,
            debit_amount=Decimal('1000.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=ChartOfAccounts.all_objects.get(tenant=tenant, account_code='1000'),
            credit_amount=Decimal('1000.00'),
        )
        period = self._apply(tenant)
        boxes = aggregate_vat_boxes(period)
        self.assertEqual(boxes['612'].base, Decimal('1000.00'))
        payload = aggregate_pdv_s_rows(period)
        self.assertEqual(payload.rows, ())
