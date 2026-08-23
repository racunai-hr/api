"""Pure TaxClassificationEngine tests — no production ledger writes."""

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from domains.tax.classification.accounts import is_tax_family_account
from domains.tax.classification.contracts import (
    ClassifiedWithoutRowsError,
    Direction,
    EventKind,
    OriginTaxEffect,
    OriginTaxOwner,
    Outcome,
    PartnerProvenance,
    PartnerSnapshot,
    TaxDocumentInput,
    TaxRelevance,
)
from domains.tax.classification.engine import _classified, classify, reconcile_rc_bases
from domains.tax.classification.finalize import finalize_period
from domains.tax.classification.hashing import hash_tax_input


def _input(**overrides) -> TaxDocumentInput:
    values = dict(
        tenant_id=1,
        source_kind='invoice_item',
        source_document_id=10,
        source_line_id=20,
        lifecycle_status='sent',
        event_kind=EventKind.ORIGINAL,
        direction=Direction.OUTPUT,
        document_date=date(2026, 4, 5),
        supply_date=None,
        partner=PartnerSnapshot(
            name='Kupac',
            country='Hrvatska',
            tax_number='12345678901',
            vat_id='',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        ),
        base_amount=Decimal('100.00'),
        vat_rate=Decimal('25.00'),
        vat_amount=Decimal('25.00'),
        currency='EUR',
        jurisdiction='RH',
        customer_type='B2B',
        supply_kind='goods',
        declared_procedure='standard',
        originates_from=None,
        origin_tax_effects=(),
        origin_effects_ambiguous=False,
        tax_relevance=TaxRelevance.TAX_RELEVANT,
        origin_tax_owner=OriginTaxOwner.NONE,
        has_linked_journal_entry=False,
        account_code=None,
        debit_amount=Decimal('0.00'),
        credit_amount=Decimal('0.00'),
        description='',
        period_year=2026,
        period_month=4,
        input_hash='',
    )
    values.update(overrides)
    document = TaxDocumentInput(**values)
    return TaxDocumentInput(**{**document.__dict__, 'input_hash': hash_tax_input(document)})


class ClassifyInvoiceTests(SimpleTestCase):
    def test_domestic_25(self):
        result = classify(_input())
        self.assertEqual(result.outcome, Outcome.CLASSIFIED)
        self.assertEqual(result.rows[0].box, '203')
        self.assertEqual(result.rows[0].base_amount, Decimal('100.00'))

    def test_unknown_rate_is_review(self):
        result = classify(_input(vat_rate=Decimal('10.00'), vat_amount=Decimal('10.00')))
        self.assertEqual(result.outcome, Outcome.REVIEW_REQUIRED)
        self.assertEqual(result.rows, ())
        self.assertEqual(result.rule_code, 'UNKNOWN_RATE_NO_LONGER_SKIPPED')

    def test_eu_goods_101(self):
        partner = PartnerSnapshot(
            name='DE GmbH',
            country='Germany',
            tax_number='DE123456789',
            vat_id='DE123456789',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        )
        result = classify(_input(partner=partner, vat_rate=Decimal('0.00'), vat_amount=Decimal('0.00')))
        self.assertEqual(result.outcome, Outcome.CLASSIFIED)
        self.assertEqual(result.rows[0].box, '101')
        self.assertIn('zp', result.rows[0].outputs)

    def test_eu_services_103(self):
        partner = PartnerSnapshot(
            name='DE GmbH',
            country='Germany',
            tax_number='DE123456789',
            vat_id='DE123456789',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        )
        result = classify(
            _input(
                partner=partner,
                vat_rate=Decimal('0.00'),
                vat_amount=Decimal('0.00'),
                supply_date=date(2026, 4, 1),
                supply_kind='services',
            )
        )
        self.assertEqual(result.rows[0].box, '103')

    def test_draft_not_relevant(self):
        result = classify(_input(lifecycle_status='draft'))
        self.assertEqual(result.outcome, Outcome.NOT_TAX_RELEVANT)


