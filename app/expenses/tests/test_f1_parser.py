from decimal import Decimal

from django.test import TestCase

from expenses.parsers.f1_csv import F1CsvParser


SAMPLE_CSV = """\
Naziv poreznog obveznika: FINE STAR d.o.o. za usluge
OIB poreznog obveznika: 36619131370
Razdoblje: 01.06.2026. 00:00:00 - 30.06.2026. 23:59:00
Broj računa;Oznaka poslovnog prostora;Oznaka naplatnog uređaja;JIR;OIB izdavatelja računa;Datum i vrijeme izdavanja računa;Datum fiskalizacije;Osnovica 0%;Porez 0%;Osnovica 5%;Porez 5%;Osnovica 13%;Porez 13%;Osnovica 25%;Porez 25%;Ukupni iznos računa;Način plaćanja
21822;BB12;1;a4e7eb63-19c3-414c-b863-1dbd9f5ac5d3;81793146560;04.06.2026. 11:50:55;04.06.2026. 11:50:56;;;;;;;3,20;0,80;4,00;K
21238;BB12;1;baaae6a5-f5b4-4792-9462-3a0919705443;81793146560;01.06.2026. 07:57:21;01.06.2026. 07:57:22;;;;;;;4,80;1,20;6,00;G
"""


class F1CsvParserTests(TestCase):
    def test_parses_rows_and_metadata(self):
        result = F1CsvParser().parse(SAMPLE_CSV, filename='f1.csv')

        self.assertEqual(result.file_metadata['taxpayer_oib'], '36619131370')
        self.assertIn('csv_period', result.file_metadata)
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.parse_errors, ())

        first = result.rows[0]
        self.assertEqual(first.receipt_number, '21822-BB12-1')
        self.assertEqual(first.external_id, 'a4e7eb63-19c3-414c-b863-1dbd9f5ac5d3')
        self.assertEqual(first.supplier_oib, '81793146560')
        self.assertEqual(first.amount, Decimal('4.00'))
        self.assertEqual(first.tax_amount, Decimal('0.80'))
        self.assertEqual(first.payment_method, 'card')
        self.assertEqual(first.source, 'f1_csv')

        second = result.rows[1]
        self.assertEqual(second.payment_method, 'cash')

    def test_strips_zero_width_characters(self):
        zwsp = '\u200b'
        csv = SAMPLE_CSV.replace('21822', f'{zwsp}21822')
        result = F1CsvParser().parse(csv)
        self.assertEqual(result.rows[0].receipt_number, '21822-BB12-1')

    def test_invalid_date_produces_parse_error(self):
        csv = SAMPLE_CSV.replace('04.06.2026. 11:50:55', 'invalid-date')
        result = F1CsvParser().parse(csv)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(len(result.parse_errors), 1)
        self.assertIn('Neispravan datum', result.parse_errors[0].message)
