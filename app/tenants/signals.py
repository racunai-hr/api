from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules
from tenants.models import Tenant
from tenants.resolution import invalidate_custom_domain_cache
from tenants.traefik_sync import sync_traefik_custom_domains


@receiver(post_save, sender=Tenant)
def provision_chart_for_new_tenant(sender, instance, created, **kwargs):
    if not created:
        return
    provision_tenant_chart(instance)
    ensure_default_posting_rules(instance)


@receiver(post_save, sender=Tenant)
def sync_custom_domain_on_save(sender, instance, **kwargs):
    invalidate_custom_domain_cache()
    try:
        sync_traefik_custom_domains()
    except OSError:
        pass


@receiver(post_delete, sender=Tenant)
def sync_custom_domain_on_delete(sender, instance, **kwargs):
    invalidate_custom_domain_cache()
    try:
        sync_traefik_custom_domains()
    except OSError:
        pass
