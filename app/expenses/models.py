import os
import re

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from tenants.mixins import TenantMixin

from accounting.services.tax_forms.pdv.supply_procedure import VatSupplyProcedure

from .validators import pdf_extension_validator, validate_pdf_content, validate_pdf_file_size


def expense_attachment_upload_to(instance, filename):
    base = os.path.basename(filename)
    safe = re.sub(r'[^\w.\-]', '_', base)
    if not safe.lower().endswith('.pdf'):
        safe = f'{safe}.pdf'
    tenant_id = instance.tenant_id or getattr(instance.expense, 'tenant_id', 'unknown')
    expense_id = instance.expense_id or 'new'
    return f'expenses/attachments/{tenant_id}/{expense_id}/{safe}'


class ExpenseCategory(TenantMixin, models.Model):
    name = models.CharField(max_length=100, verbose_name="Naziv kategorije")
    description = models.TextField(blank=True, verbose_name="Opis")
    default_account = models.ForeignKey(
        'accounting.ChartOfAccounts',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expense_categories',
        verbose_name="Konto rashoda",
    )
    is_active = models.BooleanField(default=True, verbose_name="Aktivna")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum stvaranja")

    class Meta:
        verbose_name = "Kategorija troška"
        verbose_name_plural = "Kategorije troškova"
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'name'], name='unique_expense_category_per_tenant'),
        ]

    def __str__(self):
        return self.name


class Supplier(TenantMixin, models.Model):
    """DEPRECATED (TD-001): koristiti ``partners.Partner``. Tablica zadržana za rollback."""

    name = models.CharField(max_length=200, verbose_name="Naziv dobavljača")
    address = models.TextField(blank=True, verbose_name="Adresa")
    city = models.CharField(max_length=100, blank=True, verbose_name="Grad")
    postal_code = models.CharField(max_length=20, blank=True, verbose_name="Poštanski broj")
    country = models.CharField(max_length=100, default="Croatia", verbose_name="Država")
    tax_number = models.CharField(max_length=20, blank=True, verbose_name="OIB")
    email = models.EmailField(blank=True, verbose_name="E-mail")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Telefon")
    is_active = models.BooleanField(default=True, verbose_name="Aktivan")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum stvaranja")

    class Meta:
        verbose_name = "Dobavljač"
        verbose_name_plural = "Dobavljači"
        ordering = ['name']

    def __str__(self):
        return self.name


class ExpenseSource(models.TextChoices):
    MANUAL = 'manual', 'Ručno'
    SUPER = 'super', 'SUPER'
    F1_CSV = 'f1_csv', 'F1 CSV'
    OCR = 'ocr', 'OCR'
    EMAIL = 'email', 'E-mail'


class PaymentMethod(models.TextChoices):
    CARD = 'card', 'Kartica'
    CASH = 'cash', 'Gotovina'
    TRANSFER = 'transfer', 'Transfer'
    OTHER = 'other', 'Ostalo'


class SettlementMethod(models.TextChoices):
    BUSINESS_ACCOUNT = 'business_account', 'Poslovni račun'
    COMPANY_CASH = 'company_cash', 'Gotovina (blagajna)'
    PRIVATE_CARD = 'private_card', 'Privatna kartica zaposlenika/vlasnika'
    PRIVATE_CASH = 'private_cash', 'Privatna gotovina'


class ReimbursementStatus(models.TextChoices):
    NOT_REQUIRED = 'not_required', 'Nije potrebna nadoknada'
    PENDING = 'pending', 'Čeka refundaciju'
    PARTIALLY_REIMBURSED = 'partially_reimbursed', 'Djelomično refundirano'
    REIMBURSED = 'reimbursed', 'U potpunosti refundirano'


class ExpensePayerType(models.TextChoices):
    EMPLOYEE = 'employee', 'Zaposlenik'
    OWNER = 'owner', 'Vlasnik'
    DIRECTOR = 'director', 'Direktor'
    OTHER = 'other', 'Ostalo'


