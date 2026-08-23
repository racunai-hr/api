"""VehicleTaxEvaluator unit matrix — STP v1 precedence. No API, ORM, or ledger."""

from __future__ import annotations

from decimal import Decimal
from dataclasses import fields

from django.test import SimpleTestCase

from domains.tax.vehicle.contracts import (
    BenefitInKind,
    CitExceptionMode,
    CitTreatment,
    DocumentFacts,
    EvidenceType,
    LineFacts,
    ReasonCode,
    SupplierVatStatus,
    TaxEvaluationContext,
    VatExceptionMode,
    VatTreatment,
    VehicleClass,
    VehicleFacts,
    VehicleLineKind,
)
from domains.tax.vehicle.evaluator import HALF, ONE, ZERO, evaluate, input_vat_evidence_sufficient


def _ctx(
    *,
    kind: VehicleLineKind | str | None,
    vat_amount: str = '0.00',
    net_amount: str = '10.00',
    vehicle: VehicleFacts | None = None,
    document: DocumentFacts | None = None,
) -> TaxEvaluationContext:
    vat = Decimal(vat_amount)
    net = Decimal(net_amount)
    return TaxEvaluationContext(
        line=LineFacts(
            vehicle_line_kind=kind,
            net_amount=net,
            vat_amount=vat,
            gross_amount=net + vat,
        ),
        vehicle=vehicle or VehicleFacts(),
        document=document or DocumentFacts(),
    )


def _invoice_registered() -> DocumentFacts:
    return DocumentFacts(
        evidence_type=EvidenceType.INVOICE,
        supplier_vat_status=SupplierVatStatus.REGISTERED,
    )


def _m1(*, vat_exc=VatExceptionMode.NONE, cit_exc=CitExceptionMode.NONE, bik=BenefitInKind.NONE):
    return VehicleFacts(
        vehicle_class=VehicleClass.M1,
        vat_exception_mode=vat_exc,
        cit_exception_mode=cit_exc,
        benefit_in_kind=bik,
    )


class VehicleTaxEvaluatorContractTests(SimpleTestCase):
    def test_context_has_no_expense_category(self):
        names = {item.name for item in fields(TaxEvaluationContext)}
        self.assertEqual(names, {'line', 'vehicle', 'document'})
        line_names = {item.name for item in fields(LineFacts)}
        self.assertNotIn('category', line_names)
        self.assertNotIn('category_code', line_names)

    def test_result_requires_review_is_derived(self):
        result = evaluate(_ctx(kind=None))
        self.assertTrue(result.vat_requires_review)
        self.assertTrue(result.cit_requires_review)
        self.assertTrue(result.requires_review)
        self.assertIsNone(result.vat_deductible_ratio)
        self.assertIsNone(result.cit_addback_ratio)


class IdentityGateTests(SimpleTestCase):
    def test_null_kind_reviews_both_axes(self):
        result = evaluate(_ctx(kind=None))
        self.assertIsNone(result.vat_treatment)
        self.assertIsNone(result.cit_treatment)
        self.assertIn(ReasonCode.VAT_LINE_NOT_CLASSIFIED, result.reason_codes)
        self.assertIn(ReasonCode.CIT_LINE_NOT_CLASSIFIED, result.reason_codes)

    def test_unclassified_reviews_both_axes(self):
        result = evaluate(_ctx(kind=VehicleLineKind.UNCLASSIFIED, vat_amount='4.67'))
        self.assertTrue(result.vat_requires_review)
        self.assertTrue(result.cit_requires_review)
        self.assertIsNone(result.vat_treatment)
        self.assertIsNone(result.cit_treatment)

    def test_unknown_kind_reviews_both_axes(self):
        result = evaluate(_ctx(kind='vehicle_fuel'))
        self.assertTrue(result.vat_requires_review)
        self.assertTrue(result.cit_requires_review)
        self.assertIn(ReasonCode.VAT_KIND_UNKNOWN, result.reason_codes)


