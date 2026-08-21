"""Settle open SubledgerItem from an explicit bank reconcile (ADR-0025).

Caller (Banking orchestrator) owns the outer ``transaction.atomic()`` and lock order:
BankTransaction → SubledgerItem → source. Pass ``already_locked=True`` sources.
Amount guards run on the locked SubledgerItem row.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from accounting.models import Deposit, JournalEntry, JournalEntryLine, PrivateFundsClaim, SubledgerItem
from accounting.services.analytics import get_or_create_analytic_for_partner
from accounting.services.posting import (
    get_or_create_fiscal_period,
    resolve_account,
    _next_entry_number,
)
from domains.finance.services.deposits import (
    DepositBadRequest,
    DepositConflict,
    return_deposit,
)
from domains.finance.services.subledger import allocate_payment, get_subledger_item_for_source
from expenses.models import Expense
from invoices.models import Invoice
from payments.models import BankAccount, Payment


class SettlementConflict(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class SettlementBadRequest(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SettlementResult:
    journal_entry: JournalEntry
    source_type: str
    source_id: int


def bank_reconcile_marker(bank_transaction_id: int) -> str:
    return f'[bank_reconcile:btx-{bank_transaction_id}]'


def _bank_ledger_account(tenant, bank: BankAccount):
    if bank.ledger_account_id and bank.ledger_account:
        return bank.ledger_account
    return resolve_account(tenant, '1000')


def _money(value) -> Decimal:
    amount = Decimal(str(value)).quantize(Decimal('0.01'))
    if amount <= Decimal('0'):
        raise SettlementBadRequest('invalid_amount', 'Iznos mora biti veći od nule.')
    return amount


def _next_payment_number(tenant) -> str:
    prefix = timezone.now().strftime('PAY-%Y%m-')
    last = (
        Payment.all_objects.filter(tenant=tenant, payment_number__startswith=prefix)
        .order_by('-payment_number')
        .values_list('payment_number', flat=True)
        .first()
    )
    seq = 0
    if last:
        suffix = last.rsplit('-', 1)[-1]
        if suffix.isdigit():
            seq = int(suffix)
    return f'{prefix}{seq + 1:04d}'


def _existing_btx_entry(*, tenant, bank_transaction_id: int) -> JournalEntry | None:
    marker = bank_reconcile_marker(bank_transaction_id)
    return (
        JournalEntry.all_objects.filter(
            tenant=tenant,
            description__startswith=marker,
            status__in=['draft', 'posted'],
        )
        .order_by('id')
        .first()
    )


def _mark_expense_paid_if_closed(*, tenant, expense: Expense) -> None:
    item = get_subledger_item_for_source(tenant, expense)
    if item is None or item.status != 'closed':
        return
    if expense.status == 'paid':
        return
    expense.status = 'paid'
    expense.save(update_fields=['status', 'updated_at'])


def settle_open_item_from_bank(
    *,
    tenant,
    user,
    subledger_item: SubledgerItem,
    bank_account: BankAccount,
    settlement_amount: Decimal,
    settlement_date,
    transaction_type: str,
    bank_transaction_id: int,
    locked_source=None,
) -> SettlementResult:
    """Finance use-case: settle open item and return the settlement JE for bank match.

    ``subledger_item`` and its source must already be locked by the caller.
    Amount is validated against the locked row (partial Invoice/Expense; full Deposit).
    """
    amount = _money(settlement_amount)
    if not bank_transaction_id:
        raise SettlementBadRequest('bank_tx_required', 'bank_transaction_id je obavezan.')
    if subledger_item.tenant_id != tenant.id:
        raise SettlementConflict('tenant_mismatch', 'Stavka ne pripada tenantu.')
    if subledger_item.status not in ('open', 'partial'):
        raise SettlementConflict('invalid_item_status', 'Stavka nije otvorena.')

    expected_direction = 'receivable' if transaction_type == 'credit' else 'payable'
    if subledger_item.direction != expected_direction:
        raise SettlementBadRequest('direction_mismatch', 'Smjer bankovne stavke ne odgovara saldakontu.')

    source = locked_source if locked_source is not None else subledger_item.source
    if source is None:
        raise SettlementConflict('missing_source', 'Izvor stavke nije dostupan.')

    model = subledger_item.source_content_type.model
    if model == 'deposit':
        if amount != subledger_item.open_amount:
            raise SettlementBadRequest(
                'full_amount_required',
                'Povrat kaucije zahtijeva puni iznos otvorene stavke.',
            )
        if transaction_type != 'credit':
            raise SettlementBadRequest('direction_mismatch', 'Povrat kaucije zahtijeva odobrenje.')
        try:
            return_deposit(
                tenant=tenant,
                user=user,
                deposit=source,
                already_locked=True,
                data={
                    'return_bank_account_id': bank_account.pk,
                    'return_date': settlement_date,
                    'amount': str(amount),
                },
            )
        except DepositConflict as exc:
            raise SettlementConflict(exc.code, exc.detail) from exc
        except DepositBadRequest as exc:
            raise SettlementBadRequest(exc.code, exc.detail) from exc
        source.refresh_from_db()
        entry = source.return_journal_entry
        if entry is None:
            raise SettlementConflict('missing_return_je', 'Nedostaje JE povrata.')
        return SettlementResult(journal_entry=entry, source_type='deposit', source_id=source.pk)

    if amount > subledger_item.open_amount:
        raise SettlementBadRequest(
            'amount_exceeds_open',
            'Iznos bankovne stavke premašuje otvoreni iznos saldakonta.',
        )

    existing = _existing_btx_entry(tenant=tenant, bank_transaction_id=bank_transaction_id)
    if existing is not None:
        return SettlementResult(
            journal_entry=existing,
            source_type=model,
            source_id=source.pk,
        )

    if model == 'invoice':
        if transaction_type != 'credit':
            raise SettlementBadRequest('direction_mismatch', 'Potraživanje zahtijeva odobrenje.')
        entry = _settle_invoice(
            tenant=tenant,
            user=user,
            invoice=source,
            bank_account=bank_account,
            amount=amount,
            settlement_date=settlement_date,
            bank_transaction_id=bank_transaction_id,
        )
        return SettlementResult(journal_entry=entry, source_type='invoice', source_id=source.pk)

    if model == 'expense':
        if transaction_type != 'debit':
            raise SettlementBadRequest('direction_mismatch', 'Obveza zahtijeva terećenje.')
        entry = _settle_expense(
            tenant=tenant,
            user=user,
            expense=source,
            bank_account=bank_account,
            amount=amount,
            settlement_date=settlement_date,
            bank_transaction_id=bank_transaction_id,
        )
        return SettlementResult(journal_entry=entry, source_type='expense', source_id=source.pk)

    if model == 'privatefundsclaim':
        if transaction_type != 'debit':
            raise SettlementBadRequest('direction_mismatch', 'Obveza zahtijeva terećenje.')
        entry = _settle_private_funds_claim(
            tenant=tenant,
            user=user,
            claim=source,
            bank_account=bank_account,
            amount=amount,
            settlement_date=settlement_date,
            bank_transaction_id=bank_transaction_id,
        )
        return SettlementResult(
            journal_entry=entry,
            source_type='privatefundsclaim',
            source_id=source.pk,
        )

    raise SettlementBadRequest('unsupported_source', f'Nepodržan tip izvora: {model}')


def _settle_invoice(
    *,
    tenant,
    user,
    invoice: Invoice,
    bank_account,
    amount,
    settlement_date,
    bank_transaction_id: int,
):
    payment = Payment.all_objects.create(
        tenant=tenant,
        payment_number=_next_payment_number(tenant),
        payment_type='incoming',
        payment_method='bank_transfer',
        status='completed',
        amount=amount,
        currency=getattr(invoice, 'currency', None) or 'EUR',
        bank_account=bank_account,
        related_invoice=invoice,
        payment_date=settlement_date,
        description=f'Bank reconcile — {invoice.invoice_number}',
        created_by=user if getattr(user, 'is_authenticated', False) else None,
    )
    partner = invoice.company_to
    if partner is None:
        raise SettlementBadRequest('missing_partner', 'Račun nema partnera.')
    bank_coa = _bank_ledger_account(tenant, bank_account)
    analytic = get_or_create_analytic_for_partner(tenant, partner)
    customer = analytic.chart_account
    marker = bank_reconcile_marker(bank_transaction_id)
    entry = JournalEntry.all_objects.create(
        tenant=tenant,
        entry_number=_next_entry_number(tenant, settlement_date),
        entry_date=settlement_date,
        status='draft',
        description=f'{marker} invoice-{invoice.pk} Bank reconcile — {invoice.invoice_number}',
        reference=invoice.invoice_number,
        is_auto=True,
        source_content_type=ContentType.objects.get_for_model(Payment),
        source_object_id=payment.pk,
        fiscal_period=get_or_create_fiscal_period(tenant, settlement_date),
        created_by=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=entry,
        account=bank_coa,
        description='Naplata — banka',
        debit_amount=amount,
        credit_amount=Decimal('0'),
    )
    JournalEntryLine.objects.create(
        journal_entry=entry,
        account=customer,
        analytic_account=analytic,
        description='Naplata — kupci',
        debit_amount=Decimal('0'),
        credit_amount=amount,
    )
    entry.post(user)
    allocated = allocate_payment(tenant, source=invoice, journal_entry=entry, amount=amount)
    if allocated is None:
        raise SettlementConflict('allocation_failed', 'Alokacija saldakonta nije uspjela.')
    return entry


def _settle_expense(
    *,
    tenant,
    user,
    expense: Expense,
    bank_account,
    amount,
    settlement_date,
    bank_transaction_id: int,
):
    partner = expense.supplier
    if partner is None:
        raise SettlementBadRequest('missing_partner', 'Trošak nema dobavljača.')
    bank_coa = _bank_ledger_account(tenant, bank_account)
    analytic = get_or_create_analytic_for_partner(tenant, partner, synthetic_code='2201')
    payable = analytic.chart_account
    marker = bank_reconcile_marker(bank_transaction_id)
    entry = JournalEntry.all_objects.create(
        tenant=tenant,
        entry_number=_next_entry_number(tenant, settlement_date),
        entry_date=settlement_date,
        status='draft',
        description=f'{marker} expense-{expense.pk} Bank reconcile — {expense.expense_number}',
        reference=expense.expense_number or '',
        is_auto=True,
        source_content_type=ContentType.objects.get_for_model(Expense),
        source_object_id=expense.pk,
        fiscal_period=get_or_create_fiscal_period(tenant, settlement_date),
        created_by=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=entry,
        account=payable,
        analytic_account=analytic,
        description='Plaćanje obveze',
        debit_amount=amount,
        credit_amount=Decimal('0'),
    )
    JournalEntryLine.objects.create(
        journal_entry=entry,
        account=bank_coa,
        description='Plaćanje — banka',
        debit_amount=Decimal('0'),
        credit_amount=amount,
    )
    entry.post(user)
    allocated = allocate_payment(tenant, source=expense, journal_entry=entry, amount=amount)
    if allocated is None:
        raise SettlementConflict('allocation_failed', 'Alokacija saldakonta nije uspjela.')
    _mark_expense_paid_if_closed(tenant=tenant, expense=expense)
    return entry


def _settle_private_funds_claim(
    *,
    tenant,
    user,
    claim: PrivateFundsClaim,
    bank_account,
    amount,
    settlement_date,
    bank_transaction_id: int,
):
    """Refund private funds payable: Dr 2309-P / Cr bank + allocate claim item."""
    if claim.status != PrivateFundsClaim.STATUS_POSTED:
        raise SettlementBadRequest('claim_not_posted', 'Claim mora biti proknjižen.')
    partner = claim.partner
    if partner is None:
        raise SettlementBadRequest('missing_partner', 'Claim nema partnera.')
    bank_coa = _bank_ledger_account(tenant, bank_account)
    analytic = get_or_create_analytic_for_partner(tenant, partner, synthetic_code='2309')
    payable = analytic.chart_account
    marker = bank_reconcile_marker(bank_transaction_id)
    entry = JournalEntry.all_objects.create(
        tenant=tenant,
        entry_number=_next_entry_number(tenant, settlement_date),
        entry_date=settlement_date,
        status='draft',
        description=f'{marker} private_funds-{claim.pk} Refund — {claim.number}',
        reference=claim.number or '',
        is_auto=True,
        source_content_type=ContentType.objects.get_for_model(PrivateFundsClaim),
        source_object_id=claim.pk,
        fiscal_period=get_or_create_fiscal_period(tenant, settlement_date),
        created_by=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=entry,
        account=payable,
        analytic_account=analytic,
        description='Povrat privatnih sredstava',
        debit_amount=amount,
        credit_amount=Decimal('0'),
    )
    JournalEntryLine.objects.create(
        journal_entry=entry,
        account=bank_coa,
        description='Povrat — banka',
        debit_amount=Decimal('0'),
        credit_amount=amount,
    )
    entry.post(user)
    allocated = allocate_payment(tenant, source=claim, journal_entry=entry, amount=amount)
    if allocated is None:
        raise SettlementConflict('allocation_failed', 'Alokacija saldakonta nije uspjela.')
    return entry
