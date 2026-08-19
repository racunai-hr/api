"""Reversal tax-relevance assessment for PDV projection adapters.

Adapter facts only: GFK/workflow, account structure, posting marker, legacy
description. Engine decides outcomes from TaxRelevance / OriginTaxOwner.
"""

from __future__ import annotations

from dataclasses import dataclass

from accounting.models import JournalEntry
from accounting.services.journal_markers import extract_document_type
from domains.tax.classification.accounts import is_tax_family_account
from domains.tax.classification.contracts import OriginTaxOwner, TaxRelevance

# Cash / bank / AR clearing — payment JE without VAT payable.
_CLEARING_PREFIXES = ('100', '102', '120')
_EU_ASSET_PPMV_DUAL = frozenset({'0373'})


def tenant_uses_cash_accounting(tenant_id: int) -> bool:
    """Accrual default. Cash/collected scheme must opt in explicitly later."""
    del tenant_id
    return False


def _account_codes(original: JournalEntry) -> tuple[str, ...]:
    return tuple(
        line.account.account_code
        for line in original.lines.select_related('account')
        if line.account_id and line.account.account_code
    )


def _gfk_model(original: JournalEntry) -> tuple[str | None, str | None]:
    ct = original.source_content_type
    if ct is None:
        return None, None
    return ct.app_label, ct.model


def _is_clearing_account(code: str) -> bool:
    return code.startswith(_CLEARING_PREFIXES)


def _pdv_tax_family_codes(codes: tuple[str, ...]) -> frozenset[str]:
    """Tax-family accounts that imply a PDV event for reversal relevance.

    0373 is dual-use (EU acquisition vs PPMV). Alone with clearing accounts it
    is not treated as conclusive PDV ownership here; see PPMV path.
    """
    found = {code for code in codes if is_tax_family_account(code)}
    if found <= _EU_ASSET_PPMV_DUAL:
        return frozenset()
    return frozenset(found)


def _looks_like_payment_clearing(codes: tuple[str, ...]) -> bool:
    if not codes:
        return False
    return all(_is_clearing_account(code) for code in codes)


def _looks_like_ppmv_payment(codes: tuple[str, ...], description: str) -> bool:
    """0373 + clearing only; description PPMV is last legacy confirm (not sole authority)."""
    if 'PPMV' not in (description or '').upper():
        return False
    has_0373 = any(code in _EU_ASSET_PPMV_DUAL for code in codes)
    has_clearing = any(_is_clearing_account(code) for code in codes)
    other = [
        code
        for code in codes
        if code not in _EU_ASSET_PPMV_DUAL and not _is_clearing_account(code)
    ]
    return has_0373 and has_clearing and not other


@dataclass(frozen=True)
class ReversalRelevanceAssessment:
    tax_relevance: TaxRelevance
    origin_tax_owner: OriginTaxOwner


def assess_reversal_relevance(
    original: JournalEntry | None,
    *,
    effects_present: bool,
    effects_ambiguous: bool,
) -> ReversalRelevanceAssessment:
    if original is None:
        return ReversalRelevanceAssessment(TaxRelevance.UNDETERMINED, OriginTaxOwner.NONE)

    if effects_ambiguous:
        return ReversalRelevanceAssessment(TaxRelevance.TAX_RELEVANT, OriginTaxOwner.JOURNAL_LINE)

    if effects_present:
        return ReversalRelevanceAssessment(TaxRelevance.TAX_RELEVANT, OriginTaxOwner.JOURNAL_LINE)

    codes = _account_codes(original)
    app_label, model = _gfk_model(original)
    is_invoice = app_label == 'invoices' and model == 'invoice'
    is_expense = app_label == 'expenses' and model == 'expense'
    marker = extract_document_type(original.description or '')
    pdv_codes = _pdv_tax_family_codes(codes)
    has_pdv_family = bool(pdv_codes)
    cash = tenant_uses_cash_accounting(original.tenant_id)
    description = original.description or ''

    # --- invoice_paid / payment clearing ---
    payment_by_marker = marker == 'invoice_paid'
    payment_by_structure = is_invoice and _looks_like_payment_clearing(codes) and not has_pdv_family
    if payment_by_marker or payment_by_structure:
        if has_pdv_family:
            return ReversalRelevanceAssessment(TaxRelevance.UNDETERMINED, OriginTaxOwner.INVOICE)
        if marker and marker != 'invoice_paid' and payment_by_structure:
            return ReversalRelevanceAssessment(TaxRelevance.UNDETERMINED, OriginTaxOwner.INVOICE)
        if payment_by_marker and is_invoice is False and not _looks_like_payment_clearing(codes):
            return ReversalRelevanceAssessment(TaxRelevance.UNDETERMINED, OriginTaxOwner.NONE)
        if cash:
            return ReversalRelevanceAssessment(TaxRelevance.UNDETERMINED, OriginTaxOwner.INVOICE)
        return ReversalRelevanceAssessment(TaxRelevance.NOT_TAX_RELEVANT, OriginTaxOwner.NONE)

    # --- PPMV-style non-PDV (0373 dual-use + clearing; no GFK owner) ---
    if _looks_like_ppmv_payment(codes, description) and not is_invoice and not is_expense:
        if has_pdv_family:
            return ReversalRelevanceAssessment(TaxRelevance.UNDETERMINED, OriginTaxOwner.NONE)
        unexpected_pdv = [c for c in codes if is_tax_family_account(c) and c not in _EU_ASSET_PPMV_DUAL]
        if unexpected_pdv:
            return ReversalRelevanceAssessment(TaxRelevance.UNDETERMINED, OriginTaxOwner.NONE)
        return ReversalRelevanceAssessment(TaxRelevance.NOT_TAX_RELEVANT, OriginTaxOwner.NONE)

    # --- invoice_issued without JE-line ledger ownership (e.g. Fine Star JE 36) ---
    issued_by_marker = marker == 'invoice_issued'
    if is_invoice and (issued_by_marker or has_pdv_family):
        # Active invoice supply remains on Invoice CT; JE storno is undetermined
        # until Gate 0 proves corrective document or technical repost → NTR.
        return ReversalRelevanceAssessment(TaxRelevance.UNDETERMINED, OriginTaxOwner.INVOICE)

    if is_expense and (marker == 'expense_approved' or has_pdv_family):
        return ReversalRelevanceAssessment(TaxRelevance.UNDETERMINED, OriginTaxOwner.EXPENSE)

    # --- journal-owned VAT without recoverable line evidence ---
    if has_pdv_family:
        return ReversalRelevanceAssessment(TaxRelevance.TAX_RELEVANT, OriginTaxOwner.JOURNAL_LINE)

    # Non-tax accounts, no document owner
    if not is_invoice and not is_expense and not has_pdv_family:
        if marker in {None, 'manual'} or marker not in {
            'invoice_issued',
            'invoice_paid',
            'expense_approved',
            'expense_paid',
        }:
            return ReversalRelevanceAssessment(TaxRelevance.NOT_TAX_RELEVANT, OriginTaxOwner.NONE)

    return ReversalRelevanceAssessment(TaxRelevance.UNDETERMINED, OriginTaxOwner.NONE)
