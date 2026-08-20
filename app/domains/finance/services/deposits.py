"""Deposit / kaucija lifecycle (ADR-0024)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone

from accounting.models import Deposit, JournalEntry, JournalEntryLine
from accounting.services.posting import (
    get_or_create_fiscal_period,
    resolve_account,
    _next_entry_number,
)
from domains.finance.services.subledger import (
    allocate_payment,
    create_subledger_item,
    get_subledger_item_for_source,
)
from partners.models import Partner
from payments.models import BankAccount


class DepositConflict(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class DepositBadRequest(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _money(value) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise DepositBadRequest('invalid_amount', 'Iznos mora biti decimalni broj.') from exc
    if amount <= Decimal('0'):
        raise DepositBadRequest('invalid_amount', 'Iznos mora biti veći od nule.')
    return amount.quantize(Decimal('0.01'))


def _lock_deposit(tenant, deposit_id: int) -> Deposit:
    deposit = (
        Deposit.all_objects.select_for_update(of=('self',))
        .filter(tenant=tenant, pk=deposit_id)
        .first()
    )
    if deposit is None:
        raise Http404()
    return deposit


def _next_deposit_number(tenant) -> str:
    """Allocate next KAU-YYYYMM-#### under tenant advisory lock via unique retry."""
    prefix = timezone.now().strftime('KAU-%Y%m-')
    for _ in range(20):
        last = (
            Deposit.all_objects.filter(tenant=tenant, number__startswith=prefix)
            .order_by('-number')
            .values_list('number', flat=True)
            .first()
        )
        seq = 0
        if last:
            suffix = last.rsplit('-', 1)[-1]
            if suffix.isdigit():
                seq = int(suffix)
        candidate = f'{prefix}{seq + 1:04d}'
        if not Deposit.all_objects.filter(tenant=tenant, number=candidate).exists():
            return candidate
    raise DepositBadRequest('number_exhausted', 'Nije moguće dodijeliti broj kaucije.')


def deposit_dto(deposit: Deposit) -> dict:
    item = get_subledger_item_for_source(deposit.tenant, deposit)
    open_amount = Decimal('0.00')
    operational = 'none'
    if item is not None:
        open_amount = item.open_amount
        operational = item.status
    return {
        'id': deposit.pk,
        'number': deposit.number,
        'partner_id': deposit.partner_id,
        'partner_name': deposit.partner.name if deposit.partner_id else '',
        'direction': deposit.direction,
        'amount': f'{deposit.amount:.2f}',
        'currency': deposit.currency,
        'deposit_date': deposit.deposit_date.isoformat() if deposit.deposit_date else None,
        'workflow_status': deposit.status,
        'operational_status': operational,
        'open_amount': f'{open_amount:.2f}',
        'reference': deposit.reference or '',
        'notes': deposit.notes or '',
        'return_date': deposit.return_date.isoformat() if deposit.return_date else None,
        'return_bank_account_id': deposit.return_bank_account_id,
        'given_journal_entry_id': deposit.given_journal_entry_id,
        'return_journal_entry_id': deposit.return_journal_entry_id,
        'reverse_journal_entry_id': deposit.reverse_journal_entry_id,
        'created_at': deposit.created_at.isoformat() if deposit.created_at else None,
    }


def create_deposit(*, tenant, data: dict, user=None) -> dict:
    partner_id = data.get('partner_id')
    partner = Partner.all_objects.filter(tenant=tenant, pk=partner_id).first()
    if partner is None:
        raise Http404()
    amount = _money(data.get('amount'))
    currency = str(data.get('currency') or 'EUR').strip().upper()[:3] or 'EUR'
    deposit_date = data.get('deposit_date')
    if hasattr(deposit_date, 'isoformat'):
        pass
    else:
        from django.utils.dateparse import parse_date

        deposit_date = parse_date(str(deposit_date or ''))
    if deposit_date is None:
        raise DepositBadRequest('invalid_date', 'deposit_date je obavezan (YYYY-MM-DD).')

    for attempt in range(5):
        try:
            with transaction.atomic():
                deposit = Deposit.all_objects.create(
                    tenant=tenant,
                    number=_next_deposit_number(tenant),
                    partner=partner,
                    direction=Deposit.DIRECTION_GIVEN,
                    amount=amount,
                    currency=currency,
                    deposit_date=deposit_date,
                    status=Deposit.STATUS_DRAFT,
                    reference=str(data.get('reference') or '')[:100],
                    notes=str(data.get('notes') or ''),
                    created_by=user if getattr(user, 'is_authenticated', False) else None,
                )
                return deposit_dto(deposit)
        except IntegrityError:
            if attempt == 4:
                raise
    raise DepositBadRequest('create_failed', 'Kreiranje kaucije nije uspjelo.')


