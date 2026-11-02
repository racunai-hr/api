"""Unit tests for inbound OCR direction (issuer vs buyer vs tenant)."""

from django.test import SimpleTestCase

from domains.purchasing.services.direction import (
    DIRECTION_OK,
    DIRECTION_REVIEW_REQUIRED,
    DIRECTION_TENANT_NOT_ON_DOCUMENT,
    DIRECTION_WRONG,
    CompanyIdentity,
    apply_direction_to_payload,
    classify_document,
    classify_party,
    normalize_company_name,
)


FINE_STAR = CompanyIdentity(name='Fine Star d.o.o.', oib='36619131370', vat_id='HR36619131370')

HAC = {
    'name': 'Hrvatske autoceste d.o.o.',
    'oib': '57500462912',
    'vat_number': '',
    'address': 'Šiška 1',
    'city': 'Zagreb',
    'postal_code': '10000',
    'country': 'HR',
    'iban': '',
}

FINE_STAR_PARTY = {
    'name': 'FINE STAR DOO',
    'oib': '36619131370',
    'vat_number': '',
    'address': 'BANA JOSIPA JELAČIĆA 58',
    'city': 'ŠIBENIK',
    'postal_code': '22000',
    'country': 'HR',
    'iban': '',
}

FINE_STAR_NAME_ONLY = {**FINE_STAR_PARTY, 'oib': '', 'vat_number': ''}


class DirectionUnitTests(SimpleTestCase):
    def test_normalize_company_name(self):
        self.assertEqual(normalize_company_name('FINE STAR DOO'), 'fine star')
        self.assertEqual(normalize_company_name('Fine Star d.o.o.'), 'fine star')

    def test_hard_own_company_oib_and_vat(self):
        flags = classify_party(FINE_STAR_PARTY, FINE_STAR)
        self.assertTrue(flags.is_own_company)
        self.assertFalse(flags.suspected_own_company)
        vat_party = {**FINE_STAR_PARTY, 'oib': '', 'vat_number': 'HR36619131370'}
        self.assertTrue(classify_party(vat_party, FINE_STAR).is_own_company)

    def test_name_only_is_suspected_not_hard(self):
        flags = classify_party(FINE_STAR_NAME_ONLY, FINE_STAR)
        self.assertFalse(flags.is_own_company)
        self.assertTrue(flags.suspected_own_company)

    def test_name_plus_foreign_oib_is_not_own(self):
        flags = classify_party({**FINE_STAR_PARTY, 'oib': '27759560625'}, FINE_STAR)
        self.assertFalse(flags.is_own_company)
        self.assertFalse(flags.suspected_own_company)

    def test_hac_with_buyer_oib_is_ideal_inbound(self):
        payload = {'issuer': HAC, 'buyer': FINE_STAR_PARTY, 'iban': ''}
        result = classify_document(payload, FINE_STAR)
        self.assertEqual(result.code, DIRECTION_OK)
        self.assertTrue(result.propose_supplier)
        applied, _ = apply_direction_to_payload(payload, FINE_STAR)
        self.assertEqual(applied['supplier']['oib'], '57500462912')
        self.assertEqual(applied['supplier_source'], 'issuer')
        self.assertNotEqual(applied['supplier'].get('oib'), '36619131370')

    def test_hac_name_only_buyer_is_case_5(self):
        payload = {'issuer': HAC, 'buyer': FINE_STAR_NAME_ONLY}
        result = classify_document(payload, FINE_STAR)
        self.assertEqual(result.code, DIRECTION_TENANT_NOT_ON_DOCUMENT)
        self.assertTrue(result.buyer_flags.suspected_own_company)
        applied, _ = apply_direction_to_payload(payload, FINE_STAR)
        self.assertEqual(applied['supplier']['name'], HAC['name'])

    def test_swapped_with_issuer_oib_does_not_auto_swap(self):
        payload = {'issuer': FINE_STAR_PARTY, 'buyer': HAC}
        result = classify_document(payload, FINE_STAR)
        self.assertEqual(result.code, DIRECTION_WRONG)
        self.assertFalse(result.propose_supplier)
        applied, _ = apply_direction_to_payload(payload, FINE_STAR)
        self.assertEqual(applied['supplier'], {})
        self.assertEqual(applied['supplier_source'], '')

    def test_swapped_name_only_issuer_is_not_case_3(self):
        payload = {'issuer': FINE_STAR_NAME_ONLY, 'buyer': HAC}
        result = classify_document(payload, FINE_STAR)
        self.assertNotEqual(result.code, DIRECTION_WRONG)
        self.assertEqual(result.code, DIRECTION_TENANT_NOT_ON_DOCUMENT)
        applied, _ = apply_direction_to_payload(payload, FINE_STAR)
        self.assertEqual(applied['supplier']['name'], 'FINE STAR DOO')
        self.assertTrue(result.issuer_flags.suspected_own_company)

    def test_both_hard_own_is_review_required(self):
        payload = {'issuer': FINE_STAR_PARTY, 'buyer': FINE_STAR_PARTY}
        result = classify_document(payload, FINE_STAR)
        self.assertEqual(result.code, DIRECTION_REVIEW_REQUIRED)
        applied, _ = apply_direction_to_payload(payload, FINE_STAR)
        self.assertEqual(applied['supplier'], {})

    def test_v1_supplier_only_becomes_issuer(self):
        payload = {'supplier': HAC, 'invoice_number': '1'}
        applied, result = apply_direction_to_payload(payload, FINE_STAR)
        self.assertEqual(applied['issuer']['name'], HAC['name'])
        self.assertEqual(result.code, DIRECTION_TENANT_NOT_ON_DOCUMENT)
        self.assertEqual(applied['supplier']['name'], HAC['name'])
