from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from decimal import Decimal

from invoices.models import Invoice
from tenants.mixins import TenantMixin


class BankAccount(TenantMixin, models.Model):
    STATUS_CHOICES = [
        ('pending_discovery', 'Čeka otkrivanje'),
        ('active', 'Aktivan'),
        ('inactive', 'Neaktivan'),
    ]

    account_name = models.CharField(max_length=100, verbose_name="Naziv računa")
    bank_name = models.CharField(max_length=100, verbose_name="Naziv banke")
    account_number = models.CharField(max_length=50, verbose_name="Broj računa")
    iban = models.CharField(max_length=34, blank=True, null=True, verbose_name="IBAN")
    swift_code = models.CharField(max_length=11, blank=True, verbose_name="SWIFT kod")
    currency = models.CharField(max_length=3, default='EUR', verbose_name="Valuta")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active',
        verbose_name="Status",
    )
    provider_connection = models.ForeignKey(
        'banking.BankConnection',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='discovered_accounts',
        verbose_name="PSD2 veza",
    )
    external_account_id = models.CharField(max_length=100, blank=True, verbose_name="Vanjski ID računa")
    discovered_at = models.DateTimeField(null=True, blank=True, verbose_name="Otkriven")
    is_active = models.BooleanField(default=True, verbose_name="Aktivan")
    ledger_account = models.ForeignKey(
        'accounting.ChartOfAccounts',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bank_accounts',
        verbose_name="Konto u knjizi",
        help_text="Transakcijski konto u RRiF-u (npr. 1000). Ako nije postavljeno, koristi se 1000.",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum stvaranja")

    class Meta:
        verbose_name = "Bankovni račun"
        verbose_name_plural = "Bankovni računi"
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'account_number'], name='unique_account_number_per_tenant'),
            models.UniqueConstraint(
                fields=['tenant', 'iban'],
                condition=models.Q(iban__isnull=False) & ~models.Q(iban=''),
                name='unique_iban_per_tenant',
            ),
        ]

    def __str__(self):
        label = self.iban or self.account_number
        return f"{self.account_name} - {label}"


class Payment(TenantMixin, models.Model):
    PAYMENT_TYPES = [
        ('incoming', 'Uplata'),
        ('outgoing', 'Isplata'),
    ]
    
    PAYMENT_METHODS = [
        ('bank_transfer', 'Bankovni transfer'),
        ('cash', 'Gotovina'),
        ('card', 'Kartica'),
        ('check', 'Ček'),
        ('other', 'Ostalo'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'Na čekanju'),
        ('completed', 'Završeno'),
        ('failed', 'Neuspješno'),
        ('cancelled', 'Otkazano'),
    ]

    payment_number = models.CharField(max_length=50, verbose_name="Broj uplate/isplate")
    payment_type = models.CharField(max_length=10, choices=PAYMENT_TYPES, verbose_name="Tip plaćanja")
    payment_method = models.CharField(max_length=15, choices=PAYMENT_METHODS, verbose_name="Način plaćanja")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', verbose_name="Status")
    
    amount = models.DecimalField(max_digits=15, decimal_places=2, 
                               validators=[MinValueValidator(Decimal('0.01'))], 
                               verbose_name="Iznos")
    currency = models.CharField(max_length=3, default='EUR', verbose_name="Valuta")
    
    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, 
                                   related_name='payments', verbose_name="Bankovni račun")
    related_invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='payments', verbose_name="Povezani račun")
    
    payment_date = models.DateField(verbose_name="Datum plaćanja")
    value_date = models.DateField(blank=True, null=True, verbose_name="Datum valute")
    
    description = models.TextField(blank=True, verbose_name="Opis")
    reference_number = models.CharField(max_length=50, blank=True, verbose_name="Poziv na broj")
    counterparty_name = models.CharField(max_length=200, blank=True, verbose_name="Naziv druge strane")
    counterparty_account = models.CharField(max_length=50, blank=True, verbose_name="Račun druge strane")
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Kreirao")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum stvaranja")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Datum ažuriranja")

    class Meta:
        verbose_name = "Plaćanje"
        verbose_name_plural = "Plaćanja"
        ordering = ['-payment_date', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'payment_number'], name='unique_payment_number_per_tenant'),
        ]

    def __str__(self):
        return f"{self.payment_number} - {self.amount} {self.currency}"
