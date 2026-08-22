from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from tenants.mixins import TenantMixin

from .constants import IntegrationEnvironment, IntegrationProvider, IntegrationType


class IntegrationConfig(TenantMixin, models.Model):
    integration_type = models.CharField(
        max_length=20,
        choices=IntegrationType.choices,
        verbose_name='Tip integracije',
    )
    provider = models.CharField(
        max_length=20,
        choices=IntegrationProvider.choices,
        verbose_name='Provider',
    )
    environment = models.CharField(
        max_length=20,
        choices=IntegrationEnvironment.choices,
        default=IntegrationEnvironment.PRODUCTION,
        verbose_name='Okruženje',
    )
    is_active = models.BooleanField(default=True, verbose_name='Aktivna')
    display_name = models.CharField(max_length=200, blank=True, verbose_name='Naziv')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Integracija'
        verbose_name_plural = 'Integracije'
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'integration_type', 'provider', 'environment'],
                name='unique_integration_per_tenant_type_provider_env',
            ),
            models.UniqueConstraint(
                fields=['tenant', 'integration_type', 'environment'],
                condition=models.Q(is_active=True),
                name='unique_active_integration_per_type_env',
            ),
        ]

    def __str__(self):
        label = self.display_name or f'{self.get_integration_type_display()} / {self.get_provider_display()}'
        env = self.get_environment_display()
        status = 'aktivna' if self.is_active else 'neaktivna'
        return f'{label} ({env}, {status})'


