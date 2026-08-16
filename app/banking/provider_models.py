from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from payments.models import BankAccount
from tenants.mixins import TenantMixin


class BankProvider(models.Model):
    ENVIRONMENT_CHOICES = [
        ('sandbox', 'Sandbox'),
        ('production', 'Produkcija'),
    ]

    code = models.CharField(max_length=50, unique=True, verbose_name='Šifra')
    name = models.CharField(max_length=100, verbose_name='Naziv')
    environment = models.CharField(max_length=20, choices=ENVIRONMENT_CHOICES)
    iam_base = models.URLField(verbose_name='IAM baza')
    api_base = models.URLField(verbose_name='API baza')
    service_host = models.CharField(max_length=255, verbose_name='Servisni host')
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Bankovni provider'
        verbose_name_plural = 'Bankovni provideri'

    def __str__(self):
        return f'{self.name} ({self.environment})'


class BankConnection(TenantMixin, models.Model):
    STATUS_CHOICES = [
        ('pending', 'Na čekanju'),
        ('authorizing', 'Autorizacija u tijeku'),
        ('connected', 'Spojeno'),
        ('syncing', 'Sinkronizacija'),
        ('error', 'Greška'),
        ('revoked', 'Opozvano'),
        ('expired', 'Isteklo'),
    ]

    bank_provider = models.ForeignKey(
        BankProvider,
        on_delete=models.PROTECT,
        related_name='connections',
    )
    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name='bank_connections',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    sync_success_streak = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Bankovna veza'
        verbose_name_plural = 'Bankovne veze'
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'bank_provider', 'bank_account'],
                name='unique_bank_connection_per_tenant',
            ),
        ]

    def __str__(self):
        return f'{self.bank_provider.code} → {self.bank_account}'

    def get_active_consent(self) -> BankConsent | None:
        today = timezone.localdate()
        return (
            self.consents.filter(status__in=('authorized', 'valid'), valid_until__gte=today)
            .order_by('-created_at')
            .first()
        )

    def is_syncable(self) -> bool:
        return self.status in ('connected', 'syncing')

    def is_pis_ready(self, *, min_streak: int = 3) -> bool:
        return (
            self.status == 'connected'
            and self.sync_success_streak >= min_streak
            and self.get_active_consent() is not None
        )


class BankConsent(models.Model):
    STATUS_CHOICES = [
        ('received', 'Primljen'),
        ('rejected', 'Odbijen'),
        ('valid', 'Valjan'),
        ('authorized', 'Autoriziran'),
        ('revoked', 'Opozvan'),
        ('expired', 'Istekao'),
    ]
    EXPIRY_WARNING_CHOICES = [
        ('none', 'Nema'),
        ('14d', '14 dana'),
        ('3d', '3 dana'),
        ('expired', 'Istekao'),
    ]

    connection = models.ForeignKey(
        BankConnection,
        on_delete=models.CASCADE,
        related_name='consents',
    )
    consent_id = models.CharField(max_length=100, blank=True)
    authorization_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='received')
    valid_until = models.DateField(null=True, blank=True)
    access_token = models.TextField(blank=True)
    refresh_token = models.TextField(blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)
    revoked_at = models.DateTimeField(null=True, blank=True)
    expiry_notified_at = models.DateTimeField(null=True, blank=True)
    expiry_warning_level = models.CharField(
        max_length=10,
        choices=EXPIRY_WARNING_CHOICES,
        default='none',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Bankovni consent'
        verbose_name_plural = 'Bankovni consenti'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.consent_id or self.pk} ({self.status})'

    @property
    def tenant(self):
        return self.connection.tenant


class BankApiCall(models.Model):
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='bank_api_calls',
    )
    provider = models.ForeignKey(
        BankProvider,
        on_delete=models.CASCADE,
        related_name='api_calls',
    )
    method = models.CharField(max_length=10)
    url = models.TextField()
    request_id = models.CharField(max_length=64, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    correlation_id = models.UUIDField(null=True, blank=True)
    error_summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Bankovni API poziv'
        verbose_name_plural = 'Bankovni API pozivi'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.method} {self.http_status} {self.created_at:%Y-%m-%d %H:%M}'


