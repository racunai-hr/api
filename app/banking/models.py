import os
import re
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models

from payments.models import BankAccount, Payment
from tenants.mixins import TenantMixin

from banking.provider_models import (  # noqa: F401
    BankApiCall,
    BankConnection,
    BankConsent,
    BankProvider,
    BankWebhookEvent,
    PaymentExecution,
    PaymentExecutionTransition,
    PaymentOrder,
    PaymentOrderTransition,
)


def bank_import_payload_upload_to(instance, filename: str) -> str:
    """Tenant-scoped private path; generated name only (original stays in metadata)."""
    run_uuid = getattr(instance, 'run_uuid', None) or uuid.uuid4()
    tenant_id = getattr(instance, 'tenant_id', None) or 'unknown'
    return f'banking/imports/{tenant_id}/{run_uuid}/payload.bin'


def sanitize_original_filename(name: str) -> str:
    base = os.path.basename(name or '') or 'upload.bin'
    cleaned = re.sub(r'[^\w.\-]+', '_', base, flags=re.UNICODE).strip('._')
    return (cleaned or 'upload.bin')[:200]


class BankStatement(TenantMixin, models.Model):
    STATUS_CHOICES = [
        ('imported', 'Uvezen'),
        ('reconciled', 'Usklađen'),
        ('archived', 'Arhiviran'),
    ]

    statement_number = models.CharField(max_length=50, verbose_name="Broj izvoda")
    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name='statements',
        verbose_name="Bankovni račun",
    )
    statement_date = models.DateField(verbose_name="Datum izvoda")
    opening_balance = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Početno stanje")
    closing_balance = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Završno stanje")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='imported', verbose_name="Status")

    imported_by = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Uvezao")
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum uvoza")
    reconciled_at = models.DateTimeField(null=True, blank=True, verbose_name="Datum usklađivanja")

    class Meta:
        verbose_name = "Bankovni izvod"
        verbose_name_plural = "Bankovni izvodi"
        ordering = ['-statement_date', '-statement_number']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'bank_account', 'statement_number'],
                name='unique_statement_per_tenant_account',
            ),
        ]

    def __str__(self):
        return f"{self.statement_number} - {self.statement_date}"

    def update_reconciliation_status(self):
        pending = self.transactions.filter(match_status='unmatched').exists()
        if not pending and self.transactions.exists():
            self.status = 'reconciled'
            from django.utils import timezone
            self.reconciled_at = timezone.now()
            self.save(update_fields=['status', 'reconciled_at'])


class BankTransaction(TenantMixin, models.Model):
    TRANSACTION_TYPES = [
        ('debit', 'Terećenje'),
        ('credit', 'Odobrenje'),
    ]
    MATCH_STATUS = [
        ('unmatched', 'Neusklađeno'),
        ('suggested', 'Prijedlog'),
        ('matched', 'Usklađeno'),
    ]

    bank_statement = models.ForeignKey(
        BankStatement,
        on_delete=models.CASCADE,
        related_name='transactions',
        verbose_name="Bankovni izvod",
    )
    transaction_date = models.DateField(verbose_name="Datum transakcije")
    value_date = models.DateField(null=True, blank=True, verbose_name="Datum valute")
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Iznos",
    )
    currency = models.CharField(max_length=3, default='EUR', verbose_name="Valuta")
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES, verbose_name="Tip")
    description = models.TextField(blank=True, verbose_name="Opis")
    reference = models.CharField(max_length=100, blank=True, verbose_name="Poziv na broj")
    counterparty_name = models.CharField(max_length=200, blank=True, verbose_name="Naziv druge strane")
    counterparty_iban = models.CharField(max_length=34, blank=True, verbose_name="IBAN druge strane")
    external_id = models.CharField(max_length=100, blank=True, verbose_name="Vanjski ID")
    match_status = models.CharField(
        max_length=10,
        choices=MATCH_STATUS,
        default='unmatched',
        verbose_name="Status usklađivanja",
    )
    matched_payment = models.ForeignKey(
        Payment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bank_transactions',
        verbose_name="Povezano plaćanje",
    )
    matched_journal_entry = models.ForeignKey(
        'accounting.JournalEntry',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='matched_bank_transactions',
        verbose_name="Povezana temeljnica",
    )

    class Meta:
        verbose_name = "Bankovna transakcija"
        verbose_name_plural = "Bankovne transakcije"
        ordering = ['transaction_date', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'bank_statement', 'external_id'],
                condition=models.Q(external_id__gt=''),
                name='unique_external_tx_per_statement',
            ),
            models.UniqueConstraint(
                fields=['matched_payment'],
                condition=models.Q(matched_payment__isnull=False),
                name='unique_banktx_matched_payment',
            ),
            models.UniqueConstraint(
                fields=['matched_journal_entry'],
                condition=models.Q(matched_journal_entry__isnull=False),
                name='unique_banktx_matched_journal',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(matched_payment__isnull=True)
                    | models.Q(matched_journal_entry__isnull=True)
                ),
                name='banktx_at_most_one_match_target',
            ),
        ]

    def __str__(self):
        return f"{self.transaction_date} {self.amount} {self.currency}"

    def save(self, *args, **kwargs):
        if not self.tenant_id and self.bank_statement_id:
            self.tenant = self.bank_statement.tenant
        super().save(*args, **kwargs)


