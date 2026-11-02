"""Analitička konta po partnerima, platiteljima troškova i vozilima na zalihi."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import AnalyticAccount, ChartOfAccounts, VehicleAccountingRole

VEHICLE_INVENTORY_SYNTHETIC_CODES = ('667', '6600')


def get_account_by_code(tenant, code: str) -> ChartOfAccounts | None:
    return ChartOfAccounts.all_objects.filter(tenant=tenant, account_code=code).first()


def _resolve_synthetic_code(partner, synthetic_code: str | None) -> str:
    if synthetic_code:
        return synthetic_code
    if partner.partner_type in ('supplier',):
        return '2201'
    if partner.partner_type in ('customer',):
        return '1201'
    if partner.partner_type == 'both':
        raise ValueError(
            'partner_type=both zahtijeva eksplicitni synthetic_code (1201 ili 2201).'
        )
    return '1201'


def _counterparty_type_for_code(synthetic_code: str) -> str:
    return 'supplier' if synthetic_code in ('2201', '2200') else 'partner'


def get_or_create_analytic_for_partner(
    tenant,
    partner,
    *,
    synthetic_code: str | None = None,
) -> AnalyticAccount:
    synthetic_code = _resolve_synthetic_code(partner, synthetic_code)
    counterparty_type = _counterparty_type_for_code(synthetic_code)

    existing = AnalyticAccount.all_objects.filter(
        tenant=tenant,
        counterparty_type=counterparty_type,
        partner=partner,
        synthetic_account__rrif_code=synthetic_code,
    ).select_related('chart_account', 'synthetic_account').first()
    if existing:
        return existing

    synthetic = get_account_by_code(tenant, synthetic_code)
    if synthetic is None:
        raise ChartOfAccounts.DoesNotExist(f"Sintetički konto {synthetic_code} ne postoji za tenant.")

    account_code = f"{synthetic_code}-P{partner.pk:05d}"
    account_name = partner.name[:500]

    with transaction.atomic():
        chart_account = ChartOfAccounts.all_objects.create(
            tenant=tenant,
            account_code=account_code,
            account_name=account_name,
            account_type=synthetic.account_type,
            parent_account=synthetic,
            account_class=synthetic.account_class,
            level=synthetic.level + 1,
            is_synthetic=False,
            is_postable=True,
            is_rrif=False,
        )
        return AnalyticAccount.all_objects.create(
            tenant=tenant,
            synthetic_account=synthetic,
            account_code=account_code,
            account_name=account_name,
            counterparty_type=counterparty_type,
            partner=partner,
            chart_account=chart_account,
        )


def get_or_create_analytic_for_payer(tenant, payer, synthetic_code: str = '2309') -> AnalyticAccount:
    existing = AnalyticAccount.all_objects.filter(
        tenant=tenant,
        counterparty_type='payer',
        expense_payer=payer,
        synthetic_account__rrif_code=synthetic_code,
    ).select_related('chart_account', 'synthetic_account').first()
    if existing:
        return existing

    synthetic = get_account_by_code(tenant, synthetic_code)
    if synthetic is None:
        raise ChartOfAccounts.DoesNotExist(f"Sintetički konto {synthetic_code} ne postoji za tenant.")

    account_code = f"{synthetic_code}-P{payer.pk:05d}"
    account_name = payer.name[:500]

    with transaction.atomic():
        chart_account = ChartOfAccounts.all_objects.create(
            tenant=tenant,
            account_code=account_code,
            account_name=account_name,
            account_type=synthetic.account_type,
            parent_account=synthetic,
            account_class=synthetic.account_class,
            level=synthetic.level + 1,
            is_synthetic=False,
            is_postable=True,
            is_rrif=False,
        )
        return AnalyticAccount.all_objects.create(
            tenant=tenant,
            synthetic_account=synthetic,
            account_code=account_code,
            account_name=account_name,
            counterparty_type='payer',
            expense_payer=payer,
            chart_account=chart_account,
        )


def get_or_create_inventory_analytic_for_vehicle(tenant, vehicle, synthetic_code: str) -> AnalyticAccount:
    """Inventory analitika vozila za daljnju prodaju na jednoj sintetici robe.

    Isto vozilo smije imati više sintetika (npr. 667 roba na putu, 6600 skladište).
    """
    if synthetic_code not in VEHICLE_INVENTORY_SYNTHETIC_CODES:
        raise ValidationError({'synthetic_code': f'Konto {synthetic_code} nije konto zalihe vozila.'})
    if vehicle is None:
        raise ValidationError({'vehicle': 'Nabavna vrijednost zalihe zahtijeva konkretno vozilo.'})
    if vehicle.tenant_id != tenant.id:
        raise ValidationError({'vehicle': 'Vozilo mora pripadati istom tenantu.'})
    if vehicle.accounting_role != VehicleAccountingRole.INVENTORY_FOR_RESALE:
        raise ValidationError({'vehicle': 'Vozilo nije označeno kao vozilo za daljnju prodaju.'})
    if vehicle.fixed_asset_id:
        raise ValidationError({'vehicle': 'Vozilo za daljnju prodaju ne smije biti vezano uz osnovno sredstvo.'})

    synthetic = get_account_by_code(tenant, synthetic_code)
    if synthetic is None:
        raise ChartOfAccounts.DoesNotExist(f"Sintetički konto {synthetic_code} ne postoji za tenant.")

    existing = AnalyticAccount.all_objects.filter(
        tenant=tenant,
        vehicle=vehicle,
        synthetic_account=synthetic,
    ).select_related('chart_account', 'synthetic_account').first()
    if existing:
        return existing

    account_code = f"{synthetic_code}-V{vehicle.pk:05d}"
    account_name = (str(vehicle) or account_code)[:500]

    with transaction.atomic():
        chart_account = ChartOfAccounts.all_objects.create(
            tenant=tenant,
            account_code=account_code,
            account_name=account_name,
            account_type=synthetic.account_type,
            parent_account=synthetic,
            account_class=synthetic.account_class,
            level=synthetic.level + 1,
            is_synthetic=False,
            is_postable=True,
            is_rrif=False,
        )
        return AnalyticAccount.all_objects.create(
            tenant=tenant,
            synthetic_account=synthetic,
            account_code=account_code,
            account_name=account_name,
            counterparty_type='',
            vehicle=vehicle,
            chart_account=chart_account,
        )
