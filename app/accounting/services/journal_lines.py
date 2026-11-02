"""Jedini produkcijski put do JournalEntryLine — invariant mjesta troška."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError

from django.contrib.contenttypes.models import ContentType

from accounting.models import CostCenterKind, JournalEntry, JournalEntryLine

RDG_COST_CENTER_CLASSES = frozenset({'4', '5', '7'})
COST_CENTER_LOCKED_AFTER_POSTING = (
    'Mjesto troška se ne može mijenjati nakon knjiženja. '
    'Ispravak ide kroz storno i ponovno knjiženje.'
)


def account_reporting_class(account) -> str:
    """Klasa konta za MT invariant. Fail-closed: prazna klasa → prva znamenka šifre."""
    raw = (getattr(account, 'account_class', None) or '').strip()
    if raw:
        return raw
    code = (getattr(account, 'account_code', None) or '').strip()
    return code[:1] if code else ''


def validate_cost_center_for_account(account, cost_center) -> None:
    """MT smije završiti samo na RDG stavci (klase 4, 5, 7). Fail-closed."""
    if cost_center is None:
        return
    if account is None:
        raise ValidationError({'cost_center': 'Mjesto troška zahtijeva konto.'})
    if getattr(cost_center, 'kind', None) == CostCenterKind.GROUP:
        raise ValidationError({'cost_center': 'Grupa mjesta troška nije knjiživa.'})
    account_tenant_id = getattr(account, 'tenant_id', None)
    cost_center_tenant_id = getattr(cost_center, 'tenant_id', None)
    if (
        account_tenant_id is not None
        and cost_center_tenant_id is not None
        and account_tenant_id != cost_center_tenant_id
    ):
        raise ValidationError({'cost_center': 'Mjesto troška mora pripadati istom tenantu kao konto.'})
    reporting_class = account_reporting_class(account)
    if reporting_class not in RDG_COST_CENTER_CLASSES:
        raise ValidationError({
            'cost_center': 'Mjesto troška smije biti samo na RDG kontima (klase 4, 5 i 7).',
        })


def source_has_posted_journal(instance) -> bool:
    if not getattr(instance, 'pk', None):
        return False
    ct = ContentType.objects.get_for_model(instance)
    return JournalEntry.all_objects.filter(
        tenant_id=instance.tenant_id,
        source_content_type=ct,
        source_object_id=instance.pk,
        status='posted',
    ).exists()


def reject_cost_center_change_after_posting(instance, update_fields=None) -> None:
    """Source MT je input za resolver; nakon knjiženja JE linija je kanonski trag."""
    if not instance.pk:
        return
    if update_fields is not None:
        names = set(update_fields)
        if 'cost_center' not in names and 'cost_center_id' not in names:
            return
    previous = (
        type(instance).all_objects.filter(pk=instance.pk).values('cost_center_id').first()
    )
    if previous is None or previous['cost_center_id'] == instance.cost_center_id:
        return
    if source_has_posted_journal(instance):
        raise ValidationError({'cost_center': COST_CENTER_LOCKED_AFTER_POSTING})


def persist_journal_entry_line(
    *,
    journal_entry,
    account,
    debit_amount: Decimal,
    credit_amount: Decimal,
    description: str = '',
    analytic_account=None,
    cost_center=None,
) -> JournalEntryLine:
    """Jedini dopušteni put do JournalEntryLine reda u produkciji."""
    validate_cost_center_for_account(account, cost_center)
    return JournalEntryLine.objects.create(
        journal_entry=journal_entry,
        account=account,
        analytic_account=analytic_account,
        cost_center=cost_center,
        description=description or '',
        debit_amount=debit_amount,
        credit_amount=credit_amount,
    )
