from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from tenants.mixins import TenantMixin


class UserProfile(models.Model):
    """Prošireni profil za korisnika"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    phone = models.CharField(max_length=20, blank=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    department = models.CharField(max_length=100, blank=True)
    last_activity = models.DateTimeField(default=timezone.now)
    is_active_session = models.BooleanField(default=False)
    
    def __str__(self):
        return f"{self.user.username} Profile"

    class Meta:
        verbose_name = "Korisnički profil"
        verbose_name_plural = "Korisnički profili"


class AuditLog(TenantMixin, models.Model):
    """Audit log za praćenje promjena"""
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    changes = models.JSONField(default=dict)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        verbose_name = "Dnevnik promjena"
        verbose_name_plural = "Dnevnik promjena"
        
    def __str__(self):
        return f"{self.user} - {self.action} - {self.timestamp}"