class BankWebhookEvent(TenantMixin, models.Model):
    provider = models.ForeignKey(
        BankProvider,
        on_delete=models.CASCADE,
        related_name='webhook_events',
    )
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Bankovni webhook'
        verbose_name_plural = 'Bankovni webhookovi'
        ordering = ['-created_at']


class PaymentOrder(TenantMixin, models.Model):
    """OTP PIS nalog — domaće i ostala plaćanja preko Berlin Group API-ja."""

    STATUS_CHOICES = [
        ('draft', 'Nacrt'),
        ('submitted', 'Poslan'),
        ('sca_required', 'SCA potreban'),
        ('authorised', 'Autoriziran'),
        ('accepted', 'Prihvaćen'),
        ('executed', 'Izvršen'),
        ('rejected', 'Odbijen'),
        ('failed', 'Neuspješan'),
    ]
    PAYMENT_PRODUCT_CHOICES = [
        ('domestic-payment', 'Domaće plaćanje'),
        ('sepa-credit-transfers', 'SEPA credit transfer'),
    ]

    connection = models.ForeignKey(
        BankConnection,
        on_delete=models.CASCADE,
        related_name='payment_orders',
    )
    payment = models.ForeignKey(
        'payments.Payment',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payment_orders',
    )
    payment_product = models.CharField(
        max_length=40,
        choices=PAYMENT_PRODUCT_CHOICES,
        default='sepa-credit-transfers',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    otp_payment_id = models.CharField(max_length=100, blank=True, verbose_name='OTP paymentId')
    authorization_id = models.CharField(max_length=100, blank=True)
    sca_redirect_url = models.URLField(blank=True)
    debtor_iban = models.CharField(max_length=34)
    creditor_iban = models.CharField(max_length=34)
    creditor_name = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, default='EUR')
    reference = models.CharField(max_length=140, blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)
    last_error = models.TextField(blank=True)
    posting_journal_entry = models.ForeignKey(
        'accounting.JournalEntry',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='source_payment_orders',
        verbose_name='Temeljnica knjiženja',
    )
    posted_at = models.DateTimeField(null=True, blank=True, verbose_name='Datum knjiženja')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'PIS nalog'
        verbose_name_plural = 'PIS nalozi'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.otp_payment_id or self.pk} ({self.status})'

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        status_being_saved = update_fields is None or 'status' in update_fields

        if self.pk and status_being_saved:
            from banking.services.payment_order_status_guard import (
                PaymentOrderStatusGuardError,
                is_lifecycle_transition_active,
            )

            if not is_lifecycle_transition_active():
                previous = (
                    type(self).all_objects.filter(pk=self.pk)
                    .values_list('status', flat=True)
                    .first()
                )
                if previous is not None and previous != self.status:
                    raise PaymentOrderStatusGuardError(
                        'PaymentOrder.status smije se mijenjati samo preko '
                        'PaymentOrderLifecycle.transition().',
                    )

        super().save(*args, **kwargs)