class ClassifyExpenseTests(SimpleTestCase):
    def test_zero_vat_expense_stays_review(self):
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='approved',
                vat_rate=None,
                vat_amount=Decimal('0.00'),
            )
        )
        self.assertEqual(result.outcome, Outcome.REVIEW_REQUIRED)
        self.assertEqual(result.rule_code, 'EXPENSE_GENERIC_303_REMOVED')
        self.assertEqual(result.rows, ())

    def test_mixed_rate_expense_stays_review(self):
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                vat_rate=None,
                base_amount=Decimal('223.87'),
                vat_amount=Decimal('7.49'),
            )
        )
        self.assertEqual(result.outcome, Outcome.REVIEW_REQUIRED)
        self.assertEqual(result.rule_code, 'EXPENSE_GENERIC_303_REMOVED')

    def test_cvh_mixed_rate_classifies_reconstructed_303(self):
        partner = PartnerSnapshot(
            name='CVH STP "VODICE"',
            country='Hrvatska',
            tax_number='73294314024',
            vat_id='',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        )
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                partner=partner,
                vat_rate=None,
                base_amount=Decimal('223.87'),
                vat_amount=Decimal('7.49'),
            )
        )
        self.assertEqual(result.outcome, Outcome.CLASSIFIED)
        self.assertEqual(result.rule_code, 'EXP_CVH_MIXED_25_303')
        self.assertEqual(result.rows[0].box, '303')
        self.assertEqual(result.rows[0].base_amount, Decimal('29.96'))
        self.assertEqual(result.rows[0].tax_amount, Decimal('7.49'))

    def test_cvh_mixed_small_ticket_classifies_303(self):
        partner = PartnerSnapshot(
            name='CVH STP "AUTOMEHANIKA"',
            country='Hrvatska',
            tax_number='52233171260',
            vat_id='',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        )
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                partner=partner,
                vat_rate=None,
                base_amount=Decimal('7.95'),
                vat_amount=Decimal('0.66'),
            )
        )
        self.assertEqual(result.rows[0].base_amount, Decimal('2.64'))
        self.assertEqual(result.rows[0].tax_amount, Decimal('0.66'))

    def test_hr_bank_zero_vat_is_not_tax_relevant(self):
        partner = PartnerSnapshot(
            name='OTP banka d.d.',
            country='Hrvatska',
            tax_number='52508873833',
            vat_id='',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        )
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                partner=partner,
                vat_rate=None,
                vat_amount=Decimal('0.00'),
                base_amount=Decimal('13.94'),
            )
        )
        self.assertEqual(result.outcome, Outcome.NOT_TAX_RELEVANT)
        self.assertEqual(result.rule_code, 'EXP_DOMESTIC_BANK_NO_VAT')
        self.assertEqual(result.rows, ())

    def test_hr_insurance_zero_vat_is_not_tax_relevant(self):
        partner = PartnerSnapshot(
            name='Generali osiguranje d.d.',
            country='Hrvatska',
            tax_number='10840749604',
            vat_id='',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        )
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                partner=partner,
                vat_rate=None,
                vat_amount=Decimal('0.00'),
                base_amount=Decimal('13.22'),
            )
        )
        self.assertEqual(result.outcome, Outcome.NOT_TAX_RELEVANT)
        self.assertEqual(result.rule_code, 'EXP_DOMESTIC_INSURANCE_NO_VAT')

    def test_third_country_zero_vat_ch_telecom_is_not_tax_relevant(self):
        partner = PartnerSnapshot(
            name='Telecom26 AG',
            country='Švicarska',
            tax_number='CHE-431.728.269',
            vat_id='CHE-431.728.269',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        )
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                partner=partner,
                vat_rate=None,
                vat_amount=Decimal('0.00'),
                base_amount=Decimal('300.00'),
            )
        )
        self.assertEqual(result.outcome, Outcome.NOT_TAX_RELEVANT)
        self.assertEqual(result.rule_code, 'EXP_CH_TELECOM_NO_VAT')
        self.assertEqual(result.rows, ())

    def test_third_country_non_telecom_zero_vat_stays_review(self):
        partner = PartnerSnapshot(
            name='Alpine Hotel AG',
            country='Švicarska',
            tax_number='CHE-111.222.333',
            vat_id='CHE-111.222.333',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        )
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                partner=partner,
                vat_rate=None,
                vat_amount=Decimal('0.00'),
                base_amount=Decimal('480.00'),
            )
        )
        self.assertEqual(result.outcome, Outcome.REVIEW_REQUIRED)
        self.assertEqual(result.rule_code, 'EXPENSE_GENERIC_303_REMOVED')

    def test_eu_goods_acquisition_with_vin_classifies_207_307(self):
        partner = PartnerSnapshot(
            name='SaM Automobile',
            country='Njemačka',
            tax_number='',
            vat_id='DE355497142',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        )
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                partner=partner,
                vat_rate=None,
                vat_amount=Decimal('0.00'),
                base_amount=Decimal('33000.00'),
                supply_kind='unknown',
                has_linked_journal_entry=True,
                description='Audi A8 Lang 50 TDI WAUZZZF86RN003268',
            )
        )
        self.assertEqual(result.outcome, Outcome.CLASSIFIED)
        self.assertEqual(result.rule_code, 'EXP_EU_GOODS_207_307')
        self.assertEqual([row.box for row in result.rows], ['207', '207', '307'])
        self.assertEqual(result.rows[0].base_amount, Decimal('33000.00'))
        self.assertEqual(result.rows[0].tax_amount, Decimal('0.00'))
        self.assertEqual(result.rows[1].base_amount, Decimal('0.00'))
        self.assertEqual(result.rows[1].tax_amount, Decimal('8250.00'))
        self.assertEqual(result.rows[2].base_amount, Decimal('33000.00'))
        self.assertEqual(result.rows[2].tax_amount, Decimal('8250.00'))

    def test_eu_zero_vat_without_goods_signal_keeps_journal_skip(self):
        partner = PartnerSnapshot(
            name='DE GmbH',
            country='Germany',
            tax_number='DE123456789',
            vat_id='DE123456789',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        )
        skipped = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                partner=partner,
                vat_rate=None,
                vat_amount=Decimal('0.00'),
                base_amount=Decimal('1000.00'),
                supply_kind='unknown',
                has_linked_journal_entry=True,
                description='EU usluga',
            )
        )
        self.assertEqual(skipped.outcome, Outcome.NOT_TAX_RELEVANT)
        self.assertEqual(skipped.rule_code, 'eu_expense_posted_via_journal')

        placeholder = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                partner=partner,
                vat_rate=None,
                vat_amount=Decimal('0.00'),
                base_amount=Decimal('1000.00'),
                supply_kind='unknown',
                has_linked_journal_entry=False,
                description='EU usluga',
            )
        )
        self.assertEqual(placeholder.outcome, Outcome.CLASSIFIED)
        self.assertEqual(placeholder.rule_code, 'EXP_EU_614')
        self.assertEqual(placeholder.rows[0].box, '614')

    def test_eu_goods_supply_kind_classifies_207_307_without_vin(self):
        partner = PartnerSnapshot(
            name='DE GmbH',
            country='Germany',
            tax_number='DE123456789',
            vat_id='DE123456789',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        )
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                partner=partner,
                vat_rate=None,
                vat_amount=Decimal('0.00'),
                base_amount=Decimal('8000.00'),
                supply_kind='goods',
                has_linked_journal_entry=True,
                description='EU nabava dobara',
            )
        )
        self.assertEqual(result.outcome, Outcome.CLASSIFIED)
        self.assertEqual(result.rule_code, 'EXP_EU_GOODS_207_307')
        self.assertEqual(result.rows[2].box, '307')
        self.assertEqual(result.rows[1].tax_amount, Decimal('2000.00'))

    def test_third_country_vin_does_not_become_eu_goods(self):
        partner = PartnerSnapshot(
            name='Telecom26 AG',
            country='Švicarska',
            tax_number='CHE-431.728.269',
            vat_id='CHE-431.728.269',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        )
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                partner=partner,
                vat_rate=None,
                vat_amount=Decimal('0.00'),
                base_amount=Decimal('300.00'),
                supply_kind='unknown',
                description='Prepaid WAUZZZF86RN003268',
            )
        )
        self.assertEqual(result.outcome, Outcome.NOT_TAX_RELEVANT)
        self.assertEqual(result.rule_code, 'EXP_CH_TELECOM_NO_VAT')
        self.assertEqual(result.rows, ())

    def test_domestic_25_classifies_303(self):
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                vat_rate=None,
                base_amount=Decimal('66.36'),
                vat_amount=Decimal('16.59'),
            )
        )
        self.assertEqual(result.outcome, Outcome.CLASSIFIED)
        self.assertEqual(result.rule_code, 'EXP_DOMESTIC_25_303')
        self.assertEqual(result.rows[0].box, '303')
        self.assertEqual(result.rows[0].tax_amount, Decimal('16.59'))

    def test_domestic_25_cent_rounding_classifies_303(self):
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                vat_rate=None,
                base_amount=Decimal('15.99'),
                vat_amount=Decimal('4.00'),
            )
        )
        self.assertEqual(result.outcome, Outcome.CLASSIFIED)
        self.assertEqual(result.rows[0].box, '303')

    def test_domestic_25_half_even_one_cent_classifies_303(self):
        partner = PartnerSnapshot(
            name='CVH STP "VODICE"',
            country='Hrvatska',
            tax_number='73294314024',
            vat_id='',
            provenance=PartnerProvenance.DOCUMENT_SNAPSHOT,
        )
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                partner=partner,
                vat_rate=None,
                base_amount=Decimal('40.50'),
                vat_amount=Decimal('10.13'),
            )
        )
        self.assertEqual(result.outcome, Outcome.CLASSIFIED)
        self.assertEqual(result.rule_code, 'EXP_DOMESTIC_25_303')
        self.assertEqual(result.rows[0].box, '303')
        self.assertEqual(result.rows[0].base_amount, Decimal('40.50'))
        self.assertEqual(result.rows[0].tax_amount, Decimal('10.13'))

    def test_ioss_308(self):
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='paid',
                declared_procedure='ioss',
                vat_rate=Decimal('25.00'),
            )
        )
        self.assertEqual(result.outcome, Outcome.CLASSIFIED)
        self.assertEqual(result.rows[0].box, '308')