class IntegrationAuditLog(TenantMixin, models.Model):
    STEP_DOCUMENT_BUILT = 'document_built'
    STEP_SEMANTIC_OK = 'semantic_ok'
    STEP_XSD_OK = 'xsd_ok'
    STEP_SCHEMATRON_OK = 'schematron_ok'
    STEP_SCHEMATRON_SKIPPED = 'schematron_skipped'
    STEP_SIGNED = 'signed'
    STEP_OUTBOUND_SENT = 'outbound_sent'
    STEP_OUTBOUND_FAILED = 'outbound_failed'
    STEP_FISCALIZED = 'fiscalized'
    STEP_FISCAL_FAILED = 'fiscal_failed'
    STEP_INBOUND_RECEIVED = 'inbound_received'
    STEP_EXPENSE_CREATED = 'expense_created'
    STEP_APP_RESPONSE_SENT = 'app_response_sent'
    STEP_CHOICES = [
        (STEP_DOCUMENT_BUILT, 'Dokument izgrađen'),
        (STEP_SEMANTIC_OK, 'Semantička validacija OK'),
        (STEP_XSD_OK, 'XSD validacija OK'),
        (STEP_SCHEMATRON_OK, 'Schematron OK'),
        (STEP_SCHEMATRON_SKIPPED, 'Schematron preskočen'),
        (STEP_SIGNED, 'UBL potpis'),
        (STEP_OUTBOUND_SENT, 'Izlazni poslan'),
        (STEP_OUTBOUND_FAILED, 'Izlazni neuspješan'),
        (STEP_FISCALIZED, 'Fiskaliziran'),
        (STEP_FISCAL_FAILED, 'Fiskalizacija neuspješna'),
        (STEP_INBOUND_RECEIVED, 'Ulazni AS4 zaprimljen'),
        (STEP_EXPENSE_CREATED, 'Ulazni trošak kreiran'),
        (STEP_APP_RESPONSE_SENT, 'ApplicationResponse poslan'),
    ]

    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'
    STATUS_SKIPPED = 'skipped'
    STATUS_CHOICES = [
        (STATUS_SUCCESS, 'Uspjeh'),
        (STATUS_FAILED, 'Greška'),
        (STATUS_SKIPPED, 'Preskočeno'),
    ]

    correlation_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    invoice = models.ForeignKey(
        'invoices.Invoice',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='integration_audit_logs',
    )
    integration_type = models.CharField(
        max_length=20,
        choices=IntegrationType.choices,
        blank=True,
    )
    provider = models.CharField(
        max_length=20,
        choices=IntegrationProvider.choices,
        blank=True,
    )
    step = models.CharField(max_length=30, choices=STEP_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    detail = models.JSONField(default=dict, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='integration_audit_logs',
    )
    super_link = models.ForeignKey(
        'super_integration.SuperDocumentLink',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    fiscal_log = models.ForeignKey(
        'fiscal_gateway.FiscalSubmissionLog',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    as4_link = models.ForeignKey(
        'fiscal_gateway.As4DocumentLink',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Integracijski audit log'
        verbose_name_plural = 'Integracijski audit logovi'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.correlation_id} — {self.step} ({self.status})'


class IntegrationOutboxMessage(TenantMixin, models.Model):
    OPERATION_SEND_ERACUN = 'send_eracun'
    OPERATION_FISCALIZE = 'fiscalize'
    OPERATION_APP_RESPONSE = 'app_response'
    OPERATION_CHOICES = [
        (OPERATION_SEND_ERACUN, 'Pošalji eRačun'),
        (OPERATION_FISCALIZE, 'Fiskaliziraj'),
        (OPERATION_APP_RESPONSE, 'ApplicationResponse'),
    ]

    STATUS_PENDING = 'pending'
    STATUS_RETRYING = 'retrying'
    STATUS_SUCCEEDED = 'succeeded'
    STATUS_DEAD = 'dead'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Na čekanju'),
        (STATUS_RETRYING, 'Ponovni pokušaj'),
        (STATUS_SUCCEEDED, 'Uspješno'),
        (STATUS_DEAD, 'Dead letter'),
    ]

    correlation_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    provider = models.CharField(max_length=20, choices=IntegrationProvider.choices, blank=True)
    operation = models.CharField(max_length=30, choices=OPERATION_CHOICES)
    invoice = models.ForeignKey(
        'invoices.Invoice',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='integration_outbox_messages',
    )
    payload_ref = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_error = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Integracijski outbox'
        verbose_name_plural = 'Integracijski outbox poruke'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.operation} — {self.status} ({self.correlation_id})'


class InboundGatewayDocumentLink(TenantMixin, models.Model):
    """Cached mapping from Expense → intermediary inbound document_id (read-only resolve)."""

    expense = models.OneToOneField(
        'expenses.Expense',
        on_delete=models.CASCADE,
        related_name='gateway_document_link',
    )
    gateway_document_id = models.UUIDField(db_index=True)
    bound_provider = models.CharField(max_length=40, blank=True)
    provider_ref = models.CharField(max_length=128, blank=True)
    taxpayer_oib = models.CharField(max_length=11)
    rejection_capable = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Gateway inbound document link'
        verbose_name_plural = 'Gateway inbound document links'

    def __str__(self):
        return f'Expense {self.expense_id} → {self.gateway_document_id}'


class InboundEracunRejectionAttempt(TenantMixin, models.Model):
    """Local pending e-reporting rejection attempt (Expense.status stays unchanged until terminal sync)."""

    STATUS_PENDING = 'pending'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_FAILED, 'Failed'),
    ]

    expense = models.ForeignKey(
        'expenses.Expense',
        on_delete=models.CASCADE,
        related_name='eracun_rejection_attempts',
    )
    gateway_document_id = models.UUIDField()
    attempt_id = models.UUIDField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=200)
    reason_code = models.CharField(max_length=64)
    reason_text = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    e_reporting_status = models.CharField(max_length=32, default='PENDING')
    provider_ref = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Inbound eRačun rejection attempt'
        verbose_name_plural = 'Inbound eRačun rejection attempts'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'idempotency_key'],
                name='unique_inbound_eracun_rejection_idempotency',
            ),
            models.UniqueConstraint(
                fields=['expense'],
                condition=models.Q(status='pending'),
                name='unique_pending_inbound_eracun_rejection_per_expense',
            ),
        ]

    def __str__(self):
        return f'Reject expense={self.expense_id} {self.status} ({self.e_reporting_status})'
