"""Knjiženje kunskih izvoda Fine Star (2013–2014) u glavnu knjigu.

Svaka stavka dobije jednu proknjiženu temeljnicu i vezu 1:1 s izvodom.
Izvod je vjerodostojna isprava; računi i troškovi se ne otvaraju.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction

from accounting.models import JournalEntry
from accounting.services.manual_journal import JournalLineInput, create_manual_journal_entry
from accounting.services.posting import resolve_account
from banking.book import transaction_book_amount
from banking.models import BankTransaction
from banking.reconciliation import match_transaction_to_journal_entry
from partners.models import Partner
from payments.models import BankAccount

FINASTAR_SLUG = 'finestar'
HRK_ACCOUNT_IBAN = 'HR5524070001100396357'
NIKOLA_NAME = 'Nikola Banović'
NIKOLA_NOTE = (
    'Osobna iskaznica 1054106290. '
    'Podizao gotovinu s kunskog računa društva (ATM, 2014.).'
)
LOAN_REPAYMENT_DATE = date(2014, 7, 22)
LOAN_REPAYMENT_AMOUNT = Decimal('500.00')
ZERO_BOOK_BUMP = Decimal('0.01')

KIND_CAPITAL = 'capital'
KIND_ATM = 'atm'
KIND_LOAN_IN = 'loan_in'
KIND_LOAN_OUT = 'loan_out'
KIND_FEE = 'fee'
KIND_INTEREST = 'interest'

BANK = 'BANK'


class HrkLineBookingError(Exception):
    """Stavka se ne smije knjižiti ovom naredbom."""


@dataclass(frozen=True)
class BookingLeg:
    account_code: str
    side: str
    description: str


@dataclass(frozen=True)
class HrkLineBooking:
    kind: str
    summary: str
    legs: tuple[BookingLeg, ...]


def marker_for(bank_tx_id: int) -> str:
    return f'[hrk-izvod:btx-{bank_tx_id}]'


def classify_hrk_line(bank_tx) -> HrkLineBooking:
    """Vrati predložak knjiženja ili digni HrkLineBookingError."""
    description = (bank_tx.description or '').strip()
    folded = description.upper()
    counterparty = _fold(bank_tx.counterparty_name or '')
    kind_date = bank_tx.transaction_date
    amount = Decimal(bank_tx.amount)
    direction = bank_tx.transaction_type

    if 'POVRAT OSN.POL.UPL' in folded:
        _expect(direction, 'credit', description)
        return HrkLineBooking(
            KIND_CAPITAL,
            'Uplata temeljnog kapitala d.o.o.',
            (
                BookingLeg('002', 'debit', 'Upis temeljnog kapitala d.o.o.'),
                BookingLeg('9000', 'credit', 'Upis temeljnog kapitala d.o.o.'),
                BookingLeg(BANK, 'debit', 'Uplata temeljnog kapitala na transakcijski račun'),
                BookingLeg('002', 'credit', 'Uplata temeljnog kapitala na transakcijski račun'),
            ),
        )
    if folded.startswith('POZAJMICA') and 'BANOVIC' in counterparty:
        _expect(direction, 'credit', description)
        text = f'Primljena pozajmica člana društva — {NIKOLA_NAME}'
        return HrkLineBooking(
            KIND_LOAN_IN,
            text,
            (
                BookingLeg(BANK, 'debit', text),
                BookingLeg('2145', 'credit', text),
            ),
        )
    if 'ATM GOTOVINA' in folded and kind_date == LOAN_REPAYMENT_DATE and amount == LOAN_REPAYMENT_AMOUNT:
        _expect(direction, 'debit', description)
        text = f'Povrat pozajmice člana društva — {NIKOLA_NAME}'
        return HrkLineBooking(
            KIND_LOAN_OUT,
            text,
            (
                BookingLeg('2145', 'debit', text),
                BookingLeg(BANK, 'credit', text),
            ),
        )
    if 'ATM GOTOVINA' in folded:
        _expect(direction, 'debit', description)
        text = f'Podizanje gotovine na bankomatu — {NIKOLA_NAME}'
        return HrkLineBooking(
            KIND_ATM,
            text,
            (
                BookingLeg('1331', 'debit', text),
                BookingLeg(BANK, 'credit', text),
            ),
        )
    if folded.startswith('NAKNAD'):
        _expect(direction, 'debit', description)
        text = 'Naknada platnog prometa'
        return HrkLineBooking(
            KIND_FEE,
            text,
            (
                BookingLeg('4650', 'debit', text),
                BookingLeg(BANK, 'credit', text),
            ),
        )
    if 'PRIPIS KAMATE' in folded:
        _expect(direction, 'credit', description)
        text = 'Pripis kamate'
        return HrkLineBooking(
            KIND_INTEREST,
            text,
            (
                BookingLeg(BANK, 'debit', text),
                BookingLeg('7710', 'credit', text),
            ),
        )
    raise HrkLineBookingError(
        f'Nepoznat opis stavke {kind_date} {amount} {description!r}.'
    )


def prepare_book_amount(bank_tx) -> Decimal:
    """Knjigovodstveni iznos. 0,01 HRK koji je zaokružen na 0,00 EUR knjiži se kao 0,01 EUR."""
    if (
        (bank_tx.currency or '').upper() == 'HRK'
        and Decimal(bank_tx.amount) == ZERO_BOOK_BUMP
        and (bank_tx.amount_eur is None or Decimal(bank_tx.amount_eur) == Decimal('0.00'))
    ):
        bank_tx.amount_eur = ZERO_BOOK_BUMP
        bank_tx.save(update_fields=['amount_eur'])
    return transaction_book_amount(bank_tx)


@transaction.atomic
def book_hrk_statement_lines(tenant, user: User, bank_account: BankAccount) -> list[str]:
    """Proknjiži i uskladi sve stavke jednog kunskog računa. Idempotentno."""
    ledger = resolve_account(tenant, '1000')
    if bank_account.ledger_account_id != ledger.id:
        bank_account.ledger_account = ledger
        bank_account.save(update_fields=['ledger_account'])

    lines = list(
        BankTransaction.all_objects.select_for_update()
        .filter(tenant=tenant, bank_statement__bank_account=bank_account)
        .select_related('bank_statement')
        .order_by('transaction_date', 'id')
    )
    classified = [(line, classify_hrk_line(line)) for line in lines]
    ensure_nikola_partner(tenant, user)

    outcomes: list[str] = []
    for line, booking in classified:
        outcomes.append(_book_one(tenant, user, line, booking, ledger.account_code))
    return outcomes


def ensure_nikola_partner(tenant, user: User) -> Partner:
    partner = Partner.all_objects.filter(tenant=tenant, name=NIKOLA_NAME).first()
    if partner is not None:
        notes = partner.notes or ''
        if NIKOLA_NOTE not in notes:
            partner.notes = f'{notes}\n{NIKOLA_NOTE}'.strip()
            partner.save(update_fields=['notes'])
        return partner
    return Partner.all_objects.create(
        tenant=tenant,
        name=NIKOLA_NAME,
        partner_type='other',
        address='Ivane Brlić-Mažuranić 23',
        city='Zagreb',
        postal_code='10000',
        country_code='HR',
        notes=NIKOLA_NOTE,
        created_by=user,
    )


def _book_one(tenant, user, bank_tx, booking: HrkLineBooking, bank_code: str) -> str:
    marker = marker_for(bank_tx.pk)
    existing = (
        JournalEntry.all_objects.filter(tenant=tenant, description__startswith=marker, status='posted')
        .order_by('id')
    )
    posted = list(existing[:2])
    if len(posted) > 1:
        raise HrkLineBookingError(f'Više temeljnica s oznakom {marker}.')

    if bank_tx.match_status == 'matched' and bank_tx.matched_journal_entry_id:
        if posted and bank_tx.matched_journal_entry_id == posted[0].id:
            return f'skip {marker}'
        raise HrkLineBookingError(
            f'Stavka {bank_tx.pk} je već usklađena s drugom temeljnicom.'
        )

    amount = prepare_book_amount(bank_tx)
    entry = posted[0] if posted else _create_entry(tenant, user, bank_tx, booking, bank_code, marker, amount)
    if bank_tx.matched_journal_entry_id != entry.id or bank_tx.match_status != 'matched':
        match_transaction_to_journal_entry(bank_tx, entry, user)
    return f'booked {marker} {entry.entry_number} {booking.kind}'


def _create_entry(tenant, user, bank_tx, booking, bank_code, marker, amount: Decimal) -> JournalEntry:
    shown = _hr_amount(bank_tx.amount, bank_tx.currency or 'HRK')
    lines = []
    for leg in booking.legs:
        code = bank_code if leg.account_code == BANK else leg.account_code
        debit = amount if leg.side == 'debit' else Decimal('0')
        credit = amount if leg.side == 'credit' else Decimal('0')
        lines.append(JournalLineInput(code, debit, credit, description=leg.description))
    return create_manual_journal_entry(
        tenant,
        user,
        entry_date=bank_tx.transaction_date,
        description=f'{marker} {booking.summary} ({shown})',
        reference=(bank_tx.reference or bank_tx.bank_statement.statement_number or '')[:100],
        lines=lines,
        post=True,
    )


def _expect(actual: str, expected: str, description: str) -> None:
    if actual != expected:
        raise HrkLineBookingError(
            f'Stavka {description!r} ima smjer {actual}, očekivan je {expected}.'
        )


def _fold(value: str) -> str:
    return (
        value.upper()
        .replace('Ć', 'C')
        .replace('Č', 'C')
        .replace('Š', 'S')
        .replace('Ž', 'Z')
        .replace('Đ', 'D')
    )


def _hr_amount(amount: Decimal, currency: str) -> str:
    quantized = Decimal(amount).quantize(Decimal('0.01'))
    whole, frac = f'{abs(quantized):.2f}'.split('.')
    groups: list[str] = []
    while whole:
        groups.append(whole[-3:])
        whole = whole[:-3]
    return f'{".".join(reversed(groups))},{frac} {currency}'