def cancel_deposit(*, tenant, deposit_id: int) -> dict:
    with transaction.atomic():
        deposit = _lock_deposit(tenant, deposit_id)
        if deposit.status != Deposit.STATUS_DRAFT:
            raise DepositConflict('invalid_status', 'Otkazivanje je dopušteno samo iz nacrta.')
        deposit.status = Deposit.STATUS_CANCELLED
        deposit.save(update_fields=['status', 'updated_at'])
        return deposit_dto(deposit)


def _posting_marker(kind: str, deposit_id: int) -> str:
    return f'[deposit_{kind}:{deposit_id}]'


def _create_balanced_entry(
    *,
    tenant,
    deposit: Deposit,
    user,
    kind: str,
    entry_date,
    debit_code: str,
    credit_code: str,
    amount: Decimal,
    description: str,
) -> JournalEntry:
    marker = _posting_marker(kind, deposit.pk)
    ct = ContentType.objects.get_for_model(Deposit)
    existing = JournalEntry.all_objects.filter(
        tenant=tenant,
        source_content_type=ct,
        source_object_id=deposit.pk,
        description__startswith=marker,
        status__in=['draft', 'posted'],
    ).first()
    if existing is not None:
        return existing

    debit = resolve_account(tenant, debit_code)
    credit = resolve_account(tenant, credit_code)
    entry = JournalEntry.all_objects.create(
        tenant=tenant,
        entry_number=_next_entry_number(tenant, entry_date),
        entry_date=entry_date,
        status='draft',
        description=f'{marker} {description}',
        reference=deposit.number,
        is_auto=True,
        source_content_type=ct,
        source_object_id=deposit.pk,
        fiscal_period=get_or_create_fiscal_period(tenant, entry_date),
        created_by=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=entry,
        account=debit,
        description=description,
        debit_amount=amount,
        credit_amount=Decimal('0'),
    )
    JournalEntryLine.objects.create(
        journal_entry=entry,
        account=credit,
        description=description,
        debit_amount=Decimal('0'),
        credit_amount=amount,
    )
    entry.post(user)
    return entry


def post_deposit(*, tenant, deposit_id: int, user) -> dict:
    with transaction.atomic():
        deposit = _lock_deposit(tenant, deposit_id)
        if deposit.status == Deposit.STATUS_OPEN and deposit.given_journal_entry_id:
            return deposit_dto(deposit)
        if deposit.status != Deposit.STATUS_DRAFT:
            raise DepositConflict('invalid_status', 'Knjiženje je dopušteno samo iz nacrta.')

        entry = _create_balanced_entry(
            tenant=tenant,
            deposit=deposit,
            user=user,
            kind='given',
            entry_date=deposit.deposit_date,
            debit_code='1152',
            credit_code='1020',
            amount=deposit.amount,
            description=f'Dana kaucija {deposit.number} — {deposit.partner.name}',
        )
        create_subledger_item(
            tenant,
            partner=deposit.partner,
            direction='receivable',
            source=deposit,
            journal_entry=entry,
            amount=deposit.amount,
            due_date=deposit.deposit_date,
        )
        deposit.given_journal_entry = entry
        deposit.status = Deposit.STATUS_OPEN
        deposit.save(update_fields=['given_journal_entry', 'status', 'updated_at'])
        return deposit_dto(deposit)