class ExpensePayer(TenantMixin, models.Model):
    name = models.CharField(max_length=200, verbose_name='Ime')
    oib = models.CharField(max_length=20, blank=True, verbose_name='OIB')
    type = models.CharField(
        max_length=20,
        choices=ExpensePayerType.choices,
        default=ExpensePayerType.OTHER,
        verbose_name='Tip',
    )
    is_active = models.BooleanField(default=True, verbose_name='Aktivan')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Datum stvaranja')

    class Meta:
        verbose_name = 'Platitelj troška'
        verbose_name_plural = 'Platitelji troškova'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'name'], name='unique_expense_payer_per_tenant'),
        ]

    def __str__(self):
        return self.name


class Expense(TenantMixin, models.Model):
    STATUS_CHOICES = [
        ('draft', 'Nacrt'),
        ('submitted', 'Poslan'),
        ('approved', 'Odobren'),
        ('paid', 'Plaćen'),
        ('rejected', 'Odbačen'),
    ]

    expense_number = models.CharField(max_length=50, verbose_name="Broj troška")
    source = models.CharField(
        max_length=20,
        choices=ExpenseSource.choices,
        default=ExpenseSource.MANUAL,
        verbose_name='Izvor',
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        blank=True,
        verbose_name='Način plaćanja',
    )
    settlement_method = models.CharField(
        max_length=20,
        choices=SettlementMethod.choices,
        blank=True,
        verbose_name='Način podmirenja',
    )
    paid_by = models.ForeignKey(
        ExpensePayer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expenses',
        verbose_name='Podmirenje — platitelj',
    )
    reimbursement_status = models.CharField(
        max_length=30,
        choices=ReimbursementStatus.choices,
        default=ReimbursementStatus.NOT_REQUIRED,
        verbose_name='Status nadoknade',
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft', verbose_name="Status")
    
    category = models.ForeignKey(ExpenseCategory, on_delete=models.CASCADE, 
                               related_name='expenses', verbose_name="Kategorija")
    supplier = models.ForeignKey(
        'partners.Partner',
        on_delete=models.CASCADE,
        related_name='expenses',
        verbose_name="Dobavljač",
    )
    
    amount = models.DecimalField(max_digits=15, decimal_places=2, 
                               validators=[MinValueValidator(Decimal('0.01'))], 
                               verbose_name="Iznos")
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0, 
                                   validators=[MinValueValidator(Decimal('0.00'))], 
                                   verbose_name="Iznos PDV-a")
    vat_procedure = models.CharField(
        max_length=20,
        choices=VatSupplyProcedure.choices,
        default=VatSupplyProcedure.STANDARD,
        verbose_name='PDV postupak',
        help_text='IOSS uvoz — mapira na PDV box 308',
    )
    currency = models.CharField(max_length=3, default='EUR', verbose_name="Valuta")
    
    expense_date = models.DateField(verbose_name="Datum troška")
    due_date = models.DateField(blank=True, null=True, verbose_name="Datum dospijeća")
    
    receipt_number = models.CharField(max_length=50, blank=True, verbose_name="Broj računa/potvrde")
    description = models.TextField(verbose_name="Opis troška")
    notes = models.TextField(blank=True, verbose_name="Napomene")
    
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Kreirao")
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='approved_expenses', verbose_name="Odobrio")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum stvaranja")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Datum ažuriranja")

    class Meta:
        verbose_name = "Trošak"
        verbose_name_plural = "Troškovi"
        ordering = ['-expense_date', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'expense_number'], name='unique_expense_number_per_tenant'),
        ]

    def __str__(self):
        return f"{self.expense_number} - {self.amount} {self.currency}"

    def clean(self):
        super().clean()
        private_methods = {SettlementMethod.PRIVATE_CARD, SettlementMethod.PRIVATE_CASH}
        if self.settlement_method in private_methods and self.status == 'paid' and not self.paid_by_id:
            raise ValidationError({
                'paid_by': 'Platitelj je obavezan za privatno podmirenje plaćenog troška.',
            })

    def save(self, *args, **kwargs):
        private_methods = {SettlementMethod.PRIVATE_CARD, SettlementMethod.PRIVATE_CASH}
        if self.settlement_method in private_methods and self.status == 'paid':
            if self.reimbursement_status == ReimbursementStatus.NOT_REQUIRED:
                self.reimbursement_status = ReimbursementStatus.PENDING
        elif self.settlement_method not in private_methods:
            self.reimbursement_status = ReimbursementStatus.NOT_REQUIRED
        super().save(*args, **kwargs)


