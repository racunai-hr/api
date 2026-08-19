"""Usklađivanje bankovnih transakcija s plaćanjima i temeljnicama."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import JournalEntry, JournalEntryLine
from banking.ledger import resolve_bank_ledger_account
from banking.models import BankTransaction
from payments.models import Payment

MATCH_DATE_TOLERANCE_DAYS = 0


def _ledger_line_matches_transaction(
    line: JournalEntryLine,
    ledger_account_id: int,
    bank_tx: BankTransaction,
) -> bool:
    if line.account_id != ledger_account_id:
        return False
    if bank_tx.transaction_type == 'debit':
        return line.credit_amount == bank_tx.amount and line.debit_amount == Decimal('0')
    if bank_tx.transaction_type == 'credit':
        return line.debit_amount == bank_tx.amount and line.credit_amount == Decimal('0')
    return False


def _matching_ledger_lines(
    journal_entry: JournalEntry,
    ledger_account_id: int,
    bank_tx: BankTransaction,
) -> list[JournalEntryLine]:
    return [
        line
        for line in journal_entry.lines.select_related('account').all()
        if _ledger_line_matches_transaction(line, ledger_account_id, bank_tx)
    ]


def _find_matching_ledger_line(
    journal_entry: JournalEntry,
    ledger_account_id: int,
    bank_tx: BankTransaction,
) -> JournalEntryLine | None:
    matches = _matching_ledger_lines(journal_entry, ledger_account_id, bank_tx)
    if len(matches) == 1:
        return matches[0]
    return None


def _lock_bank_transaction(bank_tx_id: int) -> BankTransaction:
    return (
        BankTransaction.all_objects.select_for_update()
        .select_related('bank_statement', 'bank_statement__bank_account', 'tenant')
        .get(pk=bank_tx_id)
    )


def _expected_payment_type(bank_tx: BankTransaction) -> str:
    return 'incoming' if bank_tx.transaction_type == 'credit' else 'outgoing'


def _validate_payment_match(bank_tx: BankTransaction, payment: Payment) -> None:
    if bank_tx.tenant_id != payment.tenant_id:
        raise ValidationError('Plaćanje i bankovna transakcija moraju biti istog tenant-a.')
    if payment.currency != bank_tx.currency:
        raise ValidationError(
            f'Valuta plaćanja ({payment.currency}) mora odgovarati '
            f'valuti transakcije ({bank_tx.currency}).'
        )
    if payment.amount != bank_tx.amount:
        raise ValidationError(
            f'Iznos plaćanja ({payment.amount}) mora biti jednak '
            f'iznosu transakcije ({bank_tx.amount}).'
        )
    expected = _expected_payment_type(bank_tx)
    if payment.payment_type != expected:
        raise ValidationError(
            f'Smjer plaćanja ({payment.payment_type}) ne odgovara '
            f'tipu transakcije ({bank_tx.transaction_type}); očekivano {expected}.'
        )


def _validate_journal_match(bank_tx: BankTransaction, journal_entry: JournalEntry) -> None:
    if bank_tx.tenant_id != journal_entry.tenant_id:
        raise ValidationError('Temeljnica i bankovna transakcija moraju biti istog tenant-a.')
    if journal_entry.status != 'posted':
        raise ValidationError('Samo knjižena temeljnica se može uskladiti s bankovnom transakcijom.')
    if bank_tx.matched_payment_id:
        raise ValidationError('Transakcija je već usklađena s plaćanjem.')
    if abs((journal_entry.entry_date - bank_tx.transaction_date).days) > MATCH_DATE_TOLERANCE_DAYS:
        raise ValidationError(
            f'Datum temeljnice ({journal_entry.entry_date}) mora odgovarati '
            f'datumu transakcije ({bank_tx.transaction_date}).'
        )
    ledger_account = resolve_bank_ledger_account(bank_tx.bank_statement.bank_account)
    matches = _matching_ledger_lines(journal_entry, ledger_account.id, bank_tx)
    if not matches:
        raise ValidationError(
            f'Temeljnica nema odgovarajuću stavku na kontu {ledger_account.account_code} '
            f'za iznos {bank_tx.amount}.'
        )
    if len(matches) > 1:
        raise ValidationError(
            f'Temeljnica ima {len(matches)} odgovarajuće stavke na kontu '
            f'{ledger_account.account_code}; usklađivanje zahtijeva točno jednu.'
        )


def _assert_payment_not_linked_elsewhere(bank_tx: BankTransaction, payment: Payment) -> None:
    conflict = (
        BankTransaction.all_objects.filter(matched_payment=payment)
        .exclude(pk=bank_tx.pk)
        .exists()
    )
    if conflict:
        raise ValidationError('Plaćanje je već povezano s drugom bankovnom transakcijom.')


def _assert_journal_not_linked_elsewhere(
    bank_tx: BankTransaction,
    journal_entry: JournalEntry,
) -> None:
    conflict = (
        BankTransaction.all_objects.filter(matched_journal_entry=journal_entry)
        .exclude(pk=bank_tx.pk)
        .exists()
    )
    if conflict:
        raise ValidationError('Temeljnica je već povezana s drugom bankovnom transakcijom.')


def suggest_matches(tenant, *, days_tolerance: int = 3) -> int:
    updated = 0
    linked_payment_ids = BankTransaction.all_objects.filter(
        matched_payment_id__isnull=False,
    ).values_list('matched_payment_id', flat=True)

    for bank_tx in BankTransaction.all_objects.filter(tenant=tenant, match_status='unmatched'):
        date_from = bank_tx.transaction_date - timedelta(days=days_tolerance)
        date_to = bank_tx.transaction_date + timedelta(days=days_tolerance)
        expected_type = _expected_payment_type(bank_tx)

        payments = Payment.all_objects.filter(
            tenant=tenant,
            status='completed',
            payment_type=expected_type,
            amount=bank_tx.amount,
            currency=bank_tx.currency,
            payment_date__gte=date_from,
            payment_date__lte=date_to,
        ).exclude(pk__in=linked_payment_ids)
        if bank_tx.reference:
            payments = payments.filter(reference_number__icontains=bank_tx.reference[:20])
        if bank_tx.counterparty_iban:
            payments = payments.filter(counterparty_account__icontains=bank_tx.counterparty_iban[-10:])

        payment = payments.first()
        if payment:
            bank_tx.match_status = 'suggested'
            bank_tx.matched_payment = payment
            bank_tx.save(update_fields=['match_status', 'matched_payment'])
            updated += 1
    return updated


def suggest_journal_matches(tenant, *, days_tolerance: int = 0) -> int:
    updated = 0
    for bank_tx in BankTransaction.all_objects.filter(
        tenant=tenant,
        match_status='unmatched',
        matched_journal_entry__isnull=True,
        matched_payment__isnull=True,
    ).select_related('bank_statement__bank_account'):
        date_from = bank_tx.transaction_date - timedelta(days=days_tolerance)
        date_to = bank_tx.transaction_date + timedelta(days=days_tolerance)
        ledger_account = resolve_bank_ledger_account(bank_tx.bank_statement.bank_account)

        candidates = JournalEntry.all_objects.filter(
            tenant=tenant,
            status='posted',
            entry_date__gte=date_from,
            entry_date__lte=date_to,
            matched_bank_transactions__isnull=True,
        ).prefetch_related('lines')

        for entry in candidates:
            if _find_matching_ledger_line(entry, ledger_account.id, bank_tx):
                bank_tx.match_status = 'suggested'
                bank_tx.matched_journal_entry = entry
                bank_tx.save(update_fields=['match_status', 'matched_journal_entry'])
                updated += 1
                break
    return updated


@transaction.atomic
def match_transaction(bank_tx: BankTransaction, payment: Payment) -> BankTransaction:
    locked_tx = _lock_bank_transaction(bank_tx.pk)

    if locked_tx.matched_journal_entry_id:
        raise ValidationError('Transakcija je već usklađena s temeljnicom. Prvo poništi usklađenje.')
    if (
        locked_tx.match_status == 'matched'
        and locked_tx.matched_payment_id
        and locked_tx.matched_payment_id != payment.pk
    ):
        raise ValidationError('Transakcija je već usklađena s plaćanjem. Prvo poništi usklađenje.')

    locked_payment = Payment.all_objects.select_for_update().get(pk=payment.pk)
    _validate_payment_match(locked_tx, locked_payment)
    _assert_payment_not_linked_elsewhere(locked_tx, locked_payment)

    if (
        locked_tx.match_status == 'matched'
        and locked_tx.matched_payment_id == locked_payment.pk
    ):
        return locked_tx

    locked_tx.matched_payment = locked_payment
    locked_tx.matched_journal_entry = None
    locked_tx.match_status = 'matched'
    locked_tx.save(update_fields=['matched_payment', 'matched_journal_entry', 'match_status'])
    locked_tx.bank_statement.update_reconciliation_status()
    _post_reconciliation_if_needed(locked_tx, locked_payment)
    return locked_tx


@transaction.atomic
def match_transaction_to_journal_entry(
    bank_tx: BankTransaction,
    journal_entry: JournalEntry,
    user,
) -> BankTransaction:
    locked_tx = _lock_bank_transaction(bank_tx.pk)

    if (
        locked_tx.matched_journal_entry_id == journal_entry.id
        and locked_tx.match_status == 'matched'
    ):
        return locked_tx
    if locked_tx.matched_journal_entry_id and locked_tx.matched_journal_entry_id != journal_entry.id:
        raise ValidationError(
            'Transakcija je već usklađena s drugom temeljnicom. Prvo poništi usklađenje.'
        )
    if locked_tx.matched_payment_id:
        raise ValidationError('Transakcija je već usklađena s plaćanjem. Prvo poništi usklađenje.')

    locked_entry = JournalEntry.all_objects.select_for_update().get(pk=journal_entry.pk)
    _validate_journal_match(locked_tx, locked_entry)
    _assert_journal_not_linked_elsewhere(locked_tx, locked_entry)

    locked_tx.matched_journal_entry = locked_entry
    locked_tx.match_status = 'matched'
    locked_tx.save(update_fields=['matched_journal_entry', 'match_status'])
    locked_tx.bank_statement.update_reconciliation_status()
    return locked_tx


@transaction.atomic
def unmatch_transaction(bank_tx: BankTransaction, user) -> BankTransaction:
    locked_tx = _lock_bank_transaction(bank_tx.pk)

    if (
        locked_tx.match_status == 'unmatched'
        and not locked_tx.matched_journal_entry_id
        and not locked_tx.matched_payment_id
    ):
        return locked_tx

    if locked_tx.matched_payment_id:
        Payment.all_objects.select_for_update().filter(pk=locked_tx.matched_payment_id).first()
    if locked_tx.matched_journal_entry_id:
        JournalEntry.all_objects.select_for_update().filter(
            pk=locked_tx.matched_journal_entry_id,
        ).first()

    locked_tx.matched_journal_entry = None
    locked_tx.matched_payment = None
    locked_tx.match_status = 'unmatched'
    locked_tx.save(update_fields=['matched_journal_entry', 'matched_payment', 'match_status'])
    locked_tx.bank_statement.update_reconciliation_status()
    return locked_tx


def _post_reconciliation_if_needed(bank_tx: BankTransaction, payment: Payment) -> None:
    """Knjiženje nakon usklađivanja ako je plaćanje vezano uz račun."""
    if not payment.related_invoice_id:
        return
    invoice = payment.related_invoice
    if invoice.status != 'paid':
        return
    from django.contrib.auth import get_user_model

    from accounting.services.posting import post_invoice_payment

    user = get_user_model().objects.filter(is_superuser=True).first()
    if user is None:
        return
    post_invoice_payment(bank_tx.tenant, invoice, payment, user)


@transaction.atomic
def create_payment_from_transaction(bank_tx: BankTransaction, user) -> Payment:
    locked_tx = _lock_bank_transaction(bank_tx.pk)
    if locked_tx.matched_journal_entry_id:
        raise ValidationError('Transakcija je već usklađena s temeljnicom.')
    if locked_tx.match_status == 'matched' and locked_tx.matched_payment_id:
        raise ValidationError('Transakcija je već usklađena s plaćanjem.')

    payment_type = _expected_payment_type(locked_tx)
    payment = Payment.all_objects.create(
        tenant=locked_tx.tenant,
        payment_number=f"BTX-{locked_tx.pk:06d}",
        payment_type=payment_type,
        payment_method='bank_transfer',
        status='completed',
        amount=locked_tx.amount,
        currency=locked_tx.currency,
        bank_account=locked_tx.bank_statement.bank_account,
        payment_date=locked_tx.transaction_date,
        value_date=locked_tx.value_date,
        description=locked_tx.description,
        reference_number=locked_tx.reference,
        counterparty_name=locked_tx.counterparty_name,
        counterparty_account=locked_tx.counterparty_iban,
        created_by=user,
    )
    match_transaction(locked_tx, payment)
    return payment
