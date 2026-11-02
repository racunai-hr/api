"""Usko retargetiranje purchase_journal_entry (remediation / repair)."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import FixedAsset, FixedAssetStatus, JournalEntry
from accounts.models import AuditLog


@transaction.atomic
def retarget_purchase_journal_entry(
    asset: FixedAsset,
    *,
    from_journal_entry: JournalEntry,
    to_journal_entry: JournalEntry,
    user,
    reason: str,
    case_id: str,
) -> FixedAsset:
    """Premjesti FA.purchase_journal_entry s from → to uz audit.

    Namjerno usko: samo ``in_preparation`` asset, isti tenant, from mora biti
    trenutni FK, to mora biti knjižen. Ne koristi se u normalnom create/activate putu.
    """
    reason = (reason or '').strip()
    case_id = (case_id or '').strip()
    if not reason:
        raise ValidationError({'reason': 'Razlog retargeta je obavezan.'})
    if not case_id:
        raise ValidationError({'case_id': 'case_id je obavezan.'})

    locked = FixedAsset.all_objects.select_for_update().get(pk=asset.pk)
    if locked.status != FixedAssetStatus.IN_PREPARATION:
        raise ValidationError(
            {'status': 'Retarget je dozvoljen samo za sredstvo u pripremi.'},
        )
    if locked.purchase_journal_entry_id != from_journal_entry.pk:
        raise ValidationError(
            {
                'purchase_journal_entry': (
                    f'Očekivan purchase JE {from_journal_entry.pk}, '
                    f'aktualno {locked.purchase_journal_entry_id}.'
                ),
            },
        )
    if to_journal_entry.tenant_id != locked.tenant_id:
        raise ValidationError({'purchase_journal_entry': 'Nova temeljnica mora biti istog tenanta.'})
    if to_journal_entry.status != 'posted':
        raise ValidationError({'purchase_journal_entry': 'Nova temeljnica nabave mora biti knjižena.'})
    if from_journal_entry.pk == to_journal_entry.pk:
        raise ValidationError({'purchase_journal_entry': 'from i to ne smiju biti isti.'})

    locked.purchase_journal_entry = to_journal_entry
    locked.full_clean()
    locked.save(update_fields=['purchase_journal_entry'])

    AuditLog.all_objects.create(
        tenant=locked.tenant,
        user=user,
        action='fixed_asset_purchase_je_retarget',
        model_name='FixedAsset',
        object_id=str(locked.pk),
        changes={
            'case_id': case_id,
            'reason': reason,
            'from_journal_entry_id': from_journal_entry.pk,
            'to_journal_entry_id': to_journal_entry.pk,
        },
    )
    return locked
