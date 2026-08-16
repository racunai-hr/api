"""Tests for create_vat_return_draft — validation, storage, atomicity, audit."""

import json
import hashlib
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.test import TestCase

from accounting.models import VATPeriod, VATReturn, VATReturnStatus
from accounting.services.tax_forms.pdv.build import build_pdv_payload
from accounting.services.tax_forms.pdv.canonical import canonical_json, payload_hash
from accounting.services.tax_forms.pdv.mapping import PDV_MAPPING_VERSION
from accounting.services.tax_forms.pdv.parse import parse_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.payload import PDV_SCHEMA_VERSION
from accounting.services.tax_forms.pdv.validation import (
    PdvSchemaValidationError,
    validate_pdv_obrazac_xml,
)
from accounting.services.tax_forms.pdv.vat_returns import create_vat_return_draft
from accounting.tests.test_pdv_checkpoint_april_2026 import PdvCheckpointApril2026Tests
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice
from tenants.models import Tenant

_FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'pdv'
_SUBMITTED_APRIL_2026 = _FIXTURES / 'submitted_april_2026.xml'


class CreateVatReturnDraftTests(PdvCheckpointApril2026Tests):
    """create_vat_return_draft on Fine Star travanj 2026 fixture data."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        settings = CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Fine Star d.o.o.',
            company_address='Bana Josipa Jelačića 58\n22000 Šibenik',
            street='Bana Josipa Jelačića',
            house_number='58',
            postal_code='22000',
            city='Šibenik',
            company_phone='+385 22 000 000',
            company_email='info@finestar.example',
            vat_number='36619131370',
            tax_office=tax_office,
        )
        cls.accountant = ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Zorana',
            last_name='Lambasa',
        )

    def test_valid_pipeline_creates_version_with_files(self):
        vat_return = create_vat_return_draft(self.period, prepared_by=self.accountant)

        self.assertEqual(vat_return.version, 1)
        self.assertEqual(vat_return.status, VATReturnStatus.GENERATED)
        self.assertEqual(vat_return.schema_version, PDV_SCHEMA_VERSION)
        self.assertEqual(vat_return.mapping_version, PDV_MAPPING_VERSION)
        self.assertEqual(vat_return.prepared_by_id, self.accountant.pk)
        self.assertEqual(len(vat_return.payload_hash), 64)

        self.assertTrue(default_storage.exists(vat_return.payload_json.name))
        self.assertTrue(default_storage.exists(vat_return.xml_unsigned.name))

        stored_payload = json.loads(default_storage.open(vat_return.payload_json.name).read().decode('utf-8'))
        built = build_pdv_payload(self.period)
        self.assertEqual(stored_payload, json.loads(canonical_json(built)))
        self.assertEqual(vat_return.payload_hash, payload_hash(built))

        reference = parse_pdv_obrazac_xml(_SUBMITTED_APRIL_2026.read_bytes())
        self.assertEqual(stored_payload['fields']['303'], json.loads(canonical_json(reference))['fields']['303'])
        self.assertEqual(stored_payload['fields']['400'], json.loads(canonical_json(reference))['fields']['400'])

        xml_bytes = default_storage.open(vat_return.xml_unsigned.name).read()
        validate_pdv_obrazac_xml(xml_bytes)
        self.assertEqual(
            vat_return.unsigned_xml_sha256,
            hashlib.sha256(xml_bytes).hexdigest(),
        )

        expected_base = f'vat_returns/{self.tenant.slug}/2026/04/v1/'
        self.assertTrue(vat_return.payload_json.name.startswith(expected_base))
        self.assertTrue(vat_return.xml_unsigned.name.startswith(expected_base))

    def test_second_draft_increments_version(self):
        first = create_vat_return_draft(self.period)
        second = create_vat_return_draft(self.period)
        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        self.assertEqual(VATReturn.all_objects.filter(vat_period=self.period).count(), 2)

    def test_period_current_return_prefers_submitted(self):
        draft = create_vat_return_draft(self.period)
        self.assertEqual(self.period.current_return, draft)

        submitted = create_vat_return_draft(self.period)
        submitted.status = VATReturnStatus.SUBMITTED
        submitted.save(update_fields=['status'])
        self.assertEqual(self.period.current_return, submitted)

    @patch('accounting.services.tax_forms.pdv.vat_returns.validate_pdv_obrazac_xml')
    def test_invalid_xml_raises_and_creates_no_record(self, mock_validate):
        mock_validate.side_effect = PdvSchemaValidationError('XSD validation failed')

        with self.assertRaises(PdvSchemaValidationError):
            create_vat_return_draft(self.period)

        self.assertEqual(VATReturn.all_objects.filter(vat_period=self.period).count(), 0)

    def test_atomicity_rolls_back_files_and_version_on_db_failure(self):
        period = VATPeriod.all_objects.create(tenant=self.tenant, year=2026, month=5)
        base_prefix = f'vat_returns/{self.tenant.slug}/2026/05/v1/'
        for suffix in ('payload.json', 'unsigned.xml'):
            path = f'{base_prefix}{suffix}'
            if default_storage.exists(path):
                default_storage.delete(path)

        with patch('accounting.services.tax_forms.pdv.vat_returns.VATReturn.objects.create') as mock_create:
            mock_create.side_effect = RuntimeError('DB insert failed')
            with self.assertRaises(RuntimeError):
                create_vat_return_draft(period)

        self.assertEqual(VATReturn.all_objects.filter(vat_period=period).count(), 0)
        self.assertFalse(default_storage.exists(f'{base_prefix}payload.json'))
        self.assertFalse(default_storage.exists(f'{base_prefix}unsigned.xml'))

        second = create_vat_return_draft(period)
        self.assertEqual(second.version, 1)

    def test_submitted_return_rejects_payload_mutation(self):
        vat_return = create_vat_return_draft(self.period)
        vat_return.status = VATReturnStatus.SUBMITTED
        vat_return.save(update_fields=['status'])

        vat_return.payload_snapshot = {'fields': {}}
        with self.assertRaises(ValidationError):
            vat_return.save()


class CreateVatReturnDraftMinimalTests(TestCase):
    """Minimal tenant setup when checkpoint expense data is not required."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='vat-return-minimal', name='VAT return minimal')
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='1000',
            defaults={'name': 'Test PU', 'city': 'Zagreb'},
        )
        CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Minimal d.o.o.',
            company_address='Ulica 1\n10000 Zagreb',
            street='Ulica',
            house_number='1',
            postal_code='10000',
            city='Zagreb',
            company_phone='+385 1 000 000',
            company_email='minimal@example.com',
            vat_number='12345678901',
            tax_office=tax_office,
        )
        settings = CompanySettings.all_objects.get(tenant=cls.tenant)
        ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Ana',
            last_name='Anic',
        )
        cls.period = VATPeriod.all_objects.create(tenant=cls.tenant, year=2026, month=1)

    def test_empty_period_still_produces_valid_draft(self):
        vat_return = create_vat_return_draft(self.period)
        self.assertEqual(vat_return.version, 1)
        self.assertTrue(default_storage.exists(vat_return.payload_json.name))
        self.assertTrue(default_storage.exists(vat_return.xml_unsigned.name))
