"""Tests for create_zp_return_draft — validation, storage, atomicity."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.test import TestCase

from accounting.models import VATPeriod, ZPReturn
from accounting.services.tax_forms.zp.build import build_zp_payload
from accounting.services.tax_forms.zp.canonical import canonical_json, payload_hash
from accounting.services.tax_forms.zp.payload import ZP_MAPPING_VERSION, ZP_SCHEMA_VERSION
from accounting.services.tax_forms.zp.validation import ZpSchemaValidationError, validate_zp_xml
from accounting.services.tax_forms.zp.zp_returns import create_zp_return_draft
from accounting.tests.fixtures.tax.zp.load import load_scenario
from accounting.tests.test_zp_aggregate import _seed_ledger
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice
from tenants.models import Tenant


class CreateZpReturnDraftTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='zp-draft', name='ZP Draft Co')
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

    def _period_for_scenario(self, scenario_id: str) -> tuple[VATPeriod, dict]:
        data = load_scenario(scenario_id)
        ledger = data['ledger']
        period_info = ledger['period']
        period = VATPeriod.all_objects.create(
            tenant=self.tenant,
            year=period_info['year'],
            month=period_info['month'],
        )
        _seed_ledger(tenant=self.tenant, period=period, ledger=ledger)
        return period, data

    def test_valid_pipeline_creates_version_with_files(self):
        period, _ = self._period_for_scenario('single_eu_partner')
        zp_return = create_zp_return_draft(period, prepared_by=self.accountant)

        self.assertEqual(zp_return.version, 1)
        self.assertEqual(zp_return.schema_version, ZP_SCHEMA_VERSION)
        self.assertEqual(zp_return.mapping_version, ZP_MAPPING_VERSION)
        self.assertEqual(zp_return.prepared_by_id, self.accountant.pk)
        self.assertEqual(len(zp_return.payload_hash), 64)

        self.assertTrue(default_storage.exists(zp_return.payload_json.name))
        self.assertTrue(default_storage.exists(zp_return.xml_unsigned.name))

        stored_payload = json.loads(default_storage.open(zp_return.payload_json.name).read().decode('utf-8'))
        built = build_zp_payload(period)
        self.assertEqual(stored_payload, json.loads(canonical_json(built)))
        self.assertEqual(zp_return.payload_hash, payload_hash(built))

        xml_bytes = default_storage.open(zp_return.xml_unsigned.name).read()
        validate_zp_xml(xml_bytes)
        self.assertEqual(
            zp_return.unsigned_xml_sha256,
            hashlib.sha256(xml_bytes).hexdigest(),
        )

        expected_base = f'zp_returns/{self.tenant.slug}/2026/06/v1/'
        self.assertTrue(zp_return.payload_json.name.startswith(expected_base))
        self.assertTrue(zp_return.xml_unsigned.name.startswith(expected_base))

    def test_second_draft_increments_version(self):
        period, _ = self._period_for_scenario('single_eu_partner')
        first = create_zp_return_draft(period)
        second = create_zp_return_draft(period)
        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        self.assertEqual(ZPReturn.all_objects.filter(vat_period=period).count(), 2)

    @patch('accounting.services.tax_forms.zp.zp_returns.validate_zp_xml')
    def test_invalid_xml_raises_and_creates_no_record(self, mock_validate):
        period, _ = self._period_for_scenario('single_eu_partner')
        mock_validate.side_effect = ZpSchemaValidationError('XSD validation failed')

        with self.assertRaises(ZpSchemaValidationError):
            create_zp_return_draft(period)

        self.assertEqual(ZPReturn.all_objects.filter(vat_period=period).count(), 0)

    def test_atomicity_rolls_back_files_and_version_on_db_failure(self):
        data = load_scenario('empty_period')
        period_info = data['ledger']['period']
        period = VATPeriod.all_objects.create(
            tenant=self.tenant,
            year=period_info['year'],
            month=period_info['month'],
        )
        base_prefix = f'zp_returns/{self.tenant.slug}/2026/08/v1/'
        for suffix in ('payload.json', 'unsigned.xml'):
            path = f'{base_prefix}{suffix}'
            if default_storage.exists(path):
                default_storage.delete(path)

        with patch('accounting.services.tax_forms.zp.zp_returns.ZPReturn.objects.create') as mock_create:
            mock_create.side_effect = RuntimeError('DB insert failed')
            with self.assertRaises(RuntimeError):
                create_zp_return_draft(period)

        self.assertEqual(ZPReturn.all_objects.filter(vat_period=period).count(), 0)
        self.assertFalse(default_storage.exists(f'{base_prefix}payload.json'))
        self.assertFalse(default_storage.exists(f'{base_prefix}unsigned.xml'))

        second = create_zp_return_draft(period)
        self.assertEqual(second.version, 1)

    def test_empty_period_still_produces_valid_draft(self):
        data = load_scenario('empty_period')
        period_info = data['ledger']['period']
        period = VATPeriod.all_objects.create(
            tenant=self.tenant,
            year=period_info['year'],
            month=period_info['month'],
        )
        zp_return = create_zp_return_draft(period)
        self.assertEqual(zp_return.version, 1)
        self.assertTrue(default_storage.exists(zp_return.payload_json.name))
        self.assertTrue(default_storage.exists(zp_return.xml_unsigned.name))
