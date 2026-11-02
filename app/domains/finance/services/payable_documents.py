"""Payable document protocol. The prepayment engine talks only to this registry."""

from __future__ import annotations

from decimal import Decimal
from typing import Callable, Protocol

from django.core.exceptions import ValidationError
from django.db import models

from accounting.models import AnalyticAccount, ChartOfAccounts, SubledgerItem
from domains.finance.services.subledger import get_subledger_item_for_source
from expenses.models import Expense
from partners.models import Partner


class PayableDocument(Protocol):
    source: models.Model
    tenant_id: int
    partner: Partner
    currency: str

    def get_open_payable_amount(self) -> Decimal: ...

    def get_payable_account(self) -> tuple[ChartOfAccounts, AnalyticAccount | None]: ...

    def get_subledger_item(self) -> SubledgerItem | None: ...

    def lock(self) -> None: ...

    def on_settlement_changed(self) -> None: ...


_FACTORIES: dict[type, Callable[[models.Model], PayableDocument]] = {}


def register_payable_adapter(model: type, factory: Callable[[models.Model], PayableDocument]) -> None:
    _FACTORIES[model] = factory


def resolve_payable_document(source: models.Model) -> PayableDocument:
    factory = _FACTORIES.get(type(source))
    if factory is None:
        raise ValidationError(f'Nema adaptera obveze za {type(source).__name__}.')
    return factory(source)


def payable_model(source_type: str) -> type:
    for model in _FACTORIES:
        if model._meta.model_name == source_type:
            return model
    raise ValidationError('Nepoznat tip obveze.')


def mark_expense_paid_if_closed(*, tenant, expense: Expense) -> None:
    item = get_subledger_item_for_source(tenant, expense)
    if item is not None and item.status == 'closed':
        if expense.status == 'paid':
            return
        expense.status = 'paid'
        expense.save(update_fields=['status', 'updated_at'])
        return
    if expense.status == 'paid':
        expense.status = 'approved'
        expense.save(update_fields=['status', 'updated_at'])


class ExpensePayableDocument:
    def __init__(self, expense: Expense):
        self.source = expense
        self._item: SubledgerItem | None = None
        self._locked = False

    @property
    def tenant_id(self) -> int:
        return self.source.tenant_id

    @property
    def partner(self) -> Partner:
        return self.source.supplier

    @property
    def currency(self) -> str:
        return self.source.currency or 'EUR'

    def lock(self) -> None:
        self.source = Expense.all_objects.select_for_update().get(pk=self.source.pk)
        item = get_subledger_item_for_source(self.source.tenant, self.source)
        self._item = (
            SubledgerItem.all_objects.select_for_update().get(pk=item.pk) if item is not None else None
        )
        self._locked = True

    def get_subledger_item(self) -> SubledgerItem | None:
        if not self._locked:
            self._item = get_subledger_item_for_source(self.source.tenant, self.source)
        return self._item

    def get_open_payable_amount(self) -> Decimal:
        item = self.get_subledger_item()
        if item is None or item.status not in ('open', 'partial'):
            return Decimal('0.00')
        return Decimal(item.open_amount)

    def get_payable_account(self) -> tuple[ChartOfAccounts, AnalyticAccount | None]:
        item = self.get_subledger_item()
        if item is None:
            raise ValidationError('Dokument nema otvorenu obvezu.')
        partner = self.partner
        line = (
            item.journal_entry.lines.select_related('account', 'analytic_account')
            .filter(credit_amount__gt=0, analytic_account__partner_id=getattr(partner, 'pk', None))
            .order_by('id')
            .first()
        )
        if line is None:
            raise ValidationError('Knjiženje dokumenta nema obvezu prema partneru.')
        return line.account, line.analytic_account

    def on_settlement_changed(self) -> None:
        # The clearing entry already settles the payable. Status paid must not
        # fire the expense_paid posting rule, which would debit 2201 again.
        self.source._skip_auto_posting = True
        mark_expense_paid_if_closed(tenant=self.source.tenant, expense=self.source)


register_payable_adapter(Expense, ExpensePayableDocument)
