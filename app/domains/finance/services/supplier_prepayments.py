"""Dani predujam dobavljaču i njegova primjena na otvorenu obvezu.

Finance use-case. Ne zove PDV knjigu ni PDV lock. Fiskalno zatvoreno razdoblje
odbija knjiženje istim uvjetom kao ručna temeljnica.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from accounting.models import (
    JournalEntry,
    PostingRule,
    PrepaymentVatTreatment,
    PurchaseNature,
    SupplierPrepayment,
    SupplierPrepaymentApplication,
)
from accounting.services.journal_lines import persist_journal_entry_line
from accounting.services.manual_journal import _ensure_period_open
from accounting.services.posting import (
    _next_entry_number,
    _rule_matches,
    ensure_default_posting_rules,
    get_or_create_fiscal_period,
    resolve_account,
)
from banking.book import transaction_book_amount, transaction_book_currency
from banking.models import BankTransaction
from banking.reconciliation import match_transaction_to_journal_entry
from domains.finance.services.bank_settlement import _bank_ledger_account
from domains.finance.services.payable_documents import PayableDocument, resolve_payable_document
from domains.finance.services.subledger import allocate_payment
from partners.models import Partner


def _money(value) -> Decimal:
    amount = Decimal(str(value)).quantize(Decimal('0.01'))
    if amount <= Decimal('0'):
        raise ValidationError('Iznos mora biti veći od nule.')
    return amount


def _active_prepayment_for_bank_tx(tenant, bank_transaction_id: int) -> SupplierPrepayment | None:
    return (
        SupplierPrepayment.all_objects.filter(
            tenant=tenant,
            bank_transaction_id=bank_transaction_id,
        )
        .exclude(status=SupplierPrepayment.STATUS_REVERSED)
        .order_by('id')
        .first()
    )


def resolve_supplier_prepayment_account(tenant, *, purchase_nature: str, vat_treatment: str = ''):
    if purchase_nature not in PurchaseNature.values:
        raise ValidationError('Nepoznata podvrsta nabave predujma.')
    treatment = (vat_treatment or '').strip()
    if purchase_nature == PurchaseNature.SERVICE:
        if treatment not in PrepaymentVatTreatment.values:
            raise ValidationError('Usluga traži PDV tretman predujma.')
    else:
        treatment = ''
    ensure_default_posting_rules(tenant)
    context = SimpleNamespace(purchase_nature=purchase_nature, vat_treatment=treatment)
    rules = PostingRule.all_objects.filter(
        tenant=tenant,
        document_type='supplier_prepayment',
        is_active=True,
    ).order_by('priority', 'id')
    for rule in rules:
        if _rule_matches(rule, Decimal('1.00'), context):
            return resolve_account(tenant, rule.debit_account_code)
    raise ValidationError('Nema pravila knjiženja za ovu podvrstu predujma.')


def _refresh_prepayment_status(prepayment: SupplierPrepayment) -> None:
    if prepayment.status == SupplierPrepayment.STATUS_REVERSED:
        return
    posted = (
        prepayment.applications.filter(clearing_journal_entry__status='posted')
        .aggregate(total=Sum('amount'))
        .get('total')
        or Decimal('0.00')
    )
    prepayment.applied_amount = Decimal(posted).quantize(Decimal('0.01'))
    available = prepayment.original_amount - prepayment.applied_amount
    if prepayment.applied_amount <= Decimal('0'):
        prepayment.status = SupplierPrepayment.STATUS_OPEN
    elif available <= Decimal('0'):
        prepayment.status = SupplierPrepayment.STATUS_APPLIED
        prepayment.applied_amount = prepayment.original_amount
    else:
        prepayment.status = SupplierPrepayment.STATUS_PARTIAL
    prepayment.save(update_fields=['applied_amount', 'status'])


def prepayment_marker(bank_transaction_id: int) -> str:
    return f'[supplier_prepayment:btx-{bank_transaction_id}]'


def application_marker(application_id: int) -> str:
    return f'[supplier_prepayment_application:{application_id}]'


@transaction.atomic
def post_supplier_prepayment(
    *,
    tenant,
    user,
    bank_transaction_id: int,
    partner_id: int,
    purchase_nature: str,
    vat_treatment: str = '',
    idempotency_key: str = '',
) -> SupplierPrepayment:
    key = (idempotency_key or '').strip()
    bank_tx = (
        BankTransaction.all_objects.select_for_update()
        .select_related('bank_statement', 'bank_statement__bank_account', 'tenant')
        .filter(pk=bank_transaction_id, tenant=tenant)
        .first()
    )
    if bank_tx is None:
        raise ValidationError('Bankovna transakcija nije pronađena.')

    existing = _active_prepayment_for_bank_tx(tenant, bank_tx.pk)
    if existing is not None:
        return existing
    if key:
        keyed = SupplierPrepayment.all_objects.filter(tenant=tenant, idempotency_key=key).first()
        if keyed is not None:
            raise ValidationError('Ključ idempotentnosti je već iskorišten za drugi predujam.')

    if bank_tx.transaction_type != 'debit':
        raise ValidationError('Predujam se knjiži samo s terećenja.')
    if bank_tx.match_status != 'unmatched' or bank_tx.matched_journal_entry_id or bank_tx.matched_payment_id:
        raise ValidationError('Transakcija je već usklađena.')
    amount = _money(transaction_book_amount(bank_tx))
    book_currency = transaction_book_currency(bank_tx)

    partner = Partner.all_objects.filter(pk=partner_id, tenant=tenant).first()
    if partner is None:
        raise ValidationError('Dobavljač nije pronađen.')

    _ensure_period_open(tenant, bank_tx.transaction_date)
    account = resolve_supplier_prepayment_account(
        tenant,
        purchase_nature=purchase_nature,
        vat_treatment=vat_treatment,
    )
    treatment = vat_treatment if purchase_nature == PurchaseNature.SERVICE else ''
    prepayment = SupplierPrepayment.all_objects.create(
        tenant=tenant,
        partner=partner,
        currency=book_currency,
        original_amount=amount,
        applied_amount=Decimal('0.00'),
        purchase_nature=purchase_nature,
        vat_treatment=treatment,
        prepayment_account=account,
        bank_transaction=bank_tx,
        posting_date=bank_tx.transaction_date,
        status=SupplierPrepayment.STATUS_OPEN,
        idempotency_key=key,
    )
    bank_account = _bank_ledger_account(tenant, bank_tx.bank_statement.bank_account)
    entry = JournalEntry.all_objects.create(
        tenant=tenant,
        entry_number=_next_entry_number(tenant, bank_tx.transaction_date),
        entry_date=bank_tx.transaction_date,
        status='draft',
        description=f'{prepayment_marker(bank_tx.pk)} Predujam dobavljaču',
        reference=bank_tx.reference or '',
        is_auto=True,
        source_content_type=ContentType.objects.get_for_model(SupplierPrepayment),
        source_object_id=prepayment.pk,
        fiscal_period=get_or_create_fiscal_period(tenant, bank_tx.transaction_date),
        created_by=user,
    )
    persist_journal_entry_line(
        journal_entry=entry,
        account=account,
        description='Dani predujam',
        debit_amount=amount,
        credit_amount=Decimal('0.00'),
    )
    persist_journal_entry_line(
        journal_entry=entry,
        account=bank_account,
        description='Isplata predujma',
        debit_amount=Decimal('0.00'),
        credit_amount=amount,
    )
    entry.post(user)
    match_transaction_to_journal_entry(bank_tx, entry, user)
    prepayment.posting_journal_entry = entry
    prepayment.save(update_fields=['posting_journal_entry'])
    return prepayment


def _clearing_date(prepayment: SupplierPrepayment, payable: PayableDocument):
    item = payable.get_subledger_item()
    if item is None:
        raise ValidationError('Dokument nema otvorenu obvezu.')
    return max(prepayment.posting_date, item.journal_entry.entry_date)


@transaction.atomic
def apply_supplier_prepayment(
    *,
    prepayment: SupplierPrepayment,
    payable_document: PayableDocument,
    amount,
    user,
    idempotency_key: str | None = None,
) -> SupplierPrepaymentApplication:
    key = (idempotency_key or '').strip()
    prepayment = SupplierPrepayment.all_objects.select_for_update().get(pk=prepayment.pk)
    payable_document.lock()
    source = payable_document.source
    if key:
        existing = (
            SupplierPrepaymentApplication.all_objects.select_for_update()
            .filter(tenant_id=prepayment.tenant_id, idempotency_key=key)
            .first()
        )
        if existing is not None:
            same_target = (
                existing.prepayment_id == prepayment.pk
                and existing.target_object_id == source.pk
                and existing.target_content_type_id == ContentType.objects.get_for_model(source).id
            )
            if not same_target:
                raise ValidationError('Ključ idempotentnosti je već iskorišten.')
            if existing.clearing_journal_entry_id:
                posted = JournalEntry.all_objects.filter(
                    pk=existing.clearing_journal_entry_id,
                    status='posted',
                ).exists()
                if posted:
                    return existing

    if prepayment.status == SupplierPrepayment.STATUS_REVERSED:
        raise ValidationError('Predujam je storniran.')
    if prepayment.tenant_id != payable_document.tenant_id:
        raise ValidationError('Predujam i dokument nisu istog tenanta.')
    if payable_document.partner is None or prepayment.partner_id != payable_document.partner.pk:
        raise ValidationError('Predujam i dokument nemaju istog partnera.')
    if (prepayment.currency or 'EUR') != (payable_document.currency or 'EUR'):
        raise ValidationError('Valuta predujma i dokumenta nije ista.')

    applied = _money(amount)
    _refresh_prepayment_status(prepayment)
    prepayment.refresh_from_db()
    if applied > prepayment.available_amount:
        raise ValidationError('Iznos premašuje raspoloživi predujam.')
    open_amount = payable_document.get_open_payable_amount()
    if applied > open_amount:
        raise ValidationError('Iznos premašuje otvorenu obvezu.')

    clearing_date = _clearing_date(prepayment, payable_document)
    _ensure_period_open(prepayment.tenant, clearing_date)
    payable_account, analytic = payable_document.get_payable_account()
    prepayment_account = prepayment.prepayment_account

    target_ct = ContentType.objects.get_for_model(source)
    application = SupplierPrepaymentApplication.all_objects.create(
        tenant_id=prepayment.tenant_id,
        prepayment=prepayment,
        target_content_type=target_ct,
        target_object_id=source.pk,
        amount=applied,
        idempotency_key=key,
    )
    entry = JournalEntry.all_objects.create(
        tenant_id=prepayment.tenant_id,
        entry_number=_next_entry_number(prepayment.tenant, clearing_date),
        entry_date=clearing_date,
        status='draft',
        description=f'{application_marker(application.pk)} Zatvaranje predujma',
        reference='',
        is_auto=True,
        source_content_type=ContentType.objects.get_for_model(SupplierPrepaymentApplication),
        source_object_id=application.pk,
        fiscal_period=get_or_create_fiscal_period(prepayment.tenant, clearing_date),
        created_by=user,
    )
    persist_journal_entry_line(
        journal_entry=entry,
        account=payable_account,
        analytic_account=analytic,
        description='Zatvaranje obveze predujmom',
        debit_amount=applied,
        credit_amount=Decimal('0.00'),
    )
    persist_journal_entry_line(
        journal_entry=entry,
        account=prepayment_account,
        description='Primjena danog predujma',
        debit_amount=Decimal('0.00'),
        credit_amount=applied,
    )
    entry.post(user)
    allocation = allocate_payment(
        prepayment.tenant,
        source=source,
        journal_entry=entry,
        amount=applied,
    )
    if allocation is None:
        raise ValidationError('Alokacija saldakonta nije uspjela.')
    application.clearing_journal_entry = entry
    application.subledger_allocation = allocation
    application.save(update_fields=['clearing_journal_entry', 'subledger_allocation'])
    _refresh_prepayment_status(prepayment)
    payable_document.on_settlement_changed()
    return application


@transaction.atomic
def reverse_supplier_prepayment_application(*, application: SupplierPrepaymentApplication, user):
    prepayment = SupplierPrepayment.all_objects.select_for_update().get(pk=application.prepayment_id)
    application = SupplierPrepaymentApplication.all_objects.select_for_update().get(pk=application.pk)
    entry = application.clearing_journal_entry
    if entry is None:
        raise ValidationError('Primjena nema temeljnicu.')
    entry = JournalEntry.all_objects.select_for_update().get(pk=entry.pk)
    if entry.status == 'posted':
        entry.reverse(user)
    elif entry.status != 'reversed':
        raise ValidationError('Temeljnica primjene nije knjižena.')
    _refresh_prepayment_status(prepayment)
    target = application.target
    if target is not None:
        document = resolve_payable_document(target)
        document.lock()
        document.on_settlement_changed()
    prepayment.refresh_from_db()
    return prepayment
