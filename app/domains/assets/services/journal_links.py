"""Asset-scoped journal-link planning and snapshot apply.

VIN matching is a one-shot candidate proposer for ``plan`` only.
``apply`` never searches VIN — it writes only the explicit snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404

from accounting.models import (
    AssetJournalLinkRole,
    DepreciationSchedule,
    FixedAsset,
    FixedAssetJournalLink,
    JournalEntry,
)
from accounts.models import AuditLog
from domains.assets.read.service import list_asset_journal_entries
from domains.assets.services.creation import extract_vin_from_journal_entry

_ROLE_VALUES = set(AssetJournalLinkRole.values)


@dataclass(frozen=True)
class PlannedLink:
    journal_entry_id: int
    entry_number: str
    proposed_role: str
    reference: str
    already_linked: bool


@dataclass
class LinkPlan:
    tenant: str
    asset_id: int
    vin: str
    candidates: list[PlannedLink] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)

    def snapshot_entries(self) -> list[dict]:
        return [
            {'journal_entry_id': row.journal_entry_id, 'role': row.proposed_role}
            for row in self.candidates
        ]

    def to_dict(self) -> dict:
        return {
            'tenant': self.tenant,
            'asset_id': self.asset_id,
            'vin': self.vin,
            'candidates': [
                {
                    'journal_entry_id': row.journal_entry_id,
                    'entry_number': row.entry_number,
                    'proposed_role': row.proposed_role,
                    'reference': row.reference,
                    'already_linked': row.already_linked,
                }
                for row in self.candidates
            ],
            'entries': self.snapshot_entries(),
            'excluded': self.excluded,
        }


def _lifecycle_ids(asset: FixedAsset) -> dict[int, str]:
    mapping: dict[int, str] = {}
    if asset.purchase_journal_entry_id:
        mapping[asset.purchase_journal_entry_id] = 'purchase_journal_entry'
    if asset.activation_journal_entry_id:
        mapping[asset.activation_journal_entry_id] = 'activation_journal_entry'
    if asset.disposal_journal_entry_id:
        mapping[asset.disposal_journal_entry_id] = 'disposal_journal_entry'
    return mapping


def propose_role_from_reference(reference: str) -> str:
    """Map a structured JE reference prefix to a proposed link role.

    VIN / prefix matching is a plan-time heuristic only.
    """
    first = (reference or '').split('|', 1)[0].strip().upper()
    if first == 'PPMV-UPLATA':
        return AssetJournalLinkRole.PAYMENT
    if first.startswith('FZOEU') or first in {'PPMV', 'PPMV-OBVEZA'}:
        return AssetJournalLinkRole.DEPENDENT_COST
    return AssetJournalLinkRole.OTHER


def plan_asset_journal_links(tenant, asset_id: int) -> LinkPlan:
    """Read-only candidate list for one asset. Never writes.

    VIN is used only to find candidates for human review.
    """
    asset = FixedAsset.all_objects.filter(tenant=tenant, pk=asset_id).first()
    if asset is None:
        raise Http404()

    vin = (asset.vin or '').upper()
    plan = LinkPlan(tenant=tenant.slug, asset_id=asset.pk, vin=vin)
    if not vin:
        return plan

    lifecycle = _lifecycle_ids(asset)
    schedule_ids = set(
        DepreciationSchedule.all_objects.filter(
            tenant=tenant,
            fixed_asset=asset,
            journal_entry__isnull=False,
        ).values_list('journal_entry_id', flat=True)
    )
    linked_ids = set(
        FixedAssetJournalLink.all_objects.filter(
            tenant=tenant,
            fixed_asset=asset,
        ).values_list('journal_entry_id', flat=True)
    )

    entries = JournalEntry.all_objects.filter(
        tenant=tenant,
        status__in=['posted', 'reversed'],
    )
    for entry in entries.iterator():
        extracted = extract_vin_from_journal_entry(entry)
        if extracted != vin:
            continue
        if entry.pk in lifecycle:
            plan.excluded.append(
                {
                    'journal_entry_id': entry.pk,
                    'entry_number': entry.entry_number,
                    'reason': lifecycle[entry.pk],
                }
            )
            continue
        if entry.pk in schedule_ids:
            plan.excluded.append(
                {
                    'journal_entry_id': entry.pk,
                    'entry_number': entry.entry_number,
                    'reason': 'depreciation_schedule',
                }
            )
            continue
        plan.candidates.append(
            PlannedLink(
                journal_entry_id=entry.pk,
                entry_number=entry.entry_number,
                proposed_role=propose_role_from_reference(entry.reference),
                reference=entry.reference or '',
                already_linked=entry.pk in linked_ids,
            )
        )

    plan.candidates.sort(key=lambda row: (row.entry_number, row.journal_entry_id))
    return plan


@transaction.atomic
def apply_asset_journal_links(
    tenant,
    asset_id: int,
    *,
    entries: Iterable[tuple[int, str]],
    case_id: str,
    reason: str,
    user,
) -> dict:
    """Create links from an explicit approved snapshot. Does not search VIN."""
    case_id = (case_id or '').strip()
    reason = (reason or '').strip()
    if not case_id:
        raise ValidationError({'case_id': 'case_id je obavezan.'})
    if not reason:
        raise ValidationError({'reason': 'Razlog je obavezan.'})

    snapshot = list(entries)
    if not snapshot:
        raise ValidationError({'entries': 'Snapshot JE ID-eva je obavezan.'})

    asset = (
        FixedAsset.all_objects.select_for_update()
        .filter(tenant=tenant, pk=asset_id)
        .first()
    )
    if asset is None:
        raise Http404()

    created: list[int] = []
    existing: list[int] = []
    for journal_entry_id, role in snapshot:
        if role not in _ROLE_VALUES:
            raise ValidationError({'role': f'Nepoznata uloga {role!r}.'})
        entry = JournalEntry.all_objects.filter(tenant=tenant, pk=journal_entry_id).first()
        if entry is None:
            raise ValidationError(
                {'journal_entry': f'Temeljnica {journal_entry_id} nije pronađena.'},
            )
        current = FixedAssetJournalLink.all_objects.filter(
            tenant=tenant,
            fixed_asset=asset,
            journal_entry=entry,
        ).first()
        if current is not None:
            if current.role != role:
                raise ValidationError(
                    {
                        'role': (
                            f'Temeljnica {entry.entry_number} već je vezana kao '
                            f'{current.role}, snapshot traži {role}.'
                        ),
                    },
                )
            existing.append(entry.pk)
            continue
        link = FixedAssetJournalLink(
            tenant=tenant,
            fixed_asset=asset,
            journal_entry=entry,
            role=role,
        )
        link.full_clean()
        link.save()
        created.append(entry.pk)

    AuditLog.all_objects.create(
        tenant=tenant,
        user=user,
        action='fixed_asset_journal_link_backfill',
        model_name='FixedAssetJournalLink',
        object_id=str(asset.pk),
        changes={
            'case_id': case_id,
            'reason': reason,
            'created_journal_entry_ids': created,
            'existing_journal_entry_ids': existing,
        },
    )
    return {
        'asset_id': asset.pk,
        'created': created,
        'existing': existing,
    }


def verify_asset_journal_links(tenant, asset_id: int) -> dict:
    payload = list_asset_journal_entries(tenant, asset_id)
    recon = payload['reconciliation']
    return {
        'asset_id': asset_id,
        'reconciliation': recon,
        'ok': recon['balanced'],
    }
