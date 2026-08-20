"""Private funds / paid on behalf of company (ADR-0026)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone

from accounting.models import Deposit, JournalEntry, JournalEntryLine, PrivateFundsClaim
from accounting.services.analytics import get_or_create_analytic_for_partner
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
from expenses.models import Expense, ExpensePayer
from partners.models import Partner
from shared.countries import country_display_name
from shared.oib import is_valid_oib, normalize_oib


ANTE_OIB = '11528564544'
ANTE_NAME = 'Ante Vrcan'


class PrivateFundsConflict(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class PrivateFundsBadRequest(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _money(value) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise PrivateFundsBadRequest('invalid_amount', 'Iznos mora biti decimalni broj.') from exc
    if amount <= Decimal('0'):
        raise PrivateFundsBadRequest('invalid_amount', 'Iznos mora biti veći od nule.')
    return amount.quantize(Decimal('0.01'))


def _lock_claim(tenant, claim_id: int) -> PrivateFundsClaim:
    claim = (
        PrivateFundsClaim.all_objects.select_for_update(of=('self',))
        .filter(tenant=tenant, pk=claim_id)
        .select_related('partner', 'related_content_type')
        .first()
    )
    if claim is None:
        raise Http404()
    return claim


def _next_claim_number(tenant) -> str:
    prefix = timezone.now().strftime('PFC-%Y%m-')
    for _ in range(20):
        last = (
            PrivateFundsClaim.all_objects.filter(tenant=tenant, number__startswith=prefix)
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
        if not PrivateFundsClaim.all_objects.filter(tenant=tenant, number=candidate).exists():
            return candidate
    raise PrivateFundsBadRequest('number_exhausted', 'Nije moguće dodijeliti broj claima.')


def posting_marker(claim_id: int) -> str:
    return f'[private_funds:{claim_id}]'


def ensure_partner_ante_vrcan(*, tenant, user=None) -> Partner:
    """Create or return Partner Ante; link ExpensePayer with same OIB (no dual identity)."""
    oib = normalize_oib(ANTE_OIB)
    if not is_valid_oib(oib):
        raise PrivateFundsBadRequest('invalid_oib', f'OIB {ANTE_OIB} nije valjan HR OIB.')

    with transaction.atomic():
        partner = Partner.all_objects.filter(tenant=tenant, tax_number=oib).first()
        if partner is None:
            partner = Partner.all_objects.filter(tenant=tenant, name=ANTE_NAME).first()

        if partner is None:
            partner = Partner.all_objects.create(
                tenant=tenant,
                name=ANTE_NAME,
                short_name='Ante',
                partner_type='other',
                status='active',
                tax_number=oib,
                address='Gärtnerstraße 44',
                city='Hanau',
                postal_code='63452',
                country_code='HR',
                country=country_display_name('HR'),
                notes='Fizička osoba; prebivalište DE (adresa ≠ jurisdikcija).',
                created_by=user if getattr(user, 'is_authenticated', False) else None,
            )
        else:
            updates = []
            if partner.tax_number != oib:
                partner.tax_number = oib
                updates.append('tax_number')
            if partner.partner_type != 'other':
                partner.partner_type = 'other'
                updates.append('partner_type')
            if partner.country_code != 'HR':
                partner.country_code = 'HR'
                partner.country = country_display_name('HR')
                updates.extend(['country_code', 'country'])
            if updates:
                partner.save(update_fields=[*updates, 'updated_at'])

        payers = list(
            ExpensePayer.all_objects.select_for_update().filter(tenant=tenant, oib=oib)
        )
        for payer in payers:
            if payer.partner_id != partner.pk:
                payer.partner = partner
                payer.save(update_fields=['partner'])
            if payer.name != ANTE_NAME and 'ante' in (payer.name or '').lower():
                # keep existing name if already Ante*; do not invent duplicates
                pass

        named = ExpensePayer.all_objects.filter(tenant=tenant, name=ANTE_NAME).first()
        if named is not None and normalize_oib(named.oib or '') not in ('', oib):
            raise PrivateFundsConflict(
                'payer_oib_mismatch',
                f'ExpensePayer "{ANTE_NAME}" ima drugi OIB — ručna provjera prije linka.',
            )
        if named is not None and named.partner_id != partner.pk:
            named.partner = partner
            fields = ['partner']
            if not named.oib:
                named.oib = oib
                fields.append('oib')
            named.save(update_fields=fields)

        return partner


def claim_dto(claim: PrivateFundsClaim) -> dict:
    item = get_subledger_item_for_source(claim.tenant, claim)
    open_amount = Decimal('0.00')
    operational = 'none'
    if item is not None:
        open_amount = item.open_amount
        operational = item.status
    return {
        'id': claim.pk,
        'number': claim.number,
        'claim_type': claim.claim_type,
        'partner_id': claim.partner_id,
        'partner_name': claim.partner.name if claim.partner_id else '',
        'amount': f'{claim.amount:.2f}',
        'currency': claim.currency,
        'claim_date': claim.claim_date.isoformat() if claim.claim_date else None,
        'status': claim.status,
        'operational_status': operational,
        'open_amount': f'{open_amount:.2f}',
        'reference': claim.reference or '',
        'notes': claim.notes or '',
        'related_type': claim.related_content_type.model if claim.related_content_type_id else '',
        'related_id': claim.related_object_id,
        'journal_entry_id': claim.journal_entry_id,
        'created_at': claim.created_at.isoformat() if claim.created_at else None,
    }


def get_claim(*, tenant, claim_id: int) -> dict:
    claim = (
        PrivateFundsClaim.all_objects.filter(tenant=tenant, pk=claim_id)
        .select_related('partner', 'related_content_type')
        .first()
    )
    if claim is None:
        raise Http404()
    return claim_dto(claim)


def create_claim(*, tenant, data: dict, user=None) -> dict:
    partner_id = data.get('partner_id')
    partner = Partner.all_objects.filter(tenant=tenant, pk=partner_id).first()
    if partner is None:
        raise Http404()

    claim_type = str(data.get('claim_type') or '').strip()
    if claim_type not in (
        PrivateFundsClaim.CLAIM_SUPPLIER_PAYMENT,
        PrivateFundsClaim.CLAIM_DEPOSIT_FUNDING,
    ):
        raise PrivateFundsBadRequest('invalid_claim_type', 'Nepoznat claim_type.')

    amount = _money(data.get('amount'))
    currency = str(data.get('currency') or 'EUR').strip().upper()[:3] or 'EUR'
    claim_date = data.get('claim_date')
    if not hasattr(claim_date, 'isoformat'):
        from django.utils.dateparse import parse_date

        claim_date = parse_date(str(claim_date or ''))
    if claim_date is None:
        raise PrivateFundsBadRequest('invalid_date', 'claim_date je obavezan (YYYY-MM-DD).')

    related_type = str(data.get('related_type') or '').strip().lower()
    related_id = data.get('related_id')
    related = _resolve_related(tenant, claim_type, related_type, related_id)

    for attempt in range(5):
        try:
            with transaction.atomic():
                claim = PrivateFundsClaim.all_objects.create(
                    tenant=tenant,
                    number=_next_claim_number(tenant),
                    claim_type=claim_type,
                    partner=partner,
                    amount=amount,
                    currency=currency,
                    claim_date=claim_date,
                    status=PrivateFundsClaim.STATUS_DRAFT,
                    reference=str(data.get('reference') or '')[:100],
                    notes=str(data.get('notes') or ''),
                    related_content_type=ContentType.objects.get_for_model(related),
                    related_object_id=related.pk,
                    created_by=user if getattr(user, 'is_authenticated', False) else None,
                )
                return claim_dto(claim)
        except IntegrityError:
            if attempt == 4:
                raise
    raise PrivateFundsBadRequest('create_failed', 'Kreiranje claima nije uspjelo.')


def _resolve_related(tenant, claim_type: str, related_type: str, related_id):
    if not related_id:
        raise PrivateFundsBadRequest('related_required', 'related_id je obavezan.')
    if claim_type == PrivateFundsClaim.CLAIM_SUPPLIER_PAYMENT:
        if related_type not in ('expense', ''):
            raise PrivateFundsBadRequest(
                'related_type',
                'supplier_payment zahtijeva related_type=expense.',
            )
        expense = Expense.all_objects.filter(tenant=tenant, pk=related_id).first()
        if expense is None:
            raise Http404()
        return expense
    if claim_type == PrivateFundsClaim.CLAIM_DEPOSIT_FUNDING:
        if related_type not in ('deposit', ''):
            raise PrivateFundsBadRequest(
                'related_type',
                'deposit_funding zahtijeva related_type=deposit.',
            )
        deposit = Deposit.all_objects.filter(tenant=tenant, pk=related_id).first()
        if deposit is None:
            raise Http404()
        return deposit
    raise PrivateFundsBadRequest('invalid_claim_type', 'Nepoznat claim_type.')


def _existing_claim_entry(*, tenant, claim: PrivateFundsClaim) -> JournalEntry | None:
    marker = posting_marker(claim.pk)
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


def post_claim(*, tenant, claim_id: int, user=None) -> dict:
    with transaction.atomic():
        claim = _lock_claim(tenant, claim_id)
        if claim.status == PrivateFundsClaim.STATUS_CANCELLED:
            raise PrivateFundsConflict('cancelled', 'Claim je otkazan.')
        if claim.status == PrivateFundsClaim.STATUS_POSTED and claim.journal_entry_id:
            return claim_dto(claim)

        existing = _existing_claim_entry(tenant=tenant, claim=claim)
        if existing is not None:
            claim.journal_entry = existing
            claim.status = PrivateFundsClaim.STATUS_POSTED
            claim.save(update_fields=['journal_entry', 'status', 'updated_at'])
            return claim_dto(claim)

        related = claim.related
        if related is None:
            raise PrivateFundsConflict('missing_related', 'Povezani dokument nije dostupan.')

        if claim.claim_type == PrivateFundsClaim.CLAIM_SUPPLIER_PAYMENT:
            entry = _post_supplier_payment(tenant=tenant, user=user, claim=claim, expense=related)
        elif claim.claim_type == PrivateFundsClaim.CLAIM_DEPOSIT_FUNDING:
            entry = _post_deposit_funding(tenant=tenant, user=user, claim=claim, deposit=related)
        else:
            raise PrivateFundsBadRequest('invalid_claim_type', 'Nepoznat claim_type.')

        claim.journal_entry = entry
        claim.status = PrivateFundsClaim.STATUS_POSTED
        claim.save(update_fields=['journal_entry', 'status', 'updated_at'])
        return claim_dto(claim)


def _post_supplier_payment(*, tenant, user, claim: PrivateFundsClaim, expense: Expense) -> JournalEntry:
    if not isinstance(expense, Expense):
        raise PrivateFundsBadRequest('related_type', 'supplier_payment zahtijeva Expense.')
    supplier = expense.supplier
    if supplier is None:
        raise PrivateFundsBadRequest('missing_supplier', 'Trošak nema dobavljača.')

    item = get_subledger_item_for_source(tenant, expense)
    if item is None or item.status not in ('open', 'partial'):
        raise PrivateFundsConflict('ap_not_open', 'AP dobavljača nije otvoren.')
    if claim.amount > item.open_amount:
        raise PrivateFundsBadRequest(
            'amount_exceeds_open',
            'Iznos claima premašuje otvoreni AP.',
        )

    # Lock expense AP item
    item = (
        type(item).all_objects.select_for_update()
        .filter(pk=item.pk)
        .first()
    )

    amount = claim.amount
    supplier_analytic = get_or_create_analytic_for_partner(tenant, supplier, synthetic_code='2201')
    ante_analytic = get_or_create_analytic_for_partner(
        tenant, claim.partner, synthetic_code='2309',
    )
    marker = posting_marker(claim.pk)
    entry = JournalEntry.all_objects.create(
        tenant=tenant,
        entry_number=_next_entry_number(tenant, claim.claim_date),
        entry_date=claim.claim_date,
        status='draft',
        description=(
            f'{marker} supplier_payment — {claim.number} '
            f'{expense.expense_number} → {claim.partner.name}'
        ),
        reference=claim.number,
        is_auto=True,
        source_content_type=ContentType.objects.get_for_model(PrivateFundsClaim),
        source_object_id=claim.pk,
        fiscal_period=get_or_create_fiscal_period(tenant, claim.claim_date),
        created_by=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=entry,
        account=supplier_analytic.chart_account,
        analytic_account=supplier_analytic,
        description=f'Creditor swap — {supplier.name}',
        debit_amount=amount,
        credit_amount=Decimal('0'),
    )
    JournalEntryLine.objects.create(
        journal_entry=entry,
        account=ante_analytic.chart_account,
        analytic_account=ante_analytic,
        description=f'Private funds payable — {claim.partner.name}',
        debit_amount=Decimal('0'),
        credit_amount=amount,
    )
    entry.post(user)

    allocated = allocate_payment(tenant, source=expense, journal_entry=entry, amount=amount)
    if allocated is None:
        raise PrivateFundsConflict('allocation_failed', 'Alokacija na AP dobavljača nije uspjela.')

    ante_item = create_subledger_item(
        tenant,
        partner=claim.partner,
        direction='payable',
        source=claim,
        journal_entry=entry,
        amount=amount,
        due_date=claim.claim_date,
    )
    if ante_item is None:
        raise PrivateFundsConflict('ante_item_failed', 'Ante SubledgerItem nije kreiran.')

    _mark_expense_paid_if_closed(tenant=tenant, expense=expense)
    return entry


def _post_deposit_funding(*, tenant, user, claim: PrivateFundsClaim, deposit: Deposit) -> JournalEntry:
    if not isinstance(deposit, Deposit):
        raise PrivateFundsBadRequest('related_type', 'deposit_funding zahtijeva Deposit.')
    if claim.amount != deposit.amount:
        raise PrivateFundsBadRequest(
            'amount_mismatch',
            'deposit_funding v1 zahtijeva amount == deposit.amount.',
        )

    amount = claim.amount
    cash = resolve_account(tenant, '1020')
    ante_analytic = get_or_create_analytic_for_partner(
        tenant, claim.partner, synthetic_code='2309',
    )
    marker = posting_marker(claim.pk)
    entry = JournalEntry.all_objects.create(
        tenant=tenant,
        entry_number=_next_entry_number(tenant, claim.claim_date),
        entry_date=claim.claim_date,
        status='draft',
        description=(
            f'{marker} deposit_funding — {claim.number} '
            f'{deposit.number} → {claim.partner.name}'
        ),
        reference=claim.number,
        is_auto=True,
        source_content_type=ContentType.objects.get_for_model(PrivateFundsClaim),
        source_object_id=claim.pk,
        fiscal_period=get_or_create_fiscal_period(tenant, claim.claim_date),
        created_by=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=entry,
        account=cash,
        description='Korekcija blagajne — privatno financiranje kaucije',
        debit_amount=amount,
        credit_amount=Decimal('0'),
    )
    JournalEntryLine.objects.create(
        journal_entry=entry,
        account=ante_analytic.chart_account,
        analytic_account=ante_analytic,
        description=f'Private funds payable — {claim.partner.name}',
        debit_amount=Decimal('0'),
        credit_amount=amount,
    )
    entry.post(user)

    ante_item = create_subledger_item(
        tenant,
        partner=claim.partner,
        direction='payable',
        source=claim,
        journal_entry=entry,
        amount=amount,
        due_date=claim.claim_date,
    )
    if ante_item is None:
        raise PrivateFundsConflict('ante_item_failed', 'Ante SubledgerItem nije kreiran.')
    return entry
