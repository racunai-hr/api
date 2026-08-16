import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def validate_tenant_slug(value):
    reserved = getattr(settings, 'TENANT_RESERVED_SLUGS', [])
    if value in reserved:
        raise ValidationError(
            f'Slug "{value}" je rezerviran za platformu i ne može se koristiti.',
            code='reserved_slug',
        )


class Tenant(models.Model):
    slug = models.SlugField(unique=True, verbose_name='Slug', validators=[validate_tenant_slug])
    name = models.CharField(max_length=200, verbose_name='Naziv')
    custom_domain = models.CharField(
        max_length=255,
        blank=True,
        unique=True,
        null=True,
        verbose_name='Prilagođena domena',
    )
    is_active = models.BooleanField(default=True, verbose_name='Aktivan')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Kreirano')

    class Meta:
        verbose_name = 'Tenant'
        verbose_name_plural = 'Tenanti'
        ordering = ['name']

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.slug:
            validate_tenant_slug(self.slug)


class TenantMembership(models.Model):
    ROLE_CHOICES = [
        ('owner', 'Vlasnik'),
        ('accountant', 'Računovođa'),
        ('viewer', 'Pregled'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='tenant_memberships',
        verbose_name='Korisnik',
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name='memberships',
        verbose_name='Tenant',
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, verbose_name='Uloga')
    is_default = models.BooleanField(default=False, verbose_name='Zadani tenant')

    class Meta:
        verbose_name = 'Članstvo u tenantu'
        verbose_name_plural = 'Članstva u tenantima'
        unique_together = [('user', 'tenant')]
        ordering = ['tenant__name', 'user__username']

    def __str__(self):
        return f'{self.user.username} @ {self.tenant.slug} ({self.get_role_display()})'

    def save(self, *args, **kwargs):
        if self.is_default:
            TenantMembership.objects.filter(
                user=self.user,
                is_default=True,
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class TenantInvitation(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Na čekanju'),
        ('accepted', 'Prihvaćeno'),
        ('revoked', 'Opozvano'),
        ('expired', 'Isteklo'),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name='invitations',
        verbose_name='Tenant',
    )
    email = models.EmailField(verbose_name='E-mail')
    role = models.CharField(
        max_length=20,
        choices=TenantMembership.ROLE_CHOICES,
        verbose_name='Uloga',
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='sent_invitations',
        verbose_name='Pozvao',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Status',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Kreirano')
    expires_at = models.DateTimeField(verbose_name='Istječe')
    accepted_at = models.DateTimeField(null=True, blank=True, verbose_name='Prihvaćeno')

    class Meta:
        verbose_name = 'Pozivnica'
        verbose_name_plural = 'Pozivnice'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.email} → {self.tenant.slug} ({self.get_status_display()})'

    def save(self, *args, **kwargs):
        if not self.expires_at:
            days = getattr(settings, 'TENANT_INVITATION_EXPIRY_DAYS', 7)
            self.expires_at = timezone.now() + timedelta(days=days)
        super().save(*args, **kwargs)

    @property
    def is_valid(self) -> bool:
        return (
            self.status == 'pending'
            and self.expires_at > timezone.now()
        )

    def accept(self, user: User) -> TenantMembership:
        if not self.is_valid:
            raise ValidationError('Pozivnica nije valjana ili je istekla.')
        membership, _ = TenantMembership.objects.update_or_create(
            user=user,
            tenant=self.tenant,
            defaults={'role': self.role},
        )
        self.status = 'accepted'
        self.accepted_at = timezone.now()
        self.save(update_fields=['status', 'accepted_at'])
        return membership