def return_deposit(
    *,
    tenant,
    deposit_id: int | None = None,
    user,
    data: dict | None = None,
    deposit: Deposit | None = None,
    already_locked: bool = False,
) -> dict:
    """Full return of an open given deposit.

    When ``already_locked=True``, ``deposit`` must be the row locked by the caller
    (ADR-0025 lock order). Nested ``select_for_update`` is skipped.
    """
    data = data or {}

    def _run(locked: Deposit) -> dict:
        if locked.status == Deposit.STATUS_RETURNED and locked.return_journal_entry_id:
            return deposit_dto(locked)
        if locked.status != Deposit.STATUS_OPEN:
            raise DepositConflict('invalid_status', 'Povrat je dopušten samo za otvorenu kauciju.')

        bank_id = data.get('return_bank_account_id') or locked.return_bank_account_id
        bank = (
            BankAccount.all_objects.filter(tenant=tenant, pk=bank_id)
            .select_related('ledger_account')
            .first()
        )
        if bank is None:
            raise DepositBadRequest('return_bank_required', 'return_bank_account_id je obavezan.')
        if (bank.currency or 'EUR').upper() != locked.currency.upper():
            raise DepositBadRequest('currency_mismatch', 'Valuta računa mora odgovarati valuti kaucije.')

        return_amount = data.get('amount', locked.amount)
        amount = _money(return_amount)
        if amount != locked.amount:
            raise DepositBadRequest(
                'full_return_required',
                'v1 dopušta samo puni povrat (iznos = iznos kaucije).',
            )

        from django.utils.dateparse import parse_date

        raw_date = data.get('return_date')
        if hasattr(raw_date, 'isoformat'):
            return_date = raw_date
        else:
            return_date = parse_date(str(raw_date or '')) or timezone.now().date()

        if bank.ledger_account_id and getattr(bank, 'ledger_account', None):
            debit_code = bank.ledger_account.account_code
        else:
            debit_code = '1000'

        entry = _create_balanced_entry(
            tenant=tenant,
            deposit=locked,
            user=user,
            kind='returned',
            entry_date=return_date,
            debit_code=debit_code,
            credit_code='1152',
            amount=amount,
            description=f'Povrat kaucije {locked.number} — {locked.partner.name}',
        )
        allocated = allocate_payment(
            tenant,
            source=locked,
            journal_entry=entry,
            amount=amount,
        )
        if allocated is None:
            raise DepositConflict('allocation_failed', 'Alokacija saldakonta nije uspjela.')

        locked.return_bank_account = bank
        locked.return_date = return_date
        locked.return_journal_entry = entry
        locked.status = Deposit.STATUS_RETURNED
        locked.save(
            update_fields=[
                'return_bank_account',
                'return_date',
                'return_journal_entry',
                'status',
                'updated_at',
            ]
        )
        return deposit_dto(locked)

    if already_locked:
        if deposit is None:
            raise DepositBadRequest('deposit_required', 'Zaključani deposit je obavezan.')
        return _run(deposit)

    with transaction.atomic():
        locked = _lock_deposit(tenant, deposit_id)
        return _run(locked)


def reverse_deposit(*, tenant, deposit_id: int, user) -> dict:
    with transaction.atomic():
        deposit = _lock_deposit(tenant, deposit_id)
        if deposit.status == Deposit.STATUS_REVERSED and deposit.reverse_journal_entry_id:
            return deposit_dto(deposit)
        if deposit.status != Deposit.STATUS_OPEN:
            raise DepositConflict('invalid_status', 'Storno je dopušteno samo za otvorenu kauciju.')
        if deposit.given_journal_entry_id is None:
            raise DepositConflict('missing_given_entry', 'Nedostaje JE davanja.')

        given = deposit.given_journal_entry
        if given.status != 'posted':
            raise DepositConflict('invalid_given_entry', 'JE davanja nije u statusu posted.')

        reversal = given.reverse(user)
        deposit.reverse_journal_entry = reversal
        deposit.status = Deposit.STATUS_REVERSED
        deposit.save(update_fields=['reverse_journal_entry', 'status', 'updated_at'])
        return deposit_dto(deposit)


def get_deposit(*, tenant, deposit_id: int) -> dict:
    deposit = (
        Deposit.all_objects.filter(tenant=tenant, pk=deposit_id)
        .select_related('partner', 'return_bank_account')
        .first()
    )
    if deposit is None:
        raise Http404()
    return deposit_dto(deposit)


def list_deposits(*, tenant, partner_id: int | None = None) -> dict:
    qs = Deposit.all_objects.filter(tenant=tenant).select_related('partner').order_by('-deposit_date', '-id')
    if partner_id is not None:
        if not Partner.all_objects.filter(tenant=tenant, pk=partner_id).exists():
            raise Http404()
        qs = qs.filter(partner_id=partner_id)
    rows = list(qs[:100])
    return {
        'count': len(rows),
        'results': [deposit_dto(row) for row in rows],
    }
