import tempfile
from pathlib import Path

from django.test import TestCase

from expenses.models import ExpenseCategory
from expenses.tests.partner_helpers import create_supplier_partner
from partners.models import Partner
from expenses.services.f1_supplier_extract import extract_supplier_oibs, extract_supplier_oibs_from_paths
from expenses.services.supplier_seed import seed_suppliers_from_f1
from expenses.tests.test_f1_parser import SAMPLE_CSV
from tenants.models import Tenant


SEED_CSV = """\
Naziv poreznog obveznika: FINE STAR d.o.o.
OIB poreznog obveznika: 36619131370
Razdoblje: 01.04.2026. 00:00:00 - 30.04.2026. 23:59:00
Broj računa;Oznaka poslovnog prostora;Oznaka naplatnog uređaja;JIR;OIB izdavatelja računa;Datum i vrijeme izdavanja računa;Datum fiskalizacije;Osnovica 0%;Porez 0%;Osnovica 5%;Porez 5%;Osnovica 13%;Porez 13%;Osnovica 25%;Porez 25%;Ukupni iznos računa;Način plaćanja
865;PJ1;1;jir-hardsoft;63182808929;24.04.2026. 15:30:48;24.04.2026. 15:30:48;;;;;;;320,44;80,11;400,55;K
476;37;1;jir-links;32614011568;16.04.2026. 19:26:27;16.04.2026. 19:26:28;;;;;;;15,99;4,00;19,99;K
12721;34;341;jir-eplus;93923226222;04.05.2026. 18:07:02;04.05.2026. 18:07:13;;;;;;;6,32;1,58;7,90;K
21822;BB12;1;jir-ht;81793146560;04.06.2026. 11:50:55;04.06.2026. 11:50:56;;;;;;;3,20;0,80;4,00;K
"""


class F1SupplierExtractTests(TestCase):
    def test_extract_supplier_oibs_from_sample_csv(self):
        oibs = extract_supplier_oibs(SAMPLE_CSV)
        self.assertEqual(oibs, {'81793146560'})

    def test_extract_supplier_oibs_from_paths_aggregates_counts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = Path(tmp_dir) / 'f1.csv'
            csv_path.write_text(SEED_CSV, encoding='utf-8')
            counts = extract_supplier_oibs_from_paths([csv_path])

        self.assertEqual(counts['63182808929'], 1)
        self.assertEqual(counts['32614011568'], 1)
        self.assertEqual(counts['93923226222'], 1)
        self.assertEqual(counts['81793146560'], 1)
        self.assertEqual(len(counts), 4)


class SupplierSeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='seedco', name='Seed Co')
        cls.ht = create_supplier_partner(
            tenant=cls.tenant,
            name='Hrvatski Telekom d.d.',
            tax_number='81793146560',
            address='Radnička cesta 21',
            city='Zagreb',
            postal_code='10000',
        )

    def _write_csv(self, directory: Path) -> Path:
        csv_path = directory / 'seed.csv'
        csv_path.write_text(SEED_CSV, encoding='utf-8')
        return csv_path

    def test_dry_run_does_not_create_suppliers(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self._write_csv(Path(tmp_dir))
            before = Partner.all_objects.filter(tenant=self.tenant).count()

            result = seed_suppliers_from_f1(
                tenant=self.tenant,
                paths=[csv_path],
                dry_run=True,
            )

            self.assertEqual(result.created, 3)
            self.assertEqual(result.skipped, 1)
            self.assertEqual(result.updated, 0)
            self.assertEqual(Partner.all_objects.filter(tenant=self.tenant).count(), before)

    def test_seed_creates_three_and_skips_ht(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self._write_csv(Path(tmp_dir))

            result = seed_suppliers_from_f1(tenant=self.tenant, paths=[csv_path])

            self.assertEqual(result.created, 3)
            self.assertEqual(result.skipped, 1)
            self.assertEqual(result.updated, 0)
            self.assertEqual(result.missing_in_map, ())

            partners = Partner.all_objects.filter(tenant=self.tenant).order_by('tax_number')
            self.assertEqual(partners.count(), 4)

            hardsoft = partners.get(tax_number='63182808929')
            self.assertEqual(hardsoft.name, 'Hardsoft j.d.o.o.')
            self.assertEqual(hardsoft.city, 'Samobor')
            self.assertEqual(hardsoft.email, 'prodaja@hardsoft.hr')

            links = partners.get(tax_number='32614011568')
            self.assertEqual(links.name, 'LINKS d.o.o.')

            eplus = partners.get(tax_number='93923226222')
            self.assertEqual(eplus.name, 'E PLUS, d.o.o.')

            ht = partners.get(tax_number='81793146560')
            self.assertEqual(ht.pk, self.ht.pk)

    def test_unknown_oib_reported_in_missing_in_map(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv = SEED_CSV.replace('63182808929', '11111111111')
            csv_path = Path(tmp_dir) / 'unknown.csv'
            csv_path.write_text(csv, encoding='utf-8')

            result = seed_suppliers_from_f1(tenant=self.tenant, paths=[csv_path])

            self.assertIn('11111111111', result.missing_in_map)
            self.assertFalse(
                Partner.all_objects.filter(tenant=self.tenant, tax_number='11111111111').exists()
            )