class ClassifyJournalTests(SimpleTestCase):
    def test_non_tax_account_not_relevant(self):
        result = classify(
            _input(
                source_kind='journal_line',
                lifecycle_status='posted',
                account_code='2201',
                debit_amount=Decimal('0.00'),
                credit_amount=Decimal('10.00'),
                direction=Direction.INPUT,
            )
        )
        self.assertEqual(result.outcome, Outcome.NOT_TAX_RELEVANT)

    def test_mapped_1400(self):
        result = classify(
            _input(
                source_kind='journal_line',
                lifecycle_status='posted',
                account_code='1400',
                debit_amount=Decimal('25.00'),
                credit_amount=Decimal('0.00'),
                direction=Direction.INPUT,
                base_amount=Decimal('0.00'),
                vat_amount=Decimal('0.00'),
            )
        )
        self.assertEqual(result.outcome, Outcome.CLASSIFIED)
        self.assertEqual(result.rows[0].box, '303')

    def test_unmapped_tax_family_credit_1400(self):
        self.assertTrue(is_tax_family_account('1400'))
        result = classify(
            _input(
                source_kind='journal_line',
                lifecycle_status='posted',
                account_code='1400',
                debit_amount=Decimal('0.00'),
                credit_amount=Decimal('25.00'),
                direction=Direction.INPUT,
            )
        )
        self.assertEqual(result.outcome, Outcome.REVIEW_REQUIRED)
        self.assertEqual(result.rule_code, 'UNMAPPED_TAX_ACCOUNT_NO_LONGER_SKIPPED')

    def test_rc_pair_multiplicity(self):
        pretporez = classify(
            _input(
                source_kind='journal_line',
                source_line_id=1,
                lifecycle_status='posted',
                account_code='14022',
                debit_amount=Decimal('2000.00'),
                credit_amount=Decimal('0.00'),
                direction=Direction.INPUT,
            )
        )
        obveza = classify(
            _input(
                source_kind='journal_line',
                source_line_id=2,
                lifecycle_status='posted',
                account_code='24022',
                debit_amount=Decimal('0.00'),
                credit_amount=Decimal('2000.00'),
                direction=Direction.OUTPUT,
            )
        )
        self.assertEqual(pretporez.rows[0].box, '307')
        self.assertEqual(obveza.rows[0].box, '207')
        self.assertEqual(pretporez.rows[0].tax_amount, Decimal('2000.00'))
        self.assertEqual(obveza.rows[0].tax_amount, Decimal('2000.00'))
        self.assertEqual(pretporez.rows[0].base_amount, Decimal('8000.00'))

    def test_rc_base_follows_acquisition_not_inverse_vat(self):
        acquisition = classify(
            _input(
                source_kind='journal_line',
                source_line_id=10,
                lifecycle_status='posted',
                account_code='0373',
                debit_amount=Decimal('15882.35'),
                credit_amount=Decimal('0.00'),
                direction=Direction.INPUT,
            )
        )
        pretporez = classify(
            _input(
                source_kind='journal_line',
                source_line_id=11,
                lifecycle_status='posted',
                account_code='14022',
                debit_amount=Decimal('3970.59'),
                credit_amount=Decimal('0.00'),
                direction=Direction.INPUT,
            )
        )
        self.assertEqual(pretporez.rows[0].base_amount, Decimal('15882.36'))
        reconciled = reconcile_rc_bases(acquisition.rows + pretporez.rows)
        bases = {row.box: row.base_amount for row in reconciled}
        self.assertEqual(bases['207'], Decimal('15882.35'))
        self.assertEqual(bases['307'], Decimal('15882.35'))

    def test_rc_reconcile_keeps_exact_8000_pair(self):
        acquisition = classify(
            _input(
                source_kind='journal_line',
                source_line_id=20,
                lifecycle_status='posted',
                account_code='0373',
                debit_amount=Decimal('8000.00'),
                credit_amount=Decimal('0.00'),
                direction=Direction.INPUT,
            )
        )
        pretporez = classify(
            _input(
                source_kind='journal_line',
                source_line_id=21,
                lifecycle_status='posted',
                account_code='14022',
                debit_amount=Decimal('2000.00'),
                credit_amount=Decimal('0.00'),
                direction=Direction.INPUT,
            )
        )
        reconciled = reconcile_rc_bases(acquisition.rows + pretporez.rows)
        pretporez_row = next(row for row in reconciled if row.box == '307')
        self.assertEqual(pretporez_row.base_amount, Decimal('8000.00'))


