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
    Outcome,
    PartnerProvenance,
    PartnerSnapshot,
    TaxDocumentInput,
)
from domains.tax.classification.engine import _classified, classify
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
    def test_generic_303_becomes_review(self):
        result = classify(
            _input(
                source_kind='expense',
                direction=Direction.INPUT,
                lifecycle_status='approved',
                vat_rate=None,
            )
        )
        self.assertEqual(result.outcome, Outcome.REVIEW_REQUIRED)
        self.assertEqual(result.rule_code, 'EXPENSE_GENERIC_303_REMOVED')
        self.assertEqual(result.rows, ())

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
            )
        )
        self.assertEqual(result.outcome, Outcome.INVALID)

    def test_ambiguous_origin_review(self):
        result = classify(
            _input(
                source_kind='journal_line',
                event_kind=EventKind.REVERSAL,
                lifecycle_status='posted',
                originates_from='journal_entry:1',
                origin_tax_effects=(),
                origin_effects_ambiguous=True,
            )
        )
        self.assertEqual(result.outcome, Outcome.REVIEW_REQUIRED)


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
