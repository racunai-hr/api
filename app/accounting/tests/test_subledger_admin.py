"""Django admin — SubledgerItem list/filter, inline, aging izvještaj."""

from datetime import date
from decimal import Decimal

from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase, override_settings

from accounting.admin_subledger import OverdueListFilter, SubledgerItemAdmin, SubledgerSourceInline
from accounting.models import SubledgerItem
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules, post_document
from accounting.services.rrif_import import import_rrif_chart
from expenses.admin import ExpenseAdmin
from expenses.models import Expense, ExpenseCategory
from invoices.admin import InvoiceAdmin
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from tenants.models import Tenant


def _request_with_tenant(factory, tenant, user, path='/'):
    request = factory.get(path)
    request.user = user
    request.tenant = tenant
    request.is_platform_admin = False
    middleware = SessionMiddleware(lambda req: None)
    middleware.process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)
    return request


@override_settings(ALLOWED_HOSTS=['testserver'])
class SubledgerAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='admin-subledger', name='Admin Subledger Co')
        cls.other_tenant = Tenant.objects.create(slug='other-subledger', name='Other Co')
        provision_tenant_chart(cls.tenant)
        provision_tenant_chart(cls.other_tenant)
        ensure_default_posting_rules(cls.tenant)
        ensure_default_posting_rules(cls.other_tenant)
        User = get_user_model()
        cls.user = User.objects.create_superuser(username='subledger-admin', password='test')
        cls.customer = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Kupac admin',
            tax_number='11111111111',
            partner_type='customer',
            status='active',
            address='Ulica 1',
            city='Split',
            postal_code='21000',
        )
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Dobavljač admin',
            tax_number='22222222222',
            partner_type='supplier',
            status='active',
            address='Ulica 2',
            city='Zagreb',
            postal_code='10000',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

    def setUp(self):
        self.factory = RequestFactory()
        self.admin = SubledgerItemAdmin(SubledgerItem, admin.site)

    def _create_invoice(self, due_date=date(2026, 1, 1)):
        invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            company_to=self.customer,
            issue_date=date(2026, 1, 15),
            due_date=due_date,
            status='sent',
            subtotal=Decimal('100.00'),
            tax_amount=Decimal('25.00'),
            total_amount=Decimal('125.00'),
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name='Usluga',
            quantity=1,
            unit_price=Decimal('100.00'),
            tax_rate=Decimal('25.00'),
        )
        post_document(self.tenant, invoice, 'invoice_issued', self.user)
        return invoice

    def test_subledger_item_registered_in_admin(self):
        self.assertTrue(admin.site.is_registered(SubledgerItem))

    def test_changelist_scoped_to_tenant(self):
        invoice = self._create_invoice()
        item = SubledgerItem.all_objects.get(source_object_id=invoice.pk)
        request = _request_with_tenant(
            self.factory,
            self.tenant,
            self.user,
            '/admin/accounting/subledgeritem/',
        )
        changelist = self.admin.get_changelist_instance(request)
        qs = changelist.get_queryset(request)
        self.assertEqual(list(qs.values_list('pk', flat=True)), [item.pk])

    @patch('accounting.admin_subledger.timezone.localdate', return_value=date(2026, 6, 1))
    def test_overdue_filter_returns_overdue_open_items(self, _mock_today):
        overdue_invoice = self._create_invoice(due_date=date(2020, 1, 1))
        current_invoice = self._create_invoice(due_date=date(2099, 12, 31))
        overdue_item = SubledgerItem.all_objects.get(source_object_id=overdue_invoice.pk)
        current_item = SubledgerItem.all_objects.get(source_object_id=current_invoice.pk)
        self.assertLess(overdue_item.due_date, date(2026, 6, 1))
        self.assertGreater(current_item.due_date, date(2026, 6, 1))

        request = _request_with_tenant(self.factory, self.tenant, self.user)
        flt = OverdueListFilter(request, {'overdue': 'yes'}, SubledgerItem, self.admin)
        scoped_qs = SubledgerItem.all_objects.filter(pk__in=[overdue_item.pk, current_item.pk])
        filtered = list(flt.queryset(request, scoped_qs))
        self.assertEqual([item.pk for item in filtered], [overdue_item.pk])

    def test_aging_report_view_renders_open_items(self):
        invoice = self._create_invoice(due_date=date(2026, 1, 1))
        request = _request_with_tenant(
            self.factory,
            self.tenant,
            self.user,
            '/admin/accounting/subledgeritem/aging-report/',
        )
        response = self.admin.aging_report_view(request)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Saldakonti — aging izvještaj', content)
        self.assertIn(self.customer.name, content)
        self.assertIn(invoice.invoice_number or 'Račun', content)

    def test_aging_export_returns_xlsx(self):
        self._create_invoice()
        request = _request_with_tenant(
            self.factory,
            self.tenant,
            self.user,
            '/admin/accounting/subledgeritem/aging-report/export/',
        )
        response = self.admin.aging_export_view(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            response['Content-Type'],
        )
        self.assertTrue(response.content.startswith(b'PK'))

    def test_invoice_admin_has_subledger_inline(self):
        invoice_admin = InvoiceAdmin(Invoice, admin.site)
        inline_models = [inline.model for inline in invoice_admin.inlines]
        self.assertIn(SubledgerSourceInline, invoice_admin.inlines)
        self.assertEqual(inline_models, [inline.model for inline in invoice_admin.inlines])

    def test_expense_admin_has_subledger_inline(self):
        expense_admin = ExpenseAdmin(Expense, admin.site)
        self.assertIn(SubledgerSourceInline, expense_admin.inlines)

    def test_subledger_inline_readonly_without_add(self):
        invoice = self._create_invoice()
        request = _request_with_tenant(self.factory, self.tenant, self.user)
        inline = SubledgerSourceInline(Invoice, admin.site)
        self.assertFalse(inline.has_add_permission(request, invoice))
        self.assertIn('open_amount', inline.readonly_fields)

    def test_changelist_includes_aging_report_link(self):
        self._create_invoice()
        request = _request_with_tenant(
            self.factory,
            self.tenant,
            self.user,
            '/admin/accounting/subledgeritem/',
        )
        response = self.admin.changelist_view(request)
        response.render()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Aging izvje', response.content)
