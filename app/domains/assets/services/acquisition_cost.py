"""Reconcile FixedAsset.acquisition_cost from capitalized journal entries."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import DepreciationSchedule, FixedAsset, FixedAssetStatus
from accounts.models import AuditLog
from domains.assets.read.service import list_asset_journal_entries


def _money(value: Decimal | None) -> Decimal:
    return Decimal(value or 0).quantize(Decimal('0.01'))


@transaction.atomic
def reconcile_acquisition_cost(
    asset: FixedAsset,
    *,
    expected_from_amount,
    user,
    reason: str,
    case_id: str,
) -> FixedAsset:
    """Set acquisition_cost from capitalized_net. Operator cannot supply a target.

    ``expected_from_amount`` is an optimistic lock against the current card value.
    The new value is derived from the canonical capitalization set (0373 movement
    on purchase + dependent_cost journal entries).
    """
    reason = (reason or '').strip()
    case_id = (case_id or '').strip()
    if not reason:
        raise ValidationError({'reason': 'Razlog usklađenja je obavezan.'})
    if not case_id:
        raise ValidationError({'case_id': 'case_id je obavezan.'})

    expected = _money(expected_from_amount)
    locked = FixedAsset.all_objects.select_for_update().get(pk=asset.pk)
    if locked.status != FixedAssetStatus.IN_PREPARATION:
        raise ValidationError(
            {'status': 'Usklađenje nabavne vrijednosti je dozvoljeno samo za sredstvo u pripremi.'},
        )
    if _money(locked.acquisition_cost) != expected:
        raise ValidationError(
            {
                'acquisition_cost': (
                    f'Očekivano {expected}, aktualno {_money(locked.acquisition_cost)}.'
                ),
            },
        )
    if DepreciationSchedule.all_objects.filter(
        tenant=locked.tenant,
        fixed_asset=locked,
        journal_entry__status='posted',
    ).exists():
        raise ValidationError(
            {'depreciation': 'Sredstvo ima knjiženu amortizaciju; nabavna vrijednost je zaključana.'},
        )

    payload = list_asset_journal_entries(locked.tenant, locked.pk)
    derived = _money(payload['reconciliation']['capitalized_net'])
    if derived <= Decimal('0.00'):
        raise ValidationError(
            {'capitalized_net': 'Kapitalizirani promet mora biti veći od nule.'},
        )
    if derived < expected:
        raise ValidationError(
            {
                'capitalized_net': (
                    f'Izvedeni kapitalizirani promet {derived} ne smije umanjiti '
                    f'karticu ({expected}).'
                ),
            },
        )

    sources = []
    for row in payload['results']:
        amount = row.get('capitalized_amount')
        if amount is None or _money(amount) == Decimal('0.00'):
            continue
        sources.append(
            {
                'journal_entry_id': row['journal_entry_id'],
                'entry_number': row['entry_number'],
                'role': row['role'],
                'capitalized_amount': str(_money(amount)),
            }
        )

    if derived == expected:
        return locked

    locked.acquisition_cost = derived
    locked.full_clean()
    locked.save(update_fields=['acquisition_cost'])

    AuditLog.all_objects.create(
        tenant=locked.tenant,
        user=user,
        action='fixed_asset_acquisition_cost_reconciled',
        model_name='FixedAsset',
        object_id=str(locked.pk),
        changes={
            'case_id': case_id,
            'reason': reason,
            'from_amount': str(expected),
            'to_amount': str(derived),
            'source_journal_entries': sources,
        },
    )
    return locked
