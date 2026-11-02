"""Fine Star 2014 quarterly nil PDV archive."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import SubmissionEvent, VATPeriod, VATReturnStatus
from accounting.services.tax_forms.pdv.import_paper import (
    FINESTAR_2014_H1,
    PaperPdvImportError,
    import_finestar_pdv_2014_h1,
)
from tenants.models import Tenant, TenantMembership

HOST = 'paperpdv.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class ImportPaperPdv2014Tests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug='paperpdv', name='Paper PDV')
        user = get_user_model().objects.create_user(username='paper-viewer', password='test')
        TenantMembership.objects.create(user=user, tenant=self.tenant, role='viewer')
        self.client = APIClient()
        token = RefreshToken.for_user(user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.client.defaults['HTTP_HOST'] = HOST

    def _pdf(self) -> Path:
        import tempfile

        if not hasattr(self, '_dir'):
            self._dir = tempfile.TemporaryDirectory()
            self.addCleanup(self._dir.cleanup)
        path = Path(self._dir.name) / 'pdv-2014.pdf'
        path.write_bytes(b'%PDF-1.4 paper')
        return path

    def test_imports_two_quarters_once(self):
        pdf = self._pdf()
        first = import_finestar_pdv_2014_h1(self.tenant, pdf)
        second = import_finestar_pdv_2014_h1(self.tenant, pdf)
        self.assertEqual(first, [('2014-01..2014-03', 'imported'), ('2014-04..2014-06', 'imported')])
        self.assertEqual(second, [('2014-01..2014-03', 'skipped'), ('2014-04..2014-06', 'skipped')])

        periods = list(
            VATPeriod.all_objects.filter(tenant=self.tenant, year=2014).order_by('month')
        )
        self.assertEqual([(p.month, p.period_to, p.status) for p in periods], [
            (1, date(2014, 3, 31), 'submitted'),
            (4, date(2014, 6, 30), 'submitted'),
        ])
        self.assertEqual(periods[0].submitted_at, FINESTAR_2014_H1[0].received_at)
        self.assertEqual(periods[1].submitted_at, FINESTAR_2014_H1[1].received_at)

        for period, quarter in zip(periods, FINESTAR_2014_H1, strict=True):
            vat_return = period.returns.get()
            self.assertEqual(vat_return.status, VATReturnStatus.IMPORTED)
            self.assertEqual(vat_return.schema_version, '2014-paper')
            self.assertEqual(vat_return.payload_snapshot['document_currency'], 'HRK')
            self.assertEqual(vat_return.payload_snapshot['document_vat_due'], '0.00')
            self.assertTrue(vat_return.payload_snapshot['nil_return'])
            event = SubmissionEvent.all_objects.get(object_id=vat_return.pk)
            self.assertEqual(event.external_identifier, quarter.external_identifier)
            self.assertTrue(event.confirmation_attachment)
            self.assertEqual(period.filing_bounds()[0], date(quarter.year, quarter.month, 1))
            self.assertEqual(period.filing_bounds()[1], quarter.period_to)

        listed = self.client.get('/api/tax/pdv/periods/').json()['results']
        by_period = {row['period']: row for row in listed}
        q1 = by_period['2014-01..2014-03']
        self.assertEqual(q1['period_from'], '2014-01-01')
        self.assertEqual(q1['period_to'], '2014-03-31')
        self.assertEqual(q1['period_status'], 'submitted')
        self.assertEqual(q1['return_status'], 'imported')
        self.assertEqual(q1['return_source'], 'imported')
        self.assertEqual(q1['vat_due'], '0.00')
        self.assertFalse(q1['has_ledger'])

        workspace = self.client.get('/api/tax/pdv/periods/2014-01..2014-03/').json()
        self.assertEqual(workspace['external_identifier'], '63765b90-3ffd-4cb7-9afa-185d2d125632')
        self.assertEqual(workspace['document_currency'], 'HRK')
        self.assertEqual(workspace['document_vat_due'], '0.00')
        self.assertTrue(workspace['has_confirmation'])
        download = self.client.get(
            f'/api/tax/submissions/{workspace["event_uuid"]}/confirmation/',
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, b'%PDF-1.4 paper')

    def test_monthly_bounds_stay_one_month(self):
        period = VATPeriod.all_objects.create(tenant=self.tenant, year=2026, month=2)
        self.assertEqual(period.filing_bounds(), (date(2026, 2, 1), date(2026, 2, 28)))

    def test_refuses_a_period_that_already_has_another_return(self):
        from django.core.files.base import ContentFile

        from accounting.models import VATReturn

        period = VATPeriod.all_objects.create(
            tenant=self.tenant,
            year=2014,
            month=1,
            period_to=date(2014, 3, 31),
            status='open',
        )
        VATReturn.all_objects.create(
            tenant=self.tenant,
            vat_period=period,
            version=1,
            status=VATReturnStatus.GENERATED,
            payload_snapshot={'fields': {}},
            payload_hash='c' * 64,
            payload_json=ContentFile(b'{}', name='v1.json'),
        )
        with self.assertRaises(PaperPdvImportError):
            import_finestar_pdv_2014_h1(self.tenant, self._pdf())

    def test_monthly_filing_sits_beside_the_quarter(self):
        from accounting.services.tax_forms.pdv.import_paper import (
            FINESTAR_PAPER_FILINGS,
            _import_quarter,
        )

        import_finestar_pdv_2014_h1(self.tenant, self._pdf())
        january = next(
            item
            for item in FINESTAR_PAPER_FILINGS
            if item.year == 2014 and item.month == 1 and item.period_to is None
        )
        self.assertEqual(
            _import_quarter(self.tenant, january, b'%PDF-1.4 month', 'jan.pdf'),
            'imported',
        )
        listed = self.client.get('/api/tax/pdv/periods/').json()['results']
        by_period = {row['period']: row for row in listed}
        self.assertEqual(by_period['2014-01']['period_from'], '2014-01-01')
        self.assertEqual(by_period['2014-01']['period_to'], '2014-01-31')
        self.assertEqual(by_period['2014-01..2014-03']['period_to'], '2014-03-31')
        month = self.client.get('/api/tax/pdv/periods/2014-01/').json()
        self.assertEqual(month['external_identifier'], '2c7f77cc-a46f-417c-968e-f1886d280302')
        self.assertEqual(month['document_currency'], 'HRK')

    def test_later_filing_with_a_different_amount_is_a_new_version(self):
        from datetime import datetime
        from uuid import UUID
        from zoneinfo import ZoneInfo

        from accounting.services.tax_forms.pdv.import_paper import PaperPdvQuarter, _import_quarter

        zagreb = ZoneInfo('Europe/Zagreb')
        first = PaperPdvQuarter(
            year=2022,
            month=2,
            period_to=None,
            received_at=datetime(2022, 3, 21, 14, 0, 0, tzinfo=zagreb),
            external_identifier=UUID('e8e81041-bc1f-480d-be7c-f664f58257bb'),
            document_vat_due='-23758.05',
            tax_office='Šibenik',
        )
        second = PaperPdvQuarter(
            year=2022,
            month=2,
            period_to=None,
            received_at=datetime(2022, 3, 25, 9, 6, 6, tzinfo=zagreb),
            external_identifier=UUID('407294af-15c8-418a-81f4-2803db588e28'),
            document_vat_due='-26740.53',
            tax_office='Šibenik',
        )
        self.assertEqual(_import_quarter(self.tenant, first, b'%PDF-1.4 a', 'pdv.pdf'), 'imported')
        self.assertEqual(_import_quarter(self.tenant, second, b'%PDF-1.4 b', 'pdv.pdf'), 'imported')
        self.assertEqual(_import_quarter(self.tenant, second, b'%PDF-1.4 b', 'pdv.pdf'), 'skipped')

        listed = self.client.get('/api/tax/pdv/periods/').json()['results']
        february = next(row for row in listed if row['period'] == '2022-02')
        self.assertEqual(february['document_vat_due'], '-26740.53')
        self.assertEqual(february['document_currency'], 'HRK')
        self.assertEqual(february['vat_due'], '0.00')

        workspace = self.client.get('/api/tax/pdv/periods/2022-02/').json()
        self.assertEqual(workspace['return_version'], 2)
        self.assertEqual(workspace['document_vat_due'], '-26740.53')
        self.assertEqual(workspace['document_currency'], 'HRK')
        self.assertEqual(workspace['external_identifier'], '407294af-15c8-418a-81f4-2803db588e28')
        self.assertEqual(len(workspace['forms']), 2)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2022, month=2)
        current = period.current_return
        self.assertEqual(current.payload_snapshot['tax_office'], 'Šibenik')
        self.assertEqual(current.payload_snapshot['taxpayer']['city'], 'Šibenik')
        self.assertEqual(period.returns.get(version=1).payload_snapshot['document_vat_due'], '-23758.05')
