from django.db import models
from django.contrib.auth import get_user_model

from tenants.mixins import TenantMixin

User = get_user_model()


class Notification(TenantMixin, models.Model):
    """Obavijesti za korisnike"""
    PRIORITY_CHOICES = [
        ('low', 'Niska'),
        ('medium', 'Srednja'), 
        ('high', 'Visoka'),
        ('urgent', 'Hitna'),
    ]
    
    title = models.CharField(max_length=200)
    message = models.TextField()
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "Obavijest"
        verbose_name_plural = "Obavijesti"


class KPIMetric(TenantMixin, models.Model):
    """KPI metrije za dashboard"""
    name = models.CharField(max_length=100)
    value = models.DecimalField(max_digits=15, decimal_places=2)
    target_value = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    category = models.CharField(max_length=50)
    date = models.DateField()
    
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'name', 'date'], name='unique_kpi_per_tenant_date'),
        ]
        ordering = ['-date']
        verbose_name = "KPI metrika"
        verbose_name_plural = "KPI metrike"
