from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from accounting.services.fx import resolve_fx_rate, to_eur_amount


class HrkStatutoryRateTests(SimpleTestCase):
    def test_hrk_rate_is_fixed_and_ignores_manual_input(self):
        rate = resolve_fx_rate(None, 'HRK', date(2022, 10, 5), manual_rate='8')
        self.assertIsNotNone(rate)
        self.assertEqual(rate.rate, Decimal('7.53450'))
        self.assertEqual(rate.source, 'statutory')
        self.assertEqual(rate.rate_date, date(2022, 10, 5))

    def test_eur_stays_one(self):
        rate = resolve_fx_rate(None, 'EUR', date(2026, 8, 1))
        self.assertEqual(rate.rate, Decimal('1'))
        self.assertEqual(rate.source, 'manual')

    def test_other_currencies_still_need_a_manual_rate(self):
        self.assertIsNone(resolve_fx_rate(None, 'USD', date(2026, 8, 1)))
        manual = resolve_fx_rate(None, 'USD', date(2026, 8, 1), manual_rate='1.08')
        self.assertEqual(manual.rate, Decimal('1.08'))
        self.assertEqual(manual.source, 'manual')

    def test_hrk_converts_half_up_and_eur_is_unchanged(self):
        self.assertEqual(to_eur_amount(Decimal('3180.00'), 'HRK'), Decimal('422.06'))
        self.assertEqual(to_eur_amount(Decimal('81.44'), 'hrk'), Decimal('10.81'))
        self.assertEqual(to_eur_amount(Decimal('28.56'), 'HRK'), Decimal('3.79'))
        self.assertEqual(to_eur_amount(Decimal('100.50'), 'EUR'), Decimal('100.50'))
        self.assertIsNone(to_eur_amount(Decimal('10.00'), 'USD'))

    def test_kuna_invoice_books_in_eur_at_the_fixed_rate(self):
        from types import SimpleNamespace

        from accounting.services.posting import _expense_net_amount, _get_amount
        from domains.reporting.documents.projection import book_eur_amount

        invoice = SimpleNamespace(
            amount=Decimal('1800.00'),
            tax_amount=Decimal('360.00'),
            currency='HRK',
            foreign_vat_amount=None,
            foreign_vat_country='',
        )
        self.assertEqual(book_eur_amount(invoice), Decimal('238.90'))
        self.assertEqual(_get_amount(invoice, 'total_amount'), Decimal('238.90'))
        self.assertEqual(_get_amount(invoice, 'tax_amount'), Decimal('47.78'))
        self.assertEqual(_expense_net_amount(invoice), Decimal('191.12'))
