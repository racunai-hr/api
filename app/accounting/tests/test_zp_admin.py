"""Tests for ZPReturn Django admin — list, draft action, XML export, mark submitted."""

from __future__ import annotations

from uuid import uuid4

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from accounting.admin import ZPReturnAdmin, ZPReturnInline, _zp_xml_export_link
from accounting.models import VATPeriod, ZPReturn
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.zp.submit import mark_zp_submitted
from accounting.services.tax_forms.zp.zp_returns import create_zp_return_draft
from accounting.tests.fixtures.tax.zp.load import load_scenario
from accounting.tests.test_zp_aggregate import _seed_ledger
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice
from tenants.models import Tenant

ADMIN_HOST = {'HTTP_HOST': 'admin.racunai.hr'}


class ZpReturnAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='zp-admin', name='ZP Admin Co')
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
        cls.accountant = ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Toni',
            last_name='Šupe',
        )
        User = get_user_model()
        cls.user = User.objects.create_superuser(
            username='zp-admin-user',
            email='zp@example.com',
            password='test',
        )

    def _period_with_ledger(self, scenario_id: str = 'single_eu_partner') -> VATPeriod:
        data = load_scenario(scenario_id)
        ledger = data['ledger']
        period_info = ledger['period']
        period = VATPeriod.all_objects.create(
            tenant=self.tenant,
            year=period_info['year'],
            month=period_info['month'],
        )
        _seed_ledger(tenant=self.tenant, period=period, ledger=ledger)
        return period

    def test_admin_list_shows_draft_and_export_link(self):
        period = self._period_with_ledger()
        zp_return = create_zp_return_draft(period, prepared_by=self.accountant)
        admin_site = AdminSite()
        zp_admin = ZPReturnAdmin(ZPReturn, admin_site)

        status = zp_admin.submission_status_display(zp_return)
        self.assertEqual(status, 'Draft')

        exports_html = str(zp_admin.exports_display(zp_return))
        self.assertIn('ZP XML', exports_html)
        self.assertIn('<a ', exports_html)

    def test_submission_filter_draft_vs_submitted(self):
        period = self._period_with_ledger()
        draft = create_zp_return_draft(period, prepared_by=self.accountant)
        submitted = create_zp_return_draft(period, prepared_by=self.accountant)
        from django.utils import timezone

        mark_zp_submitted(
            submitted,
            submitted_at=timezone.now(),
            eporezna_identifier=uuid4(),
            submitted_by=self.user,
            version_confirmed=True,
        )

        admin_site = AdminSite()
        zp_admin = ZPReturnAdmin(ZPReturn, admin_site)
        request = RequestFactory().get('/')
        request.user = self.user

        from accounting.admin import ZpReturnSubmissionFilter

        draft_filter = ZpReturnSubmissionFilter(
            request,
            {'submission_status': 'draft'},
            ZPReturn,
            zp_admin,
        )
        draft_qs = draft_filter.queryset(request, ZPReturn.all_objects.all())
        self.assertIn(draft, draft_qs)
        self.assertNotIn(submitted, draft_qs)

        submitted_filter = ZpReturnSubmissionFilter(
            request,
            {'submission_status': 'submitted'},
            ZPReturn,
            zp_admin,
        )
        submitted_qs = submitted_filter.queryset(request, ZPReturn.all_objects.all())
        self.assertIn(submitted, submitted_qs)
        self.assertNotIn(draft, submitted_qs)

    def test_generate_zp_draft_action_creates_new_version(self):
        period = self._period_with_ledger()
        v1 = create_zp_return_draft(period, prepared_by=self.accountant)
        admin_site = AdminSite()
        zp_admin = ZPReturnAdmin(ZPReturn, admin_site)
        request = RequestFactory().get('/')
        request.user = self.user
        from django.contrib.messages.storage.fallback import FallbackStorage

        request.session = 'session'
        request._messages = FallbackStorage(request)

        zp_admin.generate_zp_draft(request, ZPReturn.all_objects.filter(pk=v1.pk))

        v2 = ZPReturn.all_objects.filter(vat_period=period).order_by('-version').first()
        self.assertEqual(v2.version, 2)

    def test_zp_xml_export_view_serves_unsigned_xml(self):
        period = self._period_with_ledger()
        zp_return = create_zp_return_draft(period, prepared_by=self.accountant)
        client = Client()
        client.force_login(self.user)

        url = reverse('accounting:zp_xml_export', args=[period.pk])
        response = client.get(url, follow=True, **ADMIN_HOST)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/xml')
        self.assertIn(f'v{zp_return.version}', response['Content-Disposition'])

    def test_mark_submitted_admin_view(self):
        period = self._period_with_ledger()
        zp_return = create_zp_return_draft(period, prepared_by=self.accountant)
        admin_site = AdminSite()
        zp_admin = ZPReturnAdmin(ZPReturn, admin_site)
        request = RequestFactory().post(
            f'/admin/accounting/zpreturn/{zp_return.pk}/mark-submitted/',
            {
                'eporezna_identifier': str(uuid4()),
                'version_confirmed': 'on',
            },
        )
        request.user = self.user
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.middleware import SessionMiddleware

        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)

        response = zp_admin.mark_submitted_view(request, str(zp_return.pk))

        self.assertEqual(response.status_code, 302)
        zp_return.refresh_from_db()
        event = SubmissionService.current_submission(zp_return)
        self.assertIsNotNone(event)

    def test_vatperiod_mark_zp_submitted_view(self):
        period = self._period_with_ledger()
        create_zp_return_draft(period, prepared_by=self.accountant)
        from accounting.admin import VATPeriodAdmin

        period_admin = VATPeriodAdmin(VATPeriod, AdminSite())
        request = RequestFactory().post(
            f'/admin/accounting/vatperiod/{period.pk}/mark-zp-submitted/',
            {
                'eporezna_identifier': str(uuid4()),
                'version_confirmed': 'on',
            },
        )
        request.user = self.user
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.middleware import SessionMiddleware

        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)

        response = period_admin.mark_zp_submitted_view(request, str(period.pk))

        self.assertEqual(response.status_code, 302)
        period.refresh_from_db()
        self.assertEqual(period.status, 'submitted')

    def test_inline_exports_link(self):
        period = self._period_with_ledger()
        zp_return = create_zp_return_draft(period, prepared_by=self.accountant)
        inline = ZPReturnInline(ZPReturn, AdminSite())

        html = str(inline.exports_display(zp_return))
        self.assertIn('ZP XML', html)
        self.assertEqual(str(_zp_xml_export_link(zp_return)), html)
