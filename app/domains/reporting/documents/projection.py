"""VAT lifecycle, operational status, settlement, and controls (ADR-0020)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from accounting.models import VATEntryCategory, VATReturnStatus
from accounting.services.tax_forms.pdv.supply_procedure import VatSupplyProcedure
from domains.finance.services.aging import aging_bucket_for_days
from domains.reporting.documents.provenance import provenanced
from domains.reporting.documents.tax_active import expense_tax_active, invoice_tax_active
from fiscal_gateway.models import As4DocumentLink

MONEY_TOLERANCE = Decimal('0.01')
DISCLAIMER_SUBMITTED = (
    'PDV razdoblje predano – obuhvat ovog računa nije pojedinačno potvrđen'
)

STANDARD_VAT_PROCEDURES = frozenset({VatSupplyProcedure.STANDARD, '', None})
STANDARD_CATEGORIES = frozenset({VATEntryCategory.DOMESTIC, ''})

UNSUPPORTED_VAT_PROCEDURES = frozenset({
    VatSupplyProcedure.OSS,
    VatSupplyProcedure.EU_DISTANCE,
    VatSupplyProcedure.EU_ELECTRONIC,
    VatSupplyProcedure.IOSS,
})
UNSUPPORTED_CATEGORIES = frozenset({
    VATEntryCategory.EU_SUPPLY,
    VATEntryCategory.EU_ACQUISITION,
    VATEntryCategory.OSS_SUPPLY,
    VATEntryCategory.IMPORT,
    VATEntryCategory.ADJUSTMENT,
})

BANK_EXPECTED_PAYMENT_METHODS = frozenset({'bank_transfer'})
BANK_EXPECTED_SETTLEMENT = frozenset({'business_account', 'transfer'})
BANK_EXPECTED_EXPENSE_METHOD = frozenset({'transfer'})


def money(value) -> str | None:
    if value is None:
        return None
    return str(Decimal(value).quantize(Decimal('0.01')))


def _canonical_tax_date(direction: str, document) -> date | None:
    if direction == 'outgoing':
        return document.service_date or None
    return None


def vat_lifecycle(*, direction: str, document, ledger_rows: list) -> dict:
    status = document.status
    active = invoice_tax_active(status) if direction == 'outgoing' else expense_tax_active(status)
    if not active:
        return provenanced('not_tax_active', source='document_status')
    if not ledger_rows:
        return provenanced('awaiting_ledger', source='vat_ledger_entry')
    return provenanced('in_ledger', source='vat_ledger_entry')


def vat_amount_check(*, direction: str, document, ledger_rows: list, items: list | None = None) -> str | None:
    """Return 'mismatch', 'not_provable', or None (ok / skip)."""
    if not ledger_rows:
        return None
    procedures = set()
    if direction == 'outgoing':
        procedures = {item.vat_procedure for item in (items or [])}
    else:
        procedures = {document.vat_procedure}
    categories = {row.entry_category for row in ledger_rows}
    if procedures & UNSUPPORTED_VAT_PROCEDURES or categories & UNSUPPORTED_CATEGORIES:
        return 'not_provable'
    if not procedures.issubset(STANDARD_VAT_PROCEDURES) or not categories.issubset(STANDARD_CATEGORIES):
        return 'not_provable'
    if direction == 'outgoing':
        doc_base = document.subtotal
        doc_vat = document.tax_amount
    else:
        doc_base = document.amount - document.tax_amount
        doc_vat = document.tax_amount
    led_base = sum((row.base_amount for row in ledger_rows), Decimal('0'))
    led_vat = sum((row.vat_amount for row in ledger_rows), Decimal('0'))
    if abs(led_base - doc_base) > MONEY_TOLERANCE or abs(led_vat - doc_vat) > MONEY_TOLERANCE:
        return 'mismatch'
    return None


def vat_period_mismatch(*, direction: str, document, ledger_rows: list) -> bool:
    tax_date = _canonical_tax_date(direction, document)
    if tax_date is None or not ledger_rows:
        return False
    for row in ledger_rows:
        period = row.vat_period
        if period.year != tax_date.year or period.month != tax_date.month:
            return True
    return False


def period_returns_context(vat_returns: list, submission_events_by_return: dict) -> tuple[list[dict], str | None]:
    versions = []
    disclaimer = None
    for vat_return in vat_returns:
        events = submission_events_by_return.get(vat_return.pk, [])
        active_submitted = any(
            event.state == 'submitted' for event in events
        )
        versions.append({
            'id': vat_return.pk,
            'version': vat_return.version,
            'status': vat_return.status,
            'superseded_by_id': vat_return.superseded_by_id,
            'submitted_at': next(
                (event.submitted_at.isoformat() for event in events if event.submitted_at),
                None,
            ),
            'has_active_submission_event': active_submitted,
            'submission_nos': [event.submission_no for event in events],
        })
        if vat_return.status == VATReturnStatus.SUBMITTED and active_submitted:
            disclaimer = DISCLAIMER_SUBMITTED
        if vat_return.status == VATReturnStatus.SUPERSEDED:
            disclaimer = disclaimer or DISCLAIMER_SUBMITTED
    return versions, disclaimer


def build_subledger_block(item, as_of_day: date) -> dict:
    if item is None:
        return {
            'state': provenanced(None, reason='not_recorded'),
            'open_amount': provenanced(None, reason='not_recorded'),
            'original_amount': provenanced(None, reason='not_recorded'),
            'aging_bucket': provenanced(None, reason='not_applicable'),
            'days': provenanced(None, reason='not_applicable'),
        }
    days = (as_of_day - item.due_date).days if item.due_date else 0
    return {
        'state': provenanced(item.status, source='subledger_item'),
        'open_amount': provenanced(money(item.open_amount), source='subledger_item'),
        'original_amount': provenanced(money(item.original_amount), source='subledger_item'),
        'aging_bucket': provenanced(aging_bucket_for_days(days), source='subledger_item'),
        'days': provenanced(days, source='subledger_item'),
    }


def bank_expected(*, direction: str, payments: list, expense=None) -> bool:
    if any(p.payment_method in BANK_EXPECTED_PAYMENT_METHODS for p in payments):
        return True
    if expense is not None:
        if expense.settlement_method in BANK_EXPECTED_SETTLEMENT:
            return True
        if expense.payment_method in BANK_EXPECTED_EXPENSE_METHOD:
            return True
    return False


def has_settlement_evidence(*, allocations, payments, bank_matched: bool, closing_entries) -> bool:
    if allocations:
        return True
    if payments:
        return True
    if bank_matched:
        return True
    if closing_entries:
        return True
    return False


def bank_block(*, match_status: str | None, expected: bool) -> dict:
    if match_status:
        return {
            'match_status': provenanced(match_status, source='bank_transaction'),
        }
    if not expected:
        return {
            'match_status': provenanced(None, reason='not_applicable'),
        }
    return {
        'match_status': provenanced(None, reason='not_recorded'),
    }


def payment_order_block(order) -> dict:
    if order is None:
        return {
            'status': provenanced(None, reason='not_recorded'),
            'creditor_iban': provenanced(None, reason='not_recorded'),
        }
    return {
        'status': provenanced(order.status, source='payment_order'),
        'creditor_iban': provenanced(order.creditor_iban, source='payment_order') if order.creditor_iban else provenanced(None, reason='not_recorded'),
    }


def _subledger_settled(subledger) -> bool:
    """Paid is provable only from a subledger that is closed or has no open amount."""
    if subledger is None or subledger.status == 'cancelled':
        return False
    if subledger.status == 'closed':
        return True
    return subledger.open_amount is not None and subledger.open_amount <= MONEY_TOLERANCE


def _subledger_openish(subledger) -> bool:
    return (
        subledger is not None
        and subledger.status in ('open', 'partial')
        and subledger.open_amount is not None
        and subledger.open_amount > MONEY_TOLERANCE
    )


def operational_outgoing(*, document, subledger, as4_status: str | None, as_of_day: date) -> dict:
    if document.status == 'cancelled':
        return provenanced('cancelled', source='document_read_model')
    if as4_status in {As4DocumentLink.STATUS_REJECTED, As4DocumentLink.STATUS_FAILED}:
        return provenanced('eracun_rejected', source='document_read_model')
    if _subledger_settled(subledger):
        return provenanced('paid', source='subledger_item')
    if subledger is not None and subledger.status == 'partial':
        return provenanced('partially_paid', source='document_read_model')
    if document.status == 'paid' and subledger is None:
        return provenanced(None, reason='not_provable')
    still_open = _subledger_openish(subledger) or (
        subledger is None and document.status not in ('paid', 'cancelled')
    )
    if still_open and document.due_date and document.due_date < as_of_day:
        return provenanced('overdue', source='document_read_model')
    if as4_status == As4DocumentLink.STATUS_ACCEPTED:
        return provenanced('accepted', source='document_read_model')
    if as4_status == As4DocumentLink.STATUS_DELIVERED:
        return provenanced('delivered', source='document_read_model')
    if as4_status == As4DocumentLink.STATUS_SENT:
        return provenanced('sent', source='document_read_model')
    if document.status == 'sent':
        return provenanced('issued', source='document_read_model')
    if document.status == 'draft':
        return provenanced('draft', source='document_read_model')
    if document.status == 'paid':
        return provenanced(None, reason='not_provable')
    return provenanced(document.status, source='document_status')


def operational_incoming(
    *,
    document,
    subledger,
    disputed: bool,
    as_of_day: date,
    has_receipt_evidence: bool,
) -> dict:
    if document.status == 'rejected':
        return provenanced('rejected', source='document_read_model')
    if disputed:
        return provenanced('disputed', source='as4_document_link')
    if _subledger_settled(subledger):
        return provenanced('paid', source='subledger_item')
    if subledger is not None and subledger.status == 'partial':
        return provenanced('partially_paid', source='document_read_model')
    if document.status == 'paid' and subledger is None:
        return provenanced(None, reason='not_provable')
    openish = _subledger_openish(subledger)
    if openish and document.due_date and document.due_date < as_of_day:
        return provenanced('overdue', source='document_read_model')
    if document.status == 'approved' and openish:
        return provenanced('ready_to_pay', source='document_read_model')
    if document.status == 'approved':
        return provenanced('approved', source='document_read_model')
    if document.status == 'submitted':
        return provenanced('awaiting_approval', source='document_read_model')
    if has_receipt_evidence:
        return provenanced('received', source='document_read_model')
    if document.status == 'draft':
        return provenanced('draft', source='document_read_model')
    if document.status == 'paid':
        return provenanced(None, reason='not_provable')
    return provenanced(document.status, source='document_status')


def collect_controls(ctx: dict) -> tuple[list[str], list[str]]:
    alerts: list[str] = []
    notices: list[str] = []
    document = ctx['document']
    direction = ctx['direction']
    partner = ctx['partner']
    subledger = ctx['subledger']
    ledger_rows = ctx['ledger_rows']
    as_of_day: date = ctx['as_of_day']
    as4_status = ctx.get('as4_status')
    posting = ctx.get('posting_entry')
    payments = ctx.get('payments') or []
    allocations = ctx.get('allocations') or []
    bank_matched = bool(ctx.get('bank_matched'))
    closing_entries = ctx.get('closing_entries') or []
    payment_order = ctx.get('payment_order')
    partner_ibans = ctx.get('partner_ibans') or set()
    outbox_incomplete = ctx.get('outbox_incomplete')
    possible_duplicate = ctx.get('possible_duplicate')
    has_pdf_xml = ctx.get('has_pdf_xml')

    oib = (getattr(partner, 'tax_number', None) or '') if partner is not None else ''
    if partner is None or not oib.strip():
        alerts.append('missing_partner_or_oib')
    if not document.due_date:
        alerts.append('missing_due_date')
    if not has_pdf_xml:
        alerts.append('missing_pdf_xml')
    if possible_duplicate:
        alerts.append('possible_duplicate')

    if direction == 'outgoing':
        items = list(ctx.get('items') or [])
        if items:
            line_sum = sum((item.line_total for item in items), Decimal('0'))
            if abs(line_sum - document.subtotal) > MONEY_TOLERANCE:
                alerts.append('line_total_mismatch')
            vat_sum = sum(
                (item.line_total * (item.tax_rate or 0) / Decimal('100') for item in items),
                Decimal('0'),
            )
            if abs(vat_sum - document.tax_amount) > MONEY_TOLERANCE:
                alerts.append('vat_header_mismatch')

    active = invoice_tax_active(document.status) if direction == 'outgoing' else expense_tax_active(document.status)
    if active and not ledger_rows:
        alerts.append('vat_ledger_missing')
    if vat_period_mismatch(direction=direction, document=document, ledger_rows=ledger_rows):
        alerts.append('vat_period_mismatch')
    vat_check = vat_amount_check(
        direction=direction,
        document=document,
        ledger_rows=ledger_rows,
        items=ctx.get('items') or [],
    )
    if vat_check == 'mismatch':
        alerts.append('vat_amount_mismatch')

    issued = document.status in ('sent', 'paid', 'overdue', 'approved')
    if issued and (posting is None or posting.status != 'posted'):
        alerts.append('issued_unposted')
    if posting is not None:
        debit = posting.total_debit
        credit = posting.total_credit
        if abs(debit - credit) > MONEY_TOLERANCE:
            alerts.append('journal_unbalanced')

    openish = _subledger_openish(subledger)
    if openish and document.due_date and document.due_date < as_of_day:
        alerts.append('overdue_unpaid')
    if document.status == 'paid':
        if subledger is None:
            alerts.append('paid_status_subledger_missing')
        elif subledger.status in ('open', 'partial') and (
            subledger.open_amount is not None and subledger.open_amount > MONEY_TOLERANCE
        ):
            alerts.append('paid_status_subledger_open')

    if subledger is not None and subledger.status == 'closed':
        if not has_settlement_evidence(
            allocations=allocations,
            payments=payments,
            bank_matched=bank_matched,
            closing_entries=closing_entries,
        ):
            alerts.append('settlement_evidence_missing')

    expected_bank = bank_expected(direction=direction, payments=payments, expense=document if direction == 'incoming' else None)
    if expected_bank and not bank_matched and 'settlement_evidence_missing' not in alerts:
        # Informational unpaid bank confirmation — not bank_unmatched alarm when other evidence exists.
        pass

    if payment_order and payment_order.creditor_iban:
        if not partner_ibans:
            pass  # not_recorded, not a mismatch
        elif _norm_iban(payment_order.creditor_iban) not in partner_ibans:
            alerts.append('iban_mismatch')

    if as4_status in {As4DocumentLink.STATUS_REJECTED, As4DocumentLink.STATUS_FAILED}:
        alerts.append('eracun_rejected')
    if outbox_incomplete:
        alerts.append('outbox_incomplete')

    if posting is not None and posting.posted_at and document.updated_at and document.updated_at > posting.posted_at:
        notices.append('updated_after_posting')

    return alerts, notices


def _norm_iban(iban: str) -> str:
    return (iban or '').replace(' ', '').upper()


def posting_block(entry) -> dict:
    if entry is None:
        return {
            'state': provenanced('not_posted', source='journal_entry'),
            'entry_number': provenanced(None, reason='not_recorded'),
            'entry_date': provenanced(None, reason='not_recorded'),
            'fiscal_period': provenanced(None, reason='not_recorded'),
            'fiscal_locked': provenanced(None, reason='not_applicable'),
        }
    period = entry.fiscal_period
    locked = bool(period and period.status == 'closed')
    return {
        'state': provenanced(entry.status, source='journal_entry'),
        'entry_number': provenanced(entry.entry_number, source='journal_entry'),
        'entry_date': provenanced(entry.entry_date.isoformat(), source='journal_entry'),
        'fiscal_period': (
            provenanced(f'{period.year:04d}-{period.month:02d}', source='fiscal_period')
            if period else provenanced(None, reason='not_recorded')
        ),
        'fiscal_locked': provenanced(locked, source='fiscal_period') if period else provenanced(None, reason='not_applicable'),
    }
