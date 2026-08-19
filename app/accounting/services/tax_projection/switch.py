"""Tenant VAT projection write switch (Gate D). Fail-closed parsing; no cross-request cache."""

from __future__ import annotations

from enum import StrEnum

from settings.models import SystemParameter

VAT_PROJECTION_WRITE_KEY = 'vat_projection_write'


class SwitchState(StrEnum):
    ON = 'on'
    OFF = 'off'
    INVALID = 'invalid'


class VATProjectionSwitchInvalid(ValueError):
    """Switch value is present but not a recognized boolean token."""

    code = 'VAT_PROJECTION_SWITCH_INVALID'


_TRUE = frozenset({'true', '1', 'yes', 'on'})
_FALSE = frozenset({'false', '0', 'no', 'off'})


def parse_switch_value(raw: str | None) -> SwitchState:
    if raw is None:
        return SwitchState.OFF
    token = raw.strip().lower()
    if not token:
        return SwitchState.OFF
    if token in _TRUE:
        return SwitchState.ON
    if token in _FALSE:
        return SwitchState.OFF
    return SwitchState.INVALID


def read_projection_write_switch(tenant) -> SwitchState:
    """Read switch once. Missing row → off. Unknown value → invalid."""
    param = (
        SystemParameter.all_objects.filter(tenant_id=tenant.pk, key=VAT_PROJECTION_WRITE_KEY)
        .only('value')
        .first()
    )
    if param is None:
        return SwitchState.OFF
    return parse_switch_value(param.value)


def projection_write_enabled(tenant) -> bool:
    """True only when switch is explicitly on. Invalid raises."""
    state = read_projection_write_switch(tenant)
    if state == SwitchState.INVALID:
        raise VATProjectionSwitchInvalid(VATProjectionSwitchInvalid.code)
    return state == SwitchState.ON


def set_projection_write_switch(tenant, *, enabled: bool, description: str = '') -> SystemParameter:
    """Create/update switch under caller-held tenant lock. Reject ON if PREPARED run exists."""
    from accounting.models import VATProjectionRun, VATProjectionRunStatus

    if enabled:
        if VATProjectionRun.all_objects.filter(
            tenant_id=tenant.pk,
            status=VATProjectionRunStatus.PREPARED,
        ).exists():
            raise ValueError('VAT_PROJECTION_SWITCH_PREPARED_RUN_EXISTS')
    value = 'true' if enabled else 'false'
    param, _created = SystemParameter.all_objects.update_or_create(
        tenant=tenant,
        key=VAT_PROJECTION_WRITE_KEY,
        defaults={
            'value': value,
            'data_type': 'boolean',
            'description': description or 'ADR-0019 projection write path (Gate D)',
        },
    )
    return param


def set_projection_write_switch_locked(tenant, *, enabled: bool, description: str = '') -> SystemParameter:
    """Serialize switch changes with rebuild (tenant lock + advisory)."""
    from django.db import transaction

    from accounting.services.tax_projection.locks import advisory_lock_tenant, lock_tenant_for_projection

    with transaction.atomic():
        advisory_lock_tenant(tenant.pk)
        lock_tenant_for_projection(tenant)
        return set_projection_write_switch(tenant, enabled=enabled, description=description)