class PaymentOrderTransition(TenantMixin, models.Model):
    """Append-only audit trag. Nikad update/delete."""

    ACTOR_CHOICES = [
        ('submit', 'Submit'),
        ('otp_callback', 'OTP callback'),
        ('otp_polling', 'OTP polling'),
        ('admin_action', 'Admin akcija'),
        ('system', 'Sustav'),
        ('celery', 'Celery'),
    ]

    order = models.ForeignKey(
        PaymentOrder,
        on_delete=models.CASCADE,
        related_name='transitions',
    )
    sequence = models.PositiveSmallIntegerField()
    from_status = models.CharField(max_length=20)
    to_status = models.CharField(max_length=20)
    actor = models.CharField(max_length=64, choices=ACTOR_CHOICES)
    reason = models.CharField(max_length=200, blank=True)
    correlation_id = models.UUIDField(null=True, blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'PIS prijelaz statusa'
        verbose_name_plural = 'PIS prijelazi statusa'
        ordering = ['order', 'sequence']
        constraints = [
            models.UniqueConstraint(fields=['order', 'sequence'], name='unique_payment_order_transition_sequence'),
        ]

    def __str__(self):
        return f'#{self.order_id} seq={self.sequence}: {self.from_status} → {self.to_status}'


class PaymentExecution(TenantMixin, models.Model):
    """Tehnički pokušaj slanja PaymentOrder-a prema banci.

    ``parent_order_correlation_id`` je immutable snapshot agregatnog ID-a naloga u trenutku
    stvaranja — ne održava se sinkroniziranim s ``PaymentOrder.correlation_id``.
    """

    STATUS_CHOICES = PaymentOrder.STATUS_CHOICES
    PAYMENT_PRODUCT_CHOICES = PaymentOrder.PAYMENT_PRODUCT_CHOICES

    order = models.ForeignKey(
        PaymentOrder,
        on_delete=models.CASCADE,
        related_name='executions',
    )
    attempt = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    provider_payment_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='OTP paymentId',
    )
    authorization_id = models.CharField(max_length=100, blank=True)
    sca_redirect_url = models.URLField(blank=True)
    payment_product = models.CharField(
        max_length=40,
        choices=PAYMENT_PRODUCT_CHOICES,
        default='sepa-credit-transfers',
    )
    last_error = models.TextField(blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)
    parent_order_correlation_id = models.UUIDField(
        editable=False,
        verbose_name='Correlation ID naloga (denormalizirano)',
        help_text=(
            'Immutable snapshot of PaymentOrder.correlation_id '
            'captured when the execution was created. '
            'Not kept in sync with the order. '
            'Used for reporting, audit export, and log correlation.'
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'PIS izvršenje'
        verbose_name_plural = 'PIS izvršenja'
        ordering = ['order', '-attempt']
        constraints = [
            models.UniqueConstraint(fields=['order', 'attempt'], name='unique_payment_execution_attempt'),
            models.UniqueConstraint(
                fields=['tenant', 'provider_payment_id'],
                condition=models.Q(provider_payment_id__gt=''),
                name='unique_payment_execution_provider_id_per_tenant',
            ),
        ]

    def __str__(self):
        return f'order={self.order_id} attempt={self.attempt} ({self.status})'

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        status_being_saved = update_fields is None or 'status' in update_fields

        if self.pk and status_being_saved:
            from banking.services.payment_execution_status_guard import (
                PaymentExecutionStatusGuardError,
                is_lifecycle_transition_active,
            )

            if not is_lifecycle_transition_active():
                previous = (
                    type(self).all_objects.filter(pk=self.pk)
                    .values_list('status', flat=True)
                    .first()
                )
                if previous is not None and previous != self.status:
                    raise PaymentExecutionStatusGuardError(
                        'PaymentExecution.status smije se mijenjati samo preko '
                        'ExecutionLifecycle.transition().',
                    )

        super().save(*args, **kwargs)


class PaymentExecutionTransition(TenantMixin, models.Model):
    """Append-only audit trag za PaymentExecution. Nikad update/delete."""

    ACTOR_CHOICES = PaymentOrderTransition.ACTOR_CHOICES

    execution = models.ForeignKey(
        PaymentExecution,
        on_delete=models.CASCADE,
        related_name='transitions',
    )
    sequence = models.PositiveSmallIntegerField()
    from_status = models.CharField(max_length=20)
    to_status = models.CharField(max_length=20)
    actor = models.CharField(max_length=64, choices=ACTOR_CHOICES)
    reason = models.CharField(max_length=200, blank=True)
    correlation_id = models.UUIDField(null=True, blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'PIS prijelaz izvršenja'
        verbose_name_plural = 'PIS prijelazi izvršenja'
        ordering = ['execution', 'sequence']
        constraints = [
            models.UniqueConstraint(
                fields=['execution', 'sequence'],
                name='unique_payment_execution_transition_sequence',
            ),
        ]

    def __str__(self):
        return f'#{self.execution_id} seq={self.sequence}: {self.from_status} → {self.to_status}'
