from __future__ import annotations

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

from domains.assets.services.depreciation import run_monthly_depreciation_for_tenant
from tenants.models import Tenant


def _resolve_system_user():
    User = get_user_model()
    return User.objects.filter(is_superuser=True).order_by('pk').first()


@shared_task(name='accounting.run_monthly_depreciation')
def run_monthly_depreciation_task(
    tenant_slug: str | None = None,
    year: int | None = None,
    month: int | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Tenant-scoped mjesečna amortizacija (Celery beat)."""
    today = timezone.localdate()
    run_year = year or today.year
    run_month = month or today.month
    user = _resolve_system_user()
    if user is None:
        raise RuntimeError('Nema superuser korisnika za automatsko knjiženje amortizacije.')

    tenants = Tenant.objects.all()
    if tenant_slug:
        tenants = tenants.filter(slug=tenant_slug)

    results = []
    for tenant in tenants.iterator():
        results.append(
            run_monthly_depreciation_for_tenant(
                tenant,
                user,
                year=run_year,
                month=run_month,
                dry_run=dry_run,
            ),
        )
    return results