class ExpenseAttachment(TenantMixin, models.Model):
    expense = models.ForeignKey(
        Expense,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name='Trošak',
    )
    file = models.FileField(
        upload_to=expense_attachment_upload_to,
        validators=[pdf_extension_validator, validate_pdf_file_size, validate_pdf_content],
        verbose_name='PDF prilog',
    )
    original_filename = models.CharField(max_length=255, verbose_name='Originalni naziv datoteke')
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='expense_attachments',
        verbose_name='Učitao',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Datum učitavanja')

    class Meta:
        verbose_name = 'Prilog troška'
        verbose_name_plural = 'Prilozi troškova'
        ordering = ['-created_at']

    def __str__(self):
        return self.original_filename or self.file.name

    def save(self, *args, **kwargs):
        if self.expense_id and not self.tenant_id:
            self.tenant_id = self.expense.tenant_id
        if self.file and not self.original_filename:
            self.original_filename = os.path.basename(self.file.name)
        super().save(*args, **kwargs)


class ImportBatch(TenantMixin, models.Model):
    source = models.CharField(max_length=20, choices=ExpenseSource.choices, verbose_name='Izvor')
    filename = models.CharField(max_length=255, blank=True, verbose_name='Datoteka')
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expense_import_batches',
        verbose_name='Učitao',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name='Datum učitavanja')
    dry_run = models.BooleanField(default=False, verbose_name='Dry run')
    rows_total = models.PositiveIntegerField(default=0, verbose_name='Ukupno redaka')
    created_count = models.PositiveIntegerField(default=0, verbose_name='Kreirano')
    duplicates_count = models.PositiveIntegerField(default=0, verbose_name='Duplikata')
    errors_count = models.PositiveIntegerField(default=0, verbose_name='Grešaka')
    report_payload = models.JSONField(default=dict, blank=True, verbose_name='Izvještaj')

    class Meta:
        verbose_name = 'Import batch'
        verbose_name_plural = 'Import batch-evi'
        ordering = ['-uploaded_at']

    def __str__(self):
        label = self.filename or self.get_source_display()
        return f'{label} ({self.uploaded_at:%Y-%m-%d %H:%M})'


class ExpenseImportMetadata(TenantMixin, models.Model):
    expense = models.ForeignKey(
        Expense,
        on_delete=models.CASCADE,
        related_name='import_metadata',
        verbose_name='Trošak',
    )
    batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='metadata_rows',
        verbose_name='Batch',
    )
    source = models.CharField(max_length=20, choices=ExpenseSource.choices, verbose_name='Izvor')
    external_id = models.CharField(max_length=255, verbose_name='Vanjski ID')
    jir = models.CharField(max_length=64, blank=True, verbose_name='JIR')
    super_guid = models.CharField(max_length=64, blank=True, verbose_name='SUPER GUID')
    raw_payload = models.JSONField(default=dict, blank=True, verbose_name='Sirovi podaci')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Datum stvaranja')

    class Meta:
        verbose_name = 'Import metapodatak'
        verbose_name_plural = 'Import metapodaci'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'source', 'external_id'],
                name='unique_expense_import_external_id_per_tenant',
            ),
        ]

    def __str__(self):
        return f'{self.source}:{self.external_id}'
