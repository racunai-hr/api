"""Backfill nedostajećih DEFAULT_POSTING_RULES (uključujući 'payroll_posted') za postojeće tenante.

`ensure_default_posting_rules` je create-only/idempotentno (vidi
accounting/services/posting.py) — sigurno za ponovno pokretanje.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    from accounting.services.posting import ensure_default_posting_rules

    Tenant = apps.get_model('tenants', 'Tenant')
    # ensure_default_posting_rules koristi prave (ne-historical) modele/manager,
    # dovoljno je samo imati popis tenant id-ova iz historical modela.
    from tenants.models import Tenant as RealTenant

    for tenant_id in Tenant.objects.values_list('id', flat=True):
        tenant = RealTenant.objects.filter(pk=tenant_id).first()
        if tenant is not None:
            ensure_default_posting_rules(tenant)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0041_alter_postingrule_amount_field_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
