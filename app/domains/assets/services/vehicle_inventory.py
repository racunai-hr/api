"""Nabavna vrijednost vozila na zalihi. Jedini izvor su knjižene stavke inventory analitika."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db.models import Sum

from accounting.models import JournalEntryLine, Vehicle
from expenses.models import Expense

_ZERO = Decimal('0.00')
_BOOKED_STATUSES = ('posted', 'reversed')


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal('0.01'))


def vehicle_inventory_value(tenant, vehicle: Vehicle) -> dict:
    """Saldo (duguje − potražuje) svih inventory analitika vozila (667-Vn, 6600-Vn, …).

    Stornirana temeljnica i njen storno ulaze oboje, pa se međusobno poništavaju.
    """
    lines = (
        JournalEntryLine.objects.filter(
            journal_entry__tenant=tenant,
            journal_entry__status__in=_BOOKED_STATUSES,
            analytic_account__vehicle=vehicle,
            analytic_account__tenant=tenant,
        )
        .select_related('journal_entry', 'analytic_account')
        .order_by('journal_entry__entry_date', 'journal_entry_id', 'pk')
    )

    by_analytic: dict[str, Decimal] = {}
    by_source: dict[tuple, dict] = {}
    by_component: dict[str, Decimal] = {}
    expense_ct = ContentType.objects.get_for_model(Expense)
    expense_ids = {
        line.journal_entry.source_object_id
        for line in lines
        if line.journal_entry.source_content_type_id == expense_ct.pk
    }
    components = dict(
        Expense.all_objects.filter(tenant=tenant, pk__in=expense_ids).values_list(
            'pk', 'inventory_cost_component',
        )
    )

    total = _ZERO
    for line in lines:
        amount = _money(line.debit_amount) - _money(line.credit_amount)
        total += amount
        code = line.analytic_account.account_code
        by_analytic[code] = by_analytic.get(code, _ZERO) + amount

        entry = line.journal_entry
        key = (entry.source_content_type_id, entry.source_object_id)
        row = by_source.setdefault(key, {
            'source_type': 'expense' if entry.source_content_type_id == expense_ct.pk else '',
            'source_id': entry.source_object_id,
            'amount': _ZERO,
        })
        row['amount'] += amount

        component = ''
        if entry.source_content_type_id == expense_ct.pk:
            component = components.get(entry.source_object_id) or ''
        by_component[component] = by_component.get(component, _ZERO) + amount

    return {
        'vehicle_id': vehicle.pk,
        'total': f'{total:.2f}',
        'by_analytic': [
            {'account_code': code, 'amount': f'{amount:.2f}'}
            for code, amount in sorted(by_analytic.items())
        ],
        'by_source': [
            {**row, 'amount': f"{row['amount']:.2f}"}
            for row in by_source.values()
        ],
        'by_component': [
            {'component': component, 'amount': f'{amount:.2f}'}
            for component, amount in sorted(by_component.items())
        ],
    }


def vehicle_inventory_total(tenant, vehicle: Vehicle) -> Decimal:
    totals = JournalEntryLine.objects.filter(
        journal_entry__tenant=tenant,
        journal_entry__status__in=_BOOKED_STATUSES,
        analytic_account__vehicle=vehicle,
    ).aggregate(debit=Sum('debit_amount'), credit=Sum('credit_amount'))
    return _money(totals['debit']) - _money(totals['credit'])
