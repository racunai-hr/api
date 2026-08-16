"""Tests for VATReturn draft integrity (I001/I002) and resync."""

import hashlib
import re

from django.contrib.admin.sites import AdminSite
from django.core.files.storage import default_storage
from django.test import RequestFactory, TestCase

from accounting.admin import VATReturnAdmin, VATReturnInline, _pdv_xml_export_link
from accounting.models import VATReturn, VATReturnStatus
from accounting.services.tax_forms.pdv.integrity import (
    VatReturnOutOfSyncError,
    assert_vat_return_sync,
    check_vat_return_integrity,
)
from accounting.services.tax_forms.pdv.vat_returns import (
    create_vat_return_draft,
    resync_unsigned_xml_from_payload,
)
from accounting.tests.test_pdv_checkpoint_april_2026 import PdvCheckpointApril2026Tests
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice

_PDV_NS = 'http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacPDV/v11-0'


def _patch_pdv_field(xml_text: str, code: str, new_value: str) -> str:
    open_patterns = [
        re.escape(f'{{{_PDV_NS}}}Podatak{code}'),
        rf'(?:ns\d+:)?Podatak{code}',
    ]
    for open_pattern in open_patterns:
        pattern = rf'(<{open_pattern}>)[^<]+(</{open_pattern}>)'
        patched, count = re.subn(pattern, rf'\g<1>{new_value}\g<2>', xml_text, count=1)
        if count:
            return patched
    raise ValueError(f'Podatak{code} nije pronađen u XML-u')


class VatReturnIntegrityTests(PdvCheckpointApril2026Tests):
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

    def test_integrity_sync_after_draft(self):
        vat_return = create_vat_return_draft(self.period, prepared_by=self.accountant)
        integrity = check_vat_return_integrity(vat_return)

        self.assertTrue(integrity.payload_file_ok)
        self.assertTrue(integrity.xml_file_ok)
        self.assertTrue(integrity.fields_match)
        self.assertTrue(integrity.xml_bytes_match)
        self.assertEqual(integrity.status, 'SYNC')
        self.assertEqual(integrity.differences, [])
        assert_vat_return_sync(vat_return)

    def _patch_xml_file(self, xml_path: str, new_text: str) -> None:
        with default_storage.open(xml_path, 'wb') as handle:
            handle.write(new_text.encode('utf-8'))

    def test_integrity_detects_patched_xml(self):
        vat_return = create_vat_return_draft(self.period, prepared_by=self.accountant)
        xml_path = vat_return.xml_unsigned.name
        xml_text = default_storage.open(xml_path).read().decode('utf-8')
        patched = _patch_pdv_field(xml_text, '400', '99999.99')
        self._patch_xml_file(xml_path, patched)

        integrity = check_vat_return_integrity(vat_return)
        self.assertEqual(integrity.status, 'OUT_OF_SYNC')
        self.assertFalse(integrity.fields_match)
        self.assertFalse(integrity.xml_bytes_match)
        self.assertTrue(any('400' in diff.field for diff in integrity.differences))

        with self.assertRaises(VatReturnOutOfSyncError):
            assert_vat_return_sync(vat_return)

    def test_resync_restores_sync_without_changing_payload(self):
        vat_return = create_vat_return_draft(self.period, prepared_by=self.accountant)
        original_hash = vat_return.payload_hash
        original_payload_path = vat_return.payload_json.name

        xml_path = vat_return.xml_unsigned.name
        xml_text = default_storage.open(xml_path).read().decode('utf-8')
        patched = _patch_pdv_field(xml_text, '400', '99999.99')
        self._patch_xml_file(xml_path, patched)

        resynced = resync_unsigned_xml_from_payload(vat_return)
        integrity = check_vat_return_integrity(resynced)

        self.assertEqual(integrity.status, 'SYNC')
        self.assertEqual(resynced.payload_hash, original_hash)
        self.assertEqual(resynced.payload_json.name, original_payload_path)
        self.assertEqual(resynced.version, vat_return.version)

        xml_bytes = default_storage.open(resynced.xml_unsigned.name).read()
        self.assertEqual(
            resynced.unsigned_xml_sha256,
            hashlib.sha256(xml_bytes).hexdigest(),
        )

    def test_resync_rejects_submitted_return(self):
        vat_return = create_vat_return_draft(self.period, prepared_by=self.accountant)
        vat_return.status = VATReturnStatus.SUBMITTED
        vat_return.save(update_fields=['status'])

        with self.assertRaises(ValueError):
            resync_unsigned_xml_from_payload(vat_return)


class VatReturnAdminIntegrityTests(PdvCheckpointApril2026Tests):
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
        ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Zorana',
            last_name='Lambasa',
        )

    def _patch_xml_file(self, xml_path: str, new_text: str) -> None:
        with default_storage.open(xml_path, 'wb') as handle:
            handle.write(new_text.encode('utf-8'))

    def test_admin_exports_hidden_when_out_of_sync(self):
        vat_return = create_vat_return_draft(self.period)
        admin_site = AdminSite()
        request = RequestFactory().get('/')
        return_admin = VATReturnAdmin(VATReturn, admin_site)
        inline = VATReturnInline(VATReturn, admin_site)

        sync_html = str(return_admin.exports_display(vat_return))
        self.assertIn('PDV XML', sync_html)
        self.assertNotIn('OUT OF SYNC', sync_html)

        xml_path = vat_return.xml_unsigned.name
        xml_text = default_storage.open(xml_path).read().decode('utf-8')
        patched = _patch_pdv_field(xml_text, '400', '0.00')
        self._patch_xml_file(xml_path, patched)

        out_of_sync_html = str(return_admin.exports_display(vat_return))
        self.assertIn('OUT OF SYNC', out_of_sync_html)
        self.assertNotRegex(out_of_sync_html, r'<a[^>]+>PDV XML</a>')

        inline_html = str(inline.exports_display(vat_return))
        self.assertIn('OUT OF SYNC', inline_html)

    def test_pdv_xml_export_link_disabled_when_out_of_sync(self):
        vat_return = create_vat_return_draft(self.period)
        link = _pdv_xml_export_link(vat_return)
        self.assertIn('<a ', str(link))

        xml_path = vat_return.xml_unsigned.name
        xml_text = default_storage.open(xml_path).read().decode('utf-8')
        self._patch_xml_file(xml_path, xml_text + '<!-- tamper -->')

        link = _pdv_xml_export_link(vat_return)
        self.assertIn('OUT OF SYNC', str(link))
        self.assertNotIn('<a ', str(link))
