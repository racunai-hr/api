import uuid
from decimal import Decimal

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from tenants.mixins import TenantMixin


class SuperTenantConfig(TenantMixin, models.Model):
    api_base_url = models.URLField(
        default='https://apitest.super.hr',
        verbose_name='SUPER API URL',
    )
    username = models.CharField(max_length=200, verbose_name='SUPER korisničko ime')
    password = models.CharField(max_length=200, verbose_name='SUPER lozinka')
    company_guid = models.CharField(max_length=64, verbose_name='SUPER CompanyGuid')
    is_active = models.BooleanField(default=True, verbose_name='Aktivna integracija')
    is_test_mode = models.BooleanField(default=True, verbose_name='Test okruženje')
    last_inbound_sync_at = models.DateTimeField(null=True, blank=True, verbose_name='Zadnji sync ulaznih')
    last_outbound_poll_at = models.DateTimeField(null=True, blank=True, verbose_name='Zadnji poll izlaznih')

    class Meta:
        verbose_name = 'SUPER konfiguracija'
        verbose_name_plural = 'SUPER konfiguracije'
        constraints = [
            models.UniqueConstraint(fields=['tenant'], name='unique_super_config_per_tenant'),
        ]

    def __str__(self):
        return f'SUPER — {self.tenant.name}'


class SuperDocumentLink(TenantMixin, models.Model):
    DIRECTION_INBOUND = 'inbound'
    DIRECTION_OUTBOUND = 'outbound'
    DIRECTION_CHOICES = [
        (DIRECTION_INBOUND, 'Ulazni'),
        (DIRECTION_OUTBOUND, 'Izlazni'),
    ]

    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    super_guid = models.CharField(max_length=64, db_index=True)
    super_unique_id = models.CharField(max_length=32, blank=True)
    super_status = models.IntegerField(null=True, blank=True)
    status_remark = models.TextField(blank=True)
    ubl_xml = models.TextField(blank=True)
    pdf_path = models.CharField(max_length=500, blank=True)

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')

    last_synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'SUPER dokument'
        verbose_name_plural = 'SUPER dokumenti'
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'direction', 'super_guid'],
                name='unique_super_guid_per_tenant_direction',
            ),
        ]

    def __str__(self):
        return f'{self.get_direction_display()} {self.super_guid}'