class HardExpectedNoInputVatTests(SimpleTestCase):
    kinds = (
        VehicleLineKind.INSURANCE_COMPULSORY,
        VehicleLineKind.ROAD_FEE_ANNUAL,
        VehicleLineKind.ADMINISTRATIVE_FEE,
        VehicleLineKind.MOTOR_VEHICLE_TAX,
    )

    def test_zero_vat_finalizes_without_vehicle(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                result = evaluate(_ctx(kind=kind, vat_amount='0.00'))
                self.assertEqual(result.vat_treatment, VatTreatment.NO_INPUT_VAT)
                self.assertEqual(result.vat_deductible_ratio, ZERO)
                self.assertFalse(result.vat_requires_review)
                self.assertEqual(result.cit_treatment, CitTreatment.EXCLUDED_FROM_VEHICLE_50)
                self.assertEqual(result.cit_addback_ratio, ZERO)
                self.assertFalse(result.cit_requires_review)
                self.assertFalse(result.requires_review)

    def test_positive_vat_reviews_vat_only(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                result = evaluate(_ctx(kind=kind, vat_amount='5.00'))
                self.assertTrue(result.vat_requires_review)
                self.assertIsNone(result.vat_deductible_ratio)
                self.assertIn(ReasonCode.VAT_EVIDENCE_CONTRADICTION, result.reason_codes)
                self.assertEqual(result.cit_treatment, CitTreatment.EXCLUDED_FROM_VEHICLE_50)
                self.assertEqual(result.cit_addback_ratio, ZERO)
                self.assertFalse(result.cit_requires_review)
                self.assertTrue(result.requires_review)

    def test_vehicle_full_deduction_does_not_override_ao(self):
        result = evaluate(
            _ctx(
                kind=VehicleLineKind.INSURANCE_COMPULSORY,
                vat_amount='0.00',
                vehicle=_m1(vat_exc=VatExceptionMode.FULL_DEDUCTION),
            )
        )
        self.assertEqual(result.vat_treatment, VatTreatment.NO_INPUT_VAT)
        self.assertEqual(result.vat_deductible_ratio, ZERO)
        self.assertEqual(result.cit_treatment, CitTreatment.EXCLUDED_FROM_VEHICLE_50)


class EnvironmentalFeeTests(SimpleTestCase):
    def test_zero_vat_closes_vat_reviews_cit(self):
        result = evaluate(_ctx(kind=VehicleLineKind.ENVIRONMENTAL_FEE, vat_amount='0.00'))
        self.assertEqual(result.vat_treatment, VatTreatment.NO_INPUT_VAT)
        self.assertEqual(result.vat_deductible_ratio, ZERO)
        self.assertFalse(result.vat_requires_review)
        self.assertTrue(result.cit_requires_review)
        self.assertIsNone(result.cit_treatment)
        self.assertIsNone(result.cit_addback_ratio)
        self.assertIn(ReasonCode.CIT_ENVIRONMENTAL_FEE_UNRESOLVED, result.reason_codes)
        self.assertTrue(result.requires_review)

    def test_vehicle_excluded_does_not_close_cit(self):
        result = evaluate(
            _ctx(
                kind=VehicleLineKind.ENVIRONMENTAL_FEE,
                vat_amount='0.00',
                vehicle=_m1(cit_exc=CitExceptionMode.EXCLUDED),
            )
        )
        self.assertTrue(result.cit_requires_review)
        self.assertIsNone(result.cit_addback_ratio)


class CandidateServiceTests(SimpleTestCase):
    kinds = (
        VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
        VehicleLineKind.REGISTRATION_ADMIN_SERVICE,
    )

    def test_m1_none_with_vat_limits_and_addback(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                result = evaluate(
                    _ctx(kind=kind, vat_amount='4.67', vehicle=_m1(), document=_invoice_registered())
                )
                self.assertEqual(result.vat_treatment, VatTreatment.LIMITED_INPUT_VAT)
                self.assertEqual(result.vat_deductible_ratio, HALF)
                self.assertEqual(result.cit_treatment, CitTreatment.VEHICLE_50_ADD_BACK)
                self.assertEqual(result.cit_addback_ratio, HALF)
                self.assertFalse(result.requires_review)

    def test_m1_full_deduction_keeps_cit_addback(self):
        result = evaluate(
            _ctx(
                kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
                vat_amount='4.67',
                vehicle=_m1(vat_exc=VatExceptionMode.FULL_DEDUCTION),
                document=_invoice_registered(),
            )
        )
        self.assertEqual(result.vat_treatment, VatTreatment.FULL_INPUT_VAT)
        self.assertEqual(result.vat_deductible_ratio, ONE)
        self.assertEqual(result.cit_treatment, CitTreatment.VEHICLE_50_ADD_BACK)
        self.assertEqual(result.cit_addback_ratio, HALF)

    def test_other_class_reviews_vat_only(self):
        result = evaluate(
            _ctx(
                kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
                vat_amount='4.67',
                vehicle=VehicleFacts(
                    vehicle_class=VehicleClass.OTHER,
                    cit_exception_mode=CitExceptionMode.NONE,
                    benefit_in_kind=BenefitInKind.NONE,
                ),
                document=_invoice_registered(),
            )
        )
        self.assertTrue(result.vat_requires_review)
        self.assertIsNone(result.vat_deductible_ratio)
        self.assertEqual(result.cit_treatment, CitTreatment.VEHICLE_50_ADD_BACK)
        self.assertEqual(result.cit_addback_ratio, HALF)
        self.assertFalse(result.cit_requires_review)

    def test_zero_vat_reviews_vat_allows_cit_overlay(self):
        result = evaluate(
            _ctx(
                kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
                vat_amount='0.00',
                vehicle=_m1(),
                document=_invoice_registered(),
            )
        )
        self.assertTrue(result.vat_requires_review)
        self.assertIn(ReasonCode.VAT_EVIDENCE_INSUFFICIENT, result.reason_codes)
        self.assertEqual(result.cit_treatment, CitTreatment.VEHICLE_50_ADD_BACK)
        self.assertFalse(result.cit_requires_review)

    def test_missing_vehicle_class_reviews_vat(self):
        result = evaluate(
            _ctx(
                kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
                vat_amount='4.67',
                vehicle=VehicleFacts(
                    cit_exception_mode=CitExceptionMode.NONE,
                    benefit_in_kind=BenefitInKind.NONE,
                ),
                document=_invoice_registered(),
            )
        )
        self.assertTrue(result.vat_requires_review)
        self.assertFalse(result.cit_requires_review)

    def test_cit_excluded_does_not_need_bik(self):
        result = evaluate(
            _ctx(
                kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
                vat_amount='4.67',
                vehicle=VehicleFacts(
                    vehicle_class=VehicleClass.M1,
                    vat_exception_mode=VatExceptionMode.NONE,
                    cit_exception_mode=CitExceptionMode.EXCLUDED,
                ),
                document=_invoice_registered(),
            )
        )
        self.assertEqual(result.cit_treatment, CitTreatment.EXCLUDED_FROM_VEHICLE_50)
        self.assertEqual(result.cit_addback_ratio, ZERO)
        self.assertFalse(result.cit_requires_review)
        self.assertEqual(result.vat_treatment, VatTreatment.LIMITED_INPUT_VAT)

    def test_bik_applied_zeros_addback(self):
        result = evaluate(
            _ctx(
                kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
                vat_amount='4.67',
                vehicle=_m1(bik=BenefitInKind.APPLIED),
                document=_invoice_registered(),
            )
        )
        self.assertEqual(result.cit_treatment, CitTreatment.EXCLUDED_FROM_VEHICLE_50)
        self.assertEqual(result.cit_addback_ratio, ZERO)
        self.assertIn(ReasonCode.CIT_BIK_APPLIES, result.reason_codes)
        self.assertEqual(result.vat_treatment, VatTreatment.LIMITED_INPUT_VAT)

    def test_missing_cit_facts_review_cit_only(self):
        result = evaluate(
            _ctx(
                kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
                vat_amount='4.67',
                vehicle=VehicleFacts(
                    vehicle_class=VehicleClass.M1,
                    vat_exception_mode=VatExceptionMode.NONE,
                ),
                document=_invoice_registered(),
            )
        )
        self.assertFalse(result.vat_requires_review)
        self.assertTrue(result.cit_requires_review)
        self.assertIsNone(result.cit_addback_ratio)

    def test_p2_review_skips_vat_vehicle_overlay(self):
        result = evaluate(
            _ctx(
                kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
                vat_amount='4.67',
                vehicle=_m1(),
            )
        )
        self.assertTrue(result.vat_requires_review)
        self.assertIn(ReasonCode.VAT_EVIDENCE_INSUFFICIENT, result.reason_codes)
        self.assertNotIn(ReasonCode.VAT_M1_LIMIT, result.reason_codes)
        self.assertEqual(result.cit_treatment, CitTreatment.VEHICLE_50_ADD_BACK)


class DocumentEvidenceTests(SimpleTestCase):
    def test_input_vat_evidence_sufficient_is_three_facts(self):
        ctx = _ctx(
            kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
            vat_amount='4.67',
            document=_invoice_registered(),
        )
        self.assertTrue(input_vat_evidence_sufficient(ctx))
        self.assertFalse(
            input_vat_evidence_sufficient(
                _ctx(
                    kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
                    vat_amount='4.67',
                    document=DocumentFacts(
                        evidence_type=EvidenceType.INVOICE,
                        supplier_vat_status=SupplierVatStatus.UNKNOWN,
                    ),
                )
            )
        )

    def test_null_and_unknown_are_not_invoice(self):
        m1 = _m1()
        for document in (
            DocumentFacts(),
            DocumentFacts(
                evidence_type=EvidenceType.UNKNOWN,
                supplier_vat_status=SupplierVatStatus.REGISTERED,
            ),
        ):
            with self.subTest(document=document):
                result = evaluate(
                    _ctx(
                        kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
                        vat_amount='4.67',
                        vehicle=m1,
                        document=document,
                    )
                )
                self.assertTrue(result.vat_requires_review)
                self.assertIn(ReasonCode.VAT_EVIDENCE_INSUFFICIENT, result.reason_codes)
                self.assertEqual(result.cit_treatment, CitTreatment.VEHICLE_50_ADD_BACK)

    def test_other_evidence_reviews_not_no_input_vat(self):
        result = evaluate(
            _ctx(
                kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
                vat_amount='4.67',
                vehicle=_m1(),
                document=DocumentFacts(
                    evidence_type=EvidenceType.OTHER,
                    supplier_vat_status=SupplierVatStatus.REGISTERED,
                ),
            )
        )
        self.assertTrue(result.vat_requires_review)
        self.assertIsNone(result.vat_treatment)
        self.assertIn(ReasonCode.VAT_EVIDENCE_INSUFFICIENT, result.reason_codes)
        self.assertEqual(result.cit_treatment, CitTreatment.VEHICLE_50_ADD_BACK)

    def test_invoice_exempt_with_vat_is_contradiction(self):
        result = evaluate(
            _ctx(
                kind=VehicleLineKind.TECHNICAL_INSPECTION_SERVICE,
                vat_amount='4.67',
                vehicle=_m1(),
                document=DocumentFacts(
                    evidence_type=EvidenceType.INVOICE,
                    supplier_vat_status=SupplierVatStatus.EXEMPT,
                ),
            )
        )
        self.assertTrue(result.vat_requires_review)
        self.assertIn(ReasonCode.VAT_EVIDENCE_CONTRADICTION, result.reason_codes)
        self.assertNotIn(ReasonCode.VAT_M1_LIMIT, result.reason_codes)
        self.assertEqual(result.cit_treatment, CitTreatment.VEHICLE_50_ADD_BACK)

    def test_hard_expected_ignores_missing_supplier_status(self):
        result = evaluate(
            _ctx(
                kind=VehicleLineKind.INSURANCE_COMPULSORY,
                vat_amount='0.00',
                document=DocumentFacts(
                    evidence_type=EvidenceType.OTHER,
                    supplier_vat_status=None,
                ),
            )
        )
        self.assertEqual(result.vat_treatment, VatTreatment.NO_INPUT_VAT)
        self.assertFalse(result.vat_requires_review)
        self.assertFalse(result.cit_requires_review)
