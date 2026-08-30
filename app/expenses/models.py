import os
import re
import uuid

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q

from tenants.mixins import TenantMixin

from accounting.services.tax_forms.pdv.supply_procedure import VatSupplyProcedure

from .validators import (
    invoice_file_extension_validator,
    validate_invoice_file_content,
    validate_invoice_file_size,
)


def expense_attachment_upload_to(instance, filename):
    base = os.path.basename(filename)
    safe = re.sub(r'[^\w.\-]', '_', base) or 'attachment.bin'
    tenant_id = instance.tenant_id or getattr(instance.expense, 'tenant_id', 'unknown')
    expense_id = instance.expense_id or 'new'
    return f'expenses/attachments/{tenant_id}/{expense_id}/{safe}'


class ExpenseCategory(TenantMixin, models.Model):
    name = models.CharField(max_length=100, verbose_name="Naziv kategorije")
    code = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        verbose_name="Šifra kategorije",
        help_text="Stabilni strojni identitet vrste troška. Bez koda je NULL, ne prazan string.",
    )
    description = models.TextField(blank=True, verbose_name="Opis")
    default_account = models.ForeignKey(
        'accounting.ChartOfAccounts',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expense_categories',
        verbose_name="Konto rashoda",
    )
    default_cost_center = models.ForeignKey(
        'accounting.CostCenter',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expense_categories',
        verbose_name='Zadano mjesto troška',
    )
    is_active = models.BooleanField(default=True, verbose_name="Aktivna")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum stvaranja")

    class Meta:
        verbose_name = "Kategorija troška"
        verbose_name_plural = "Kategorije troškova"
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'name'], name='unique_expense_category_per_tenant'),
            models.UniqueConstraint(
                fields=['tenant', 'code'],
                condition=models.Q(code__isnull=False),
                name='unique_expense_category_code_per_tenant',
            ),
            models.CheckConstraint(
                check=~models.Q(code=''),
                name='expense_category_code_not_empty_string',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.code == '':
            self.code = None
        super().save(*args, **kwargs)

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


class ExpenseAccountSource(models.TextChoices):
    MANUAL_OVERRIDE = 'manual_override', 'Ručna korekcija'
    CATEGORY_DEFAULT = 'category_default', 'Zadano konto vrste troška'
    PARTNER_DEFAULT = 'partner_default', 'Zadana vrsta partnera'
    PARTNER_HISTORY = 'partner_history', 'Povijest partnera'
    POSTING_RULE_FALLBACK = 'posting_rule_fallback', 'Pravilo knjiženja'


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
    partner = models.ForeignKey(
        'partners.Partner',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expense_payers',
        verbose_name='Povezani Partner (MDM)',
    )
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


class ExpensePostingProfile(models.TextChoices):
    """Eksplicitna poslovna/posting klasifikacija troška (ne izvoditi iz konta)."""

    OPEX = 'opex', 'Operativni trošak'
    ASSET_PURCHASE = 'asset_purchase', 'Nabava dugotrajne imovine'


class VehicleLineKind(models.TextChoices):
    """Činjenica s isprave za stavku vozila. Nije PDV/ZPD tretman."""

    TECHNICAL_INSPECTION_SERVICE = 'technical_inspection_service', 'Tehnički pregled'
    REGISTRATION_ADMIN_SERVICE = 'registration_admin_service', 'Administracija registracije'
    ROAD_FEE_ANNUAL = 'road_fee_annual', 'Godišnja naknada za ceste'
    ENVIRONMENTAL_FEE = 'environmental_fee', 'Naknada za okoliš'
    ADMINISTRATIVE_FEE = 'administrative_fee', 'Upravna pristojba'
    MOTOR_VEHICLE_TAX = 'motor_vehicle_tax', 'Porez na cestovna motorna vozila'
    INSURANCE_COMPULSORY = 'insurance_compulsory', 'Obvezno osiguranje'
    UNCLASSIFIED = 'unclassified', 'Neklasificirano'


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
    posting_profile = models.CharField(
        max_length=32,
        choices=ExpensePostingProfile.choices,
        default=ExpensePostingProfile.OPEX,
        verbose_name='Posting profil',
        help_text='Poslovna vrsta dokumenta koja određuje način knjiženja (npr. opex, asset_purchase).',
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
    expense_account = models.ForeignKey(
        'accounting.ChartOfAccounts',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expense_account_overrides',
        verbose_name='Rashodno konto (override)',
    )
    expense_account_source = models.CharField(
        max_length=32,
        choices=ExpenseAccountSource.choices,
        default=ExpenseAccountSource.CATEGORY_DEFAULT,
        verbose_name='Izvor rashodnog konta',
    )
    vehicle = models.ForeignKey(
        'accounting.Vehicle',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='expenses',
        verbose_name='Vozilo',
        help_text='Operativna pripadnost troška vozilu. Ne utječe na knjiženje.',
    )
    cost_center = models.ForeignKey(
        'accounting.CostCenter',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expenses',
        verbose_name='Mjesto troška',
        help_text='Input za resolver. Kanonski trag nakon knjiženja je JournalEntryLine.cost_center.',
    )
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
        if self.category_id and self.category.tenant_id != self.tenant_id:
            raise ValidationError({'category': 'Vrsta troška ne pripada istom tenantu.'})
        if self.expense_account_id:
            account = self.expense_account
            if account.tenant_id != self.tenant_id:
                raise ValidationError({'expense_account': 'Konto ne pripada istom tenantu.'})
            if not account.is_active or not account.is_postable:
                raise ValidationError({'expense_account': 'Konto mora biti aktivno i knjiživo.'})
        if self.vehicle_id and self.vehicle.tenant_id != self.tenant_id:
            raise ValidationError({'vehicle': 'Vozilo mora pripadati istom tenantu.'})
        if self.cost_center_id:
            if self.cost_center.tenant_id != self.tenant_id:
                raise ValidationError({'cost_center': 'Mjesto troška mora pripadati istom tenantu.'})
            if not self.cost_center.is_bookable:
                raise ValidationError({'cost_center': 'Grupa mjesta troška nije knjiživa.'})

    def save(self, *args, **kwargs):
        self._reject_locked_accounting_input_changes(kwargs.get('update_fields'))
        private_methods = {SettlementMethod.PRIVATE_CARD, SettlementMethod.PRIVATE_CASH}
        if self.settlement_method in private_methods and self.status == 'paid':
            if self.reimbursement_status == ReimbursementStatus.NOT_REQUIRED:
                self.reimbursement_status = ReimbursementStatus.PENDING
        elif self.settlement_method not in private_methods:
            self.reimbursement_status = ReimbursementStatus.NOT_REQUIRED
        super().save(*args, **kwargs)

    def _reject_locked_accounting_input_changes(self, update_fields) -> None:
        if not self.pk:
            return
        previous = (
            type(self).all_objects.filter(pk=self.pk)
            .values(
                'status',
                'category_id',
                'expense_account_id',
                'expense_account_source',
                'posting_profile',
                'cost_center_id',
            )
            .first()
        )
        if previous is None or previous['status'] == 'draft':
            return
        tracked = {
            'category_id': 'category',
            'expense_account_id': 'expense_account',
            'expense_account_source': 'expense_account_source',
            'posting_profile': 'posting_profile',
            'cost_center_id': 'cost_center',
        }
        if update_fields is not None:
            update_names = set(update_fields)
            relevant = [
                field for field, name in tracked.items()
                if field in update_names or name in update_names
            ]
            if not relevant:
                return
        changed = {}
        if previous['category_id'] != self.category_id:
            changed['category'] = 'Vrsta troška se ne može mijenjati nakon odobrenja.'
        if previous['expense_account_id'] != self.expense_account_id:
            changed['expense_account'] = 'Rashodno konto se ne može mijenjati nakon odobrenja.'
        if previous['expense_account_source'] != self.expense_account_source:
            changed['expense_account_source'] = 'Izvor konta se ne može mijenjati nakon odobrenja.'
        if previous['posting_profile'] != self.posting_profile:
            if self._allows_remediation_posting_profile_migration(
                previous_profile=previous['posting_profile'],
            ):
                pass
            else:
                changed['posting_profile'] = 'Posting profil se ne može mijenjati nakon odobrenja.'
        if previous['cost_center_id'] != self.cost_center_id:
            changed['cost_center'] = (
                'Mjesto troška se ne može mijenjati nakon knjiženja. '
                'Ispravak ide kroz storno i ponovno knjiženje.'
            )
        if changed:
            raise ValidationError(changed)

    def _allows_remediation_posting_profile_migration(self, *, previous_profile: str) -> bool:
        """Uski escape hatch: samo opex→asset_purchase kroz remediation meta + reason.

        Normalni application/admin save i dalje mora odbiti promjenu profila nakon odobrenja.
        """
        meta = getattr(self, '_remediation_posting_profile_migration', None)
        if not isinstance(meta, dict):
            return False
        reason = (meta.get('reason') or '').strip()
        case_id = (meta.get('case_id') or '').strip()
        if not reason or not case_id:
            return False
        return (
            previous_profile == ExpensePostingProfile.OPEX
            and self.posting_profile == ExpensePostingProfile.ASSET_PURCHASE
            and meta.get('from_profile') == ExpensePostingProfile.OPEX
            and meta.get('to_profile') == ExpensePostingProfile.ASSET_PURCHASE
        )


class ExpenseLine(TenantMixin, models.Model):
    """Stavka ulaznog dokumenta. Činjenice s isprave; ne utječe na AP/JE header tok."""

    expense = models.ForeignKey(
        Expense,
        on_delete=models.CASCADE,
        related_name='lines',
        verbose_name='Trošak',
    )
    position = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        verbose_name='Redoslijed',
    )
    description = models.TextField(verbose_name='Opis stavke')
    net_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Osnovica',
    )
    vat_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Iznos PDV-a',
    )
    gross_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Iznos s PDV-om',
    )
    vehicle_line_kind = models.CharField(
        max_length=64,
        choices=VehicleLineKind.choices,
        null=True,
        blank=True,
        verbose_name='Vrsta stavke vozila',
        help_text='Činjenica s isprave. NULL = nije provedeno ili nije primjenjivo; unclassified = pokušano, nije prepoznato.',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Datum stvaranja')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Datum ažuriranja')

    class Meta:
        verbose_name = 'Stavka troška'
        verbose_name_plural = 'Stavke troška'
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(
                fields=['expense', 'position'],
                name='unique_expense_line_position_per_expense',
            ),
            models.CheckConstraint(
                check=~Q(vehicle_line_kind=''),
                name='expense_line_kind_not_empty_string',
            ),
            models.CheckConstraint(
                check=~Q(description=''),
                name='expense_line_description_not_empty',
            ),
            models.CheckConstraint(
                check=Q(position__gte=1),
                name='expense_line_position_gte_1',
            ),
            models.CheckConstraint(
                check=Q(net_amount__gte=0) & Q(vat_amount__gte=0) & Q(gross_amount__gte=0),
                name='expense_line_amounts_non_negative',
            ),
            models.CheckConstraint(
                check=Q(gross_amount=F('net_amount') + F('vat_amount')),
                name='expense_line_gross_equals_net_plus_vat',
            ),
        ]

    def __str__(self):
        return f'{self.position}. {self.description}'

    def clean(self):
        super().clean()
        if not self.expense_id:
            return
        expense_tenant_id = self.expense.tenant_id
        if self.tenant_id and self.tenant_id != expense_tenant_id:
            raise ValidationError({
                'tenant': 'Tenant stavke mora biti isti kao tenant troška.',
            })

    def save(self, *args, **kwargs):
        if self.vehicle_line_kind == '':
            self.vehicle_line_kind = None
        if self.expense_id:
            self.tenant_id = self.expense.tenant_id
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
        validators=[
            invoice_file_extension_validator,
            validate_invoice_file_size,
            validate_invoice_file_content,
        ],
        verbose_name='Prilog računa',
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