class BankImportRun(TenantMixin, models.Model):
    """Operativni audit async uvoza bankovnog izvoda (ADR-0021)."""

    STATUS_QUEUED = 'queued'
    STATUS_RUNNING = 'running'
    STATUS_SUCCEEDED = 'succeeded'
    STATUS_FAILED = 'failed'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_QUEUED, 'U redu'),
        (STATUS_RUNNING, 'U tijeku'),
        (STATUS_SUCCEEDED, 'Uspješno'),
        (STATUS_FAILED, 'Neuspješno'),
        (STATUS_REJECTED, 'Odbijeno'),
    ]
    SOURCE_UPLOAD = 'upload'
    SOURCE_CHOICES = [
        (SOURCE_UPLOAD, 'Upload'),
    ]
    TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED, STATUS_REJECTED})

    run_uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bank_import_runs',
        verbose_name='Pokrenuo',
    )
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_UPLOAD)
    format = models.CharField(max_length=20, blank=True, verbose_name='Format')
    original_filename = models.CharField(max_length=200, blank=True)
    content_sha256 = models.CharField(max_length=64, db_index=True)
    payload = models.FileField(
        upload_to=bank_import_payload_upload_to,
        blank=True,
        verbose_name='Datoteka',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True)
    idempotency_key = models.CharField(max_length=128, blank=True, default='')
    celery_task_id = models.CharField(max_length=255, blank=True, default='')

    statements_processed = models.PositiveIntegerField(default=0)
    statements_created = models.PositiveIntegerField(default=0)
    statements_updated = models.PositiveIntegerField(default=0)
    transactions_processed = models.PositiveIntegerField(default=0)
    transactions_created = models.PositiveIntegerField(default=0)
    transactions_skipped = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)

    warnings = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Uvoz bankovnog izvoda'
        verbose_name_plural = 'Uvozi bankovnih izvoda'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status', '-created_at'], name='bankimportrun_tenant_status'),
            models.Index(fields=['tenant', 'content_sha256'], name='bankimportrun_tenant_sha'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'actor', 'idempotency_key'],
                condition=models.Q(idempotency_key__gt=''),
                name='unique_bank_import_idempotency_per_actor',
            ),
        ]

    def __str__(self):
        return f'ImportRun #{self.pk} ({self.status})'


class BankSyncRun(TenantMixin, models.Model):
    """Operativni posao PSD2 AIS sinkronizacije (ADR-0021)."""

    STATUS_QUEUED = 'queued'
    STATUS_RUNNING = 'running'
    STATUS_SUCCEEDED = 'succeeded'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_QUEUED, 'U redu'),
        (STATUS_RUNNING, 'U tijeku'),
        (STATUS_SUCCEEDED, 'Uspješno'),
        (STATUS_FAILED, 'Neuspješno'),
    ]
    ACTIVE_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING})
    TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED})

    connection = models.ForeignKey(
        'banking.BankConnection',
        on_delete=models.CASCADE,
        related_name='sync_runs',
        verbose_name='Veza',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bank_sync_runs',
        verbose_name='Pokrenuo',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True)
    transactions_created = models.PositiveIntegerField(default=0)
    transactions_skipped = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True, default='')
    celery_task_id = models.CharField(max_length=255, blank=True, default='')
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)
    auto_create_payments = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Sinkronizacija banke'
        verbose_name_plural = 'Sinkronizacije banke'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'connection', '-created_at'], name='banksyncrun_tenant_conn'),
            models.Index(fields=['status', '-created_at'], name='banksyncrun_status'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['connection'],
                condition=models.Q(status__in=['queued', 'running']),
                name='unique_active_bank_sync_per_connection',
            ),
        ]

    def __str__(self):
        return f'SyncRun #{self.pk} conn={self.connection_id} ({self.status})'


class BankReconcileIdempotency(TenantMixin, models.Model):
    """Idempotency store for reconcile-open-item (ADR-0025)."""

    bank_transaction = models.ForeignKey(
        BankTransaction,
        on_delete=models.CASCADE,
        related_name='reconcile_idempotency_keys',
    )
    idempotency_key = models.CharField(max_length=128)
    subledger_item_id = models.PositiveIntegerField()
    result_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Idempotency usklađivanja'
        verbose_name_plural = 'Idempotency usklađivanja'
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'bank_transaction', 'idempotency_key'],
                name='unique_bank_reconcile_idempotency',
            ),
        ]

    def __str__(self):
        return f'ReconcileIdempotency tx={self.bank_transaction_id} key={self.idempotency_key}'
