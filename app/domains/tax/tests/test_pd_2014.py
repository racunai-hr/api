"""PD 2014 iz knjige poklapa predano tijelo, uključujući kamatu iz 2013."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.services.chart import provision_tenant_chart
from accounting.services.manual_journal import JournalLineInput, create_manual_journal_entry
from banking.models import BankStatement, BankTransaction
from banking.services.hrk_statement_booking import book_hrk_statement_lines
from domains.tax.read.pd import pd_year_dto
from domains.tax.read.pd_lines import (
    PD_LINES,
    filed_body_2014,
    filed_body_2015,
    filed_body_2016,
    filed_body_2017,
    filed_body_2018,
    filed_body_2019,
    filed_body_2020,
    filed_body_2021,
    filed_body_2022,
)
from payments.models import BankAccount
from settings.models import CompanySettings, TaxOffice
from tenants.models import Tenant, TenantMembership

HOST = 'pd2014.racunai.hr'


def _row(body: dict, code: str) -> str:
    return next(row['amount'] for row in body['rows'] if row['code'] == code)


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class Pd2014FromLedgerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif = __import__(
            'accounting.services.rrif_import',
            fromlist=['import_rrif_chart'],
        ).import_rrif_chart
        import_rrif(clear=True)
        cls.tenant = Tenant.objects.create(slug='pd2014', name='FINE STAR d.o.o.')
        User = get_user_model()
        cls.user = User.objects.create_user(username='pd2014-accountant', password='test')
        TenantMembership.objects.create(user=cls.user, tenant=cls.tenant, role='accountant')
        provision_tenant_chart(cls.tenant)
        office, _ = TaxOffice.objects.get_or_create(
            code='3409',
            defaults={'name': 'Zagreb - Susedgrad', 'city': 'Zagreb'},
        )
        CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='FINE STAR d.o.o.',
            street='A.T.MIMARE',
            house_number='46',
            city='ZAGREB',
            vat_number='36619131370',
            tax_office=office,
        )
        cls.bank_account = BankAccount.all_objects.create(
            tenant=cls.tenant,
            account_name='Kuna',
            bank_name='OTP BANKA d.d.',
            account_number='1100396357',
            iban='HR5524070001100396357',
            currency='HRK',
        )
        statement_2013 = BankStatement.all_objects.create(
            tenant=cls.tenant,
            statement_number='2013',
            bank_account=cls.bank_account,
            statement_date=date(2013, 12, 31),
            currency='HRK',
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('0.00'),
            imported_by=cls.user,
        )
        statement_2014 = BankStatement.all_objects.create(
            tenant=cls.tenant,
            statement_number='2014',
            bank_account=cls.bank_account,
            statement_date=date(2014, 12, 31),
            currency='HRK',
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('0.00'),
            imported_by=cls.user,
        )
        cls._tx(
            statement_2013,
            on=date(2013, 12, 20),
            amount=Decimal('20000.00'),
            transaction_type='credit',
            description='POVRAT OSN.POL.UPL.06/12/13',
        )
        cls._tx(
            statement_2013,
            on=date(2013, 12, 31),
            amount=Decimal('0.60'),
            transaction_type='credit',
            description='PRIPIS KAMATE',
        )
        for on, amount in (
            (date(2014, 1, 2), Decimal('25.00')),
            (date(2014, 2, 3), Decimal('25.00')),
            (date(2014, 3, 3), Decimal('25.00')),
            (date(2014, 4, 1), Decimal('45.85')),
            (date(2014, 5, 2), Decimal('33.85')),
            (date(2014, 6, 2), Decimal('25.00')),
            (date(2014, 7, 1), Decimal('25.00')),
            (date(2014, 8, 1), Decimal('0.48')),
            (date(2014, 9, 30), Decimal('0.01')),
        ):
            cls._tx(
                statement_2014,
                on=on,
                amount=amount,
                transaction_type='debit',
                description='NAKNADA PLATNOG PROMETA',
            )
        for on, amount in (
            (date(2014, 3, 31), Decimal('4.35')),
            (date(2014, 6, 30), Decimal('0.23')),
            (date(2014, 9, 30), Decimal('0.01')),
        ):
            cls._tx(
                statement_2014,
                on=on,
                amount=amount,
                transaction_type='credit',
                description='PRIPIS KAMATE',
            )
        cls._tx(
            statement_2014,
            on=date(2014, 3, 4),
            amount=Decimal('1500.00'),
            transaction_type='debit',
            description='ATM GOTOVINA/03.03/HRK/1500,00',
        )
        book_hrk_statement_lines(cls.tenant, cls.user, cls.bank_account)

    @classmethod
    def _tx(cls, statement, *, on, amount, transaction_type, description):
        return BankTransaction.all_objects.create(
            tenant=cls.tenant,
            bank_statement=statement,
            transaction_date=on,
            amount=amount,
            currency='HRK',
            amount_eur=Decimal('1.00'),
            transaction_type=transaction_type,
            description=description,
        )

    def test_body_matches_filed_return_including_founding_interest(self):
        body = pd_year_dto(self.tenant, 2014)
        filed = filed_body_2014()

        self.assertEqual(body['currency'], 'HRK')
        self.assertEqual(body['founding_year'], 2013)
        self.assertEqual(body['header']['oib'], '36619131370')
        self.assertEqual(body['missing_hrk'], [])
        self.assertTrue(body['matches_filed'])
        self.assertEqual([row['code'] for row in body['rows']], [code for code, _label in PD_LINES])
        for row in body['rows']:
            self.assertEqual(row['amount'], filed[row['code']], row['code'])
            self.assertEqual(row['filed'], filed[row['code']])

        self.assertEqual(_row(body, '1'), '5.19')
        self.assertEqual(_row(body, '2'), '205.19')
        self.assertEqual(_row(body, '3'), '0.00')
        self.assertEqual(_row(body, '4'), '200.00')
        self.assertEqual(_row(body, '39'), '-200.00')
        self.assertEqual(_row(body, '41'), '200.00')
        self.assertEqual(_row(body, '43'), '20.00')
        self.assertEqual(_row(body, '44'), '0.00')

        self.assertEqual(len(body['founding_items']), 1)
        founding = body['founding_items'][0]
        self.assertEqual(founding['entry_date'], '2013-12-31')
        self.assertEqual(founding['account_code'], '7710')
        self.assertEqual(founding['amount'], '0.60')

    def test_year_api_returns_the_same_body(self):
        client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = HOST

        response = client.get('/api/tax/pd/years/2014/')
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body['matches_filed'])
        self.assertEqual(_row(body, '1'), '5.19')
        self.assertEqual(_row(body, '4'), '200.00')
        self.assertEqual(body['founding_items'][0]['amount'], '0.60')

        missing = client.get('/api/tax/pd/years/nope/')
        self.assertEqual(missing.status_code, 404)

    def test_empty_2015_matches_the_filed_zero_return(self):
        body = pd_year_dto(self.tenant, 2015)
        filed = filed_body_2015()

        self.assertEqual(body['currency'], 'HRK')
        self.assertIsNone(body['founding_year'])
        self.assertEqual(body['missing_hrk'], [])
        self.assertTrue(body['matches_filed'])
        for row in body['rows']:
            self.assertEqual(row['amount'], filed[row['code']], row['code'])
            self.assertEqual(row['filed'], filed[row['code']])
        self.assertEqual(_row(body, '1'), '0.00')
        self.assertEqual(_row(body, '2'), '0.00')
        self.assertEqual(_row(body, '43'), '20.00')
        self.assertEqual(_row(body, '44'), '0.00')

    def test_empty_2016_keeps_the_filed_loss_apart_from_the_ledger(self):
        body = pd_year_dto(self.tenant, 2016)
        filed = filed_body_2016()

        self.assertEqual(body['currency'], 'HRK')
        self.assertEqual(body['tax_rate'], '20.00')
        self.assertFalse(body['matches_filed'])
        self.assertEqual(_row(body, '2'), '0.00')
        filed_row = {row['code']: row['filed'] for row in body['rows']}
        self.assertEqual(filed_row['2'], filed['2'])
        self.assertEqual(filed_row['4'], '230.00')
        self.assertEqual(filed_row['39'], '-230.00')
        self.assertEqual(filed_row['40'], '200.00')
        self.assertEqual(filed_row['41'], '430.00')
        self.assertEqual(filed_row['43'], '20.00')

    def test_empty_2017_keeps_the_filed_reduced_rate_apart_from_the_ledger(self):
        body = pd_year_dto(self.tenant, 2017)
        filed = filed_body_2017()

        self.assertEqual(body['currency'], 'HRK')
        self.assertEqual(body['tax_rate'], '18.00')
        self.assertFalse(body['matches_filed'])
        self.assertEqual(_row(body, '2'), '0.00')
        filed_row = {row['code']: row['filed'] for row in body['rows']}
        self.assertEqual(filed_row['2'], filed['2'])
        self.assertEqual(filed_row['4'], '1129.00')
        self.assertEqual(filed_row['39'], '-1129.00')
        self.assertEqual(filed_row['40'], '430.00')
        self.assertEqual(filed_row['41'], '1559.00')
        self.assertEqual(filed_row['43'], '12.00')

    def test_empty_2018_keeps_the_filed_loss_apart_from_the_ledger(self):
        body = pd_year_dto(self.tenant, 2018)
        filed = filed_body_2018()

        self.assertEqual(body['currency'], 'HRK')
        self.assertEqual(body['tax_rate'], '18.00')
        self.assertFalse(body['matches_filed'])
        self.assertEqual(_row(body, '2'), '0.00')
        filed_row = {row['code']: row['filed'] for row in body['rows']}
        self.assertEqual(filed_row['2'], filed['2'])
        self.assertEqual(filed_row['4'], '230.00')
        self.assertEqual(filed_row['39'], '-230.00')
        self.assertEqual(filed_row['40'], '1559.00')
        self.assertEqual(filed_row['41'], '1789.00')
        self.assertEqual(filed_row['43'], '12.00')

    def test_empty_2019_keeps_the_filed_loss_apart_from_the_ledger(self):
        body = pd_year_dto(self.tenant, 2019)
        filed = filed_body_2019()

        self.assertEqual(body['currency'], 'HRK')
        self.assertEqual(body['tax_rate'], '18.00')
        self.assertFalse(body['matches_filed'])
        self.assertEqual(_row(body, '2'), '0.00')
        filed_row = {row['code']: row['filed'] for row in body['rows']}
        self.assertEqual(filed_row['2'], filed['2'])
        self.assertEqual(filed_row['4'], '230.00')
        self.assertEqual(filed_row['39'], '-230.00')
        self.assertEqual(filed_row['40'], '1789.00')
        self.assertEqual(filed_row['41'], '2019.00')
        self.assertEqual(filed_row['43'], '12.00')

    def test_empty_2020_keeps_the_filed_lipa_loss_apart_from_the_ledger(self):
        body = pd_year_dto(self.tenant, 2020)
        filed = filed_body_2020()

        self.assertEqual(body['currency'], 'HRK')
        self.assertEqual(body['tax_rate'], '18.00')
        self.assertFalse(body['matches_filed'])
        self.assertEqual(_row(body, '2'), '0.00')
        filed_row = {row['code']: row['filed'] for row in body['rows']}
        self.assertEqual(filed_row['2'], filed['2'])
        self.assertEqual(filed_row['4'], '0.56')
        self.assertEqual(filed_row['39'], '-0.56')
        self.assertEqual(filed_row['40'], '2019.00')
        self.assertEqual(filed_row['41'], '2019.56')
        self.assertEqual(filed_row['43'], '12.00')

    def test_empty_2021_keeps_the_filed_loss_and_the_ten_percent_rate(self):
        body = pd_year_dto(self.tenant, 2021)
        filed = filed_body_2021()

        self.assertEqual(body['currency'], 'HRK')
        self.assertEqual(body['tax_rate'], '18.00')
        self.assertFalse(body['matches_filed'])
        self.assertEqual(_row(body, '1'), '0.00')
        self.assertEqual(_row(body, '2'), '0.00')
        filed_row = {row['code']: row['filed'] for row in body['rows']}
        self.assertEqual(filed_row['1'], filed['1'])
        self.assertEqual(filed_row['2'], '12584.46')
        self.assertEqual(filed_row['4'], '8775.11')
        self.assertEqual(filed_row['39'], '-8775.11')
        self.assertEqual(filed_row['40'], '1590.00')
        self.assertEqual(filed_row['41'], '10365.11')
        self.assertEqual(filed_row['43'], '10.00')

    def test_empty_2022_keeps_the_filed_loss_the_fine_and_the_ten_percent_rate(self):
        body = pd_year_dto(self.tenant, 2022)
        filed = filed_body_2022()

        self.assertEqual(body['currency'], 'HRK')
        self.assertEqual(body['tax_rate'], '18.00')
        self.assertFalse(body['matches_filed'])
        self.assertEqual(_row(body, '1'), '0.00')
        filed_row = {row['code']: row['filed'] for row in body['rows']}
        self.assertEqual(filed_row['1'], filed['1'])
        self.assertEqual(filed_row['2'], '85528.73')
        self.assertEqual(filed_row['4'], '84806.75')
        self.assertEqual(filed_row['11'], '200.00')
        self.assertEqual(filed_row['26'], '200.00')
        self.assertEqual(filed_row['39'], '-84606.75')
        self.assertEqual(filed_row['40'], '8775.11')
        self.assertEqual(filed_row['41'], '93381.86')
        self.assertEqual(filed_row['43'], '10.00')

    def test_hrk_line_without_bank_transaction_stays_out_of_the_form(self):
        create_manual_journal_entry(
            self.tenant,
            self.user,
            entry_date=date(2015, 1, 15),
            description='Rashod bez bankovne stavke',
            lines=[
                JournalLineInput('4650', Decimal('10.00'), Decimal('0')),
                JournalLineInput('1000', Decimal('0'), Decimal('10.00')),
            ],
            post=True,
        )
        body = pd_year_dto(self.tenant, 2015)
        self.assertEqual(_row(body, '2'), '0.00')
        self.assertEqual(len(body['missing_hrk']), 1)
        self.assertEqual(body['missing_hrk'][0]['account_code'], '4650')
        self.assertEqual(
            body['missing_hrk'][0]['reason'],
            'Nema povezane bankovne stavke u kunama.',
        )
        self.assertFalse(body['matches_filed'])