def incoming_invoice_upload_to(instance, filename):
    base = os.path.basename(filename)
    safe = re.sub(r'[^\w.\-]', '_', base) or 'invoice.bin'
    tenant_id = instance.tenant_id or 'unknown'
    token = getattr(instance, 'import_uuid', None) or uuid.uuid4()
    return f'purchasing/invoice-imports/{tenant_id}/{token}/{safe}'


class IncomingInvoiceImport(TenantMixin, models.Model):
    STATUS_QUEUED = 'queued'
    STATUS_PROCESSING = 'processing'
    STATUS_EXTRACTED = 'extracted'
    STATUS_FAILED = 'failed'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_DISCARDED = 'discarded'
    STATUS_CHOICES = [
        (STATUS_QUEUED, 'U redu'),
        (STATUS_PROCESSING, 'Obrada'),
        (STATUS_EXTRACTED, 'Ekstrahirano'),
        (STATUS_FAILED, 'Neuspjelo'),
        (STATUS_CONFIRMED, 'Potvrđeno'),
        (STATUS_DISCARDED, 'Odbačeno'),
    ]
    TERMINAL_STATUSES = frozenset({STATUS_CONFIRMED, STATUS_DISCARDED})

    MATCH_EXACT_OIB = 'exact_oib'
    MATCH_VAT = 'vat'
    MATCH_IBAN_CANDIDATE = 'iban_candidate'
    MATCH_MISSING = 'missing'
    MATCH_CHOICES = [
        (MATCH_EXACT_OIB, 'Točan OIB'),
        (MATCH_VAT, 'PDV broj'),
        (MATCH_IBAN_CANDIDATE, 'IBAN kandidat'),
        (MATCH_MISSING, 'Nije pronađen'),
    ]

    DUPLICATE_NONE = 'none'
    DUPLICATE_HARD = 'hard'
    DUPLICATE_BUSINESS = 'business'
    DUPLICATE_CHOICES = [
        (DUPLICATE_NONE, 'Nema'),
        (DUPLICATE_HARD, 'Hard'),
        (DUPLICATE_BUSINESS, 'Poslovni kandidat'),
    ]

    import_uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='incoming_invoice_imports',
        verbose_name='Učitao',
    )
    original_file = models.FileField(
        upload_to=incoming_invoice_upload_to,
        validators=[
            invoice_file_extension_validator,
            validate_invoice_file_size,
            validate_invoice_file_content,
        ],
        verbose_name='Originalni račun',
    )
    original_filename = models.CharField(max_length=255, verbose_name='Originalni naziv datoteke')
    content_type = models.CharField(max_length=100, blank=True)
    file_sha256 = models.CharField(max_length=64, db_index=True)
    file_size = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
        db_index=True,
    )
    idempotency_key = models.CharField(max_length=128)
    celery_task_id = models.CharField(max_length=255, blank=True, default='')
    last_error = models.CharField(max_length=500, blank=True, default='')

    ocr_provider = models.CharField(max_length=40, blank=True, default='')
    ocr_model = models.CharField(max_length=80, blank=True, default='')
    ocr_schema_version = models.CharField(max_length=80, blank=True, default='')
    ocr_extracted_at = models.DateTimeField(null=True, blank=True)
    extracted_payload = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    matched_partner = models.ForeignKey(
        'partners.Partner',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='incoming_invoice_imports',
        verbose_name='Spojeni partner',
    )
    partner_match = models.CharField(
        max_length=20,
        choices=MATCH_CHOICES,
        default=MATCH_MISSING,
    )
    partner_diff = models.JSONField(default=list, blank=True)
    partner_candidate_id = models.IntegerField(null=True, blank=True)

    duplicate_kind = models.CharField(
        max_length=20,
        choices=DUPLICATE_CHOICES,
        default=DUPLICATE_NONE,
    )
    duplicate_expense = models.ForeignKey(
        Expense,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ocr_duplicate_imports',
        verbose_name='Duplikat troška',
    )
    duplicate_detail = models.JSONField(default=dict, blank=True)
    duplicate_override = models.BooleanField(default=False)

    confirmed_expense = models.ForeignKey(
        Expense,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ocr_imports',
        verbose_name='Potvrđeni trošak',
    )
    confirmed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='confirmed_incoming_invoice_imports',
        verbose_name='Potvrdio',
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'OCR uvoz ulaznog računa'
        verbose_name_plural = 'OCR uvozi ulaznih računa'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status', '-created_at'], name='ocrimport_tenant_status'),
            models.Index(fields=['tenant', 'file_sha256'], name='ocrimport_tenant_sha'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'idempotency_key'],
                name='unique_incoming_invoice_import_idempotency',
            ),
        ]

    def __str__(self):
        return f'IncomingInvoiceImport #{self.pk} ({self.status})'