class ReversalTests(SimpleTestCase):
    def test_inverts_origin_effect(self):
        effect = OriginTaxEffect(
            box='303',
            base_amount=Decimal('100.00'),
            tax_amount=Decimal('25.00'),
            direction=Direction.INPUT,
            category='domestic',
            ledger_type='U-RA',
            sign=Decimal('1'),
            source_kind='journal_line',
            source_document_id=1,
            source_line_id=1,
            ledger_entry_id=9,
        )
        result = classify(
            _input(
                source_kind='journal_line',
                event_kind=EventKind.REVERSAL,
                direction=Direction.CORRECTIVE,
                lifecycle_status='posted',
                originates_from='journal_entry:1',
                origin_tax_effects=(effect,),
                tax_relevance=TaxRelevance.TAX_RELEVANT,
                origin_tax_owner=OriginTaxOwner.JOURNAL_LINE,
                account_code='1400',
                debit_amount=Decimal('0.00'),
                credit_amount=Decimal('25.00'),
            )
        )
        self.assertEqual(result.outcome, Outcome.CLASSIFIED)
        self.assertEqual(result.rows[0].sign, Decimal('-1'))
        self.assertEqual(result.rows[0].box, '303')

    def test_missing_ledger_evidence_invalid(self):
        result = classify(
            _input(
                source_kind='journal_line',
                event_kind=EventKind.REVERSAL,
                lifecycle_status='posted',
                originates_from='journal_entry:1',
                origin_tax_effects=(),
                tax_relevance=TaxRelevance.TAX_RELEVANT,
                origin_tax_owner=OriginTaxOwner.JOURNAL_LINE,
            )
        )
        self.assertEqual(result.outcome, Outcome.INVALID)
        self.assertEqual(result.rule_code, 'reversal_missing_ledger_evidence')

    def test_ambiguous_origin_invalid(self):
        result = classify(
            _input(
                source_kind='journal_line',
                event_kind=EventKind.REVERSAL,
                lifecycle_status='posted',
                originates_from='journal_entry:1',
                origin_tax_effects=(),
                origin_effects_ambiguous=True,
                tax_relevance=TaxRelevance.TAX_RELEVANT,
                origin_tax_owner=OriginTaxOwner.JOURNAL_LINE,
            )
        )
        self.assertEqual(result.outcome, Outcome.INVALID)
        self.assertEqual(result.rule_code, 'REVERSAL_AMBIGUOUS_ORIGIN_EFFECT')

    def test_not_tax_relevant_reversal(self):
        result = classify(
            _input(
                source_kind='journal_line',
                event_kind=EventKind.REVERSAL,
                lifecycle_status='posted',
                originates_from='journal_entry:1',
                origin_tax_effects=(),
                tax_relevance=TaxRelevance.NOT_TAX_RELEVANT,
                origin_tax_owner=OriginTaxOwner.NONE,
            )
        )
        self.assertEqual(result.outcome, Outcome.NOT_TAX_RELEVANT)
        self.assertEqual(result.rule_code, 'REV_NOT_TAX_RELEVANT')
        self.assertEqual(result.rows, ())

    def test_undetermined_tax_owner_review(self):
        result = classify(
            _input(
                source_kind='journal_line',
                event_kind=EventKind.REVERSAL,
                lifecycle_status='posted',
                originates_from='journal_entry:1',
                origin_tax_effects=(),
                tax_relevance=TaxRelevance.UNDETERMINED,
                origin_tax_owner=OriginTaxOwner.INVOICE,
            )
        )
        self.assertEqual(result.outcome, Outcome.REVIEW_REQUIRED)
        self.assertEqual(result.rule_code, 'REVERSAL_TAX_OWNER_UNDETERMINED')
        self.assertEqual(result.rows, ())


class FinalizeAndInvariantTests(SimpleTestCase):
    def test_classified_empty_rows_raises(self):
        document = _input()
        with self.assertRaises(ClassifiedWithoutRowsError):
            _classified(document, (), reason='x', rule_code='x', warnings=[])

    def test_finalize_610(self):
        result = classify(
            _input(
                source_kind='journal_line',
                lifecycle_status='posted',
                account_code='0320',
                debit_amount=Decimal('100.00'),
                credit_amount=Decimal('0.00'),
                direction=Direction.INPUT,
            )
        )
        finalized = finalize_period(result.rows, period_year=2026, period_month=4, period_id=7)
        boxes = [row.box for row in finalized]
        self.assertIn('612', boxes)
        self.assertIn('610', boxes)

    def test_current_partner_warning(self):
        partner = PartnerSnapshot(
            name='X',
            country='Hrvatska',
            tax_number='12345678901',
            vat_id='',
            provenance=PartnerProvenance.CURRENT_PARTNER,
        )
        result = classify(_input(partner=partner))
        self.assertIn('current_partner', result.warnings)
