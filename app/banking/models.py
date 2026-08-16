from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from decimal import Decimal

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
        ]

    def __str__(self):
        return f"{self.transaction_date} {self.amount} {self.currency}"

    def save(self, *args, **kwargs):
        if not self.tenant_id and self.bank_statement_id:
            self.tenant = self.bank_statement.tenant
        super().save(*args, **kwargs)
