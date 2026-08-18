import os
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from tenants.mixins import TenantMixin

from accounting.services.tax_forms.pdv.boxes import VATBox


class AccountType(TenantMixin, models.Model):
    TYPE_CHOICES = [
        ('asset', 'Imovina'),
        ('liability', 'Obveze'),
        ('equity', 'Glavnica'),
        ('revenue', 'Prihodi'),
        ('expense', 'Rashodi'),
    ]

    name = models.CharField(max_length=50, choices=TYPE_CHOICES, verbose_name="Tip konta")
    description = models.TextField(blank=True, verbose_name="Opis")

    class Meta:
        verbose_name = "Tip konta"
        verbose_name_plural = "Tipovi kontova"
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'name'], name='unique_account_type_per_tenant'),
        ]

    def __str__(self):
        return self.get_name_display()


class RRIFChartEntry(models.Model):
    """Platform-level RRIF-RP2025 predložak kontnog plana."""

    code = models.CharField(max_length=6, unique=True, verbose_name="RRIF ključ")
    name = models.CharField(max_length=500, verbose_name="Naziv")
    parent_code = models.CharField(max_length=6, blank=True, verbose_name="Nadređeni ključ")
    account_class = models.CharField(max_length=1, verbose_name="Klasa")
    account_type_name = models.CharField(max_length=20, verbose_name="Tip konta")
    level = models.PositiveSmallIntegerField(default=0, verbose_name="Razina")
    is_synthetic = models.BooleanField(default=False, verbose_name="Sintetički")
    is_postable = models.BooleanField(default=True, verbose_name="Knjiživo")

    class Meta:
        verbose_name = "RRIF konto"
        verbose_name_plural = "RRIF kontni plan"
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.name}"


class ChartOfAccounts(TenantMixin, models.Model):
    account_code = models.CharField(max_length=20, verbose_name="Šifra konta")
    rrif_code = models.CharField(max_length=6, blank=True, verbose_name="RRIF ključ")
    account_name = models.CharField(max_length=500, verbose_name="Naziv konta")
    account_type = models.ForeignKey(
        AccountType,
        on_delete=models.CASCADE,
        related_name='accounts',
        verbose_name="Tip konta",
    )
    parent_account = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='sub_accounts',
        verbose_name="Nadređeni konto",
    )
    account_class = models.CharField(max_length=1, blank=True, verbose_name="RRIF klasa")
    level = models.PositiveSmallIntegerField(default=0, verbose_name="Razina")
    is_synthetic = models.BooleanField(default=False, verbose_name="Sintetički")
    is_postable = models.BooleanField(default=True, verbose_name="Knjiživo")
    is_rrif = models.BooleanField(default=False, verbose_name="RRIF konto")
    description = models.TextField(blank=True, verbose_name="Opis")
    is_active = models.BooleanField(default=True, verbose_name="Aktivan")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum stvaranja")

    class Meta:
        verbose_name = "Konto"
        verbose_name_plural = "Kontni plan"
        ordering = ['account_code']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'account_code'], name='unique_account_code_per_tenant'),
        ]

    def __str__(self):
        return f"{self.account_code} - {self.account_name}"


class AnalyticAccount(TenantMixin, models.Model):
    """Analitičko proširenje sintetičkog konta (npr. 1201 za partnera)."""

    COUNTERPARTY_TYPES = [
        ('partner', 'Partner (kupac)'),
        ('supplier', 'Dobavljač'),
        ('payer', 'Platitelj troška'),
    ]

    synthetic_account = models.ForeignKey(
        ChartOfAccounts,
        on_delete=models.CASCADE,
        related_name='analytic_accounts',
        verbose_name="Sintetički konto",
    )
    account_code = models.CharField(max_length=20, verbose_name="Analitička šifra")
    account_name = models.CharField(max_length=500, verbose_name="Naziv")
    counterparty_type = models.CharField(max_length=10, choices=COUNTERPARTY_TYPES, verbose_name="Tip subjekta")
    partner = models.ForeignKey(
        'partners.Partner',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='analytic_accounts',
        verbose_name="Partner",
    )
    expense_payer = models.ForeignKey(
        'expenses.ExpensePayer',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='analytic_accounts',
        verbose_name='Platitelj troška',
    )
    chart_account = models.OneToOneField(
        ChartOfAccounts,
        on_delete=models.CASCADE,
        related_name='analytic_source',
        verbose_name="Konto u kontnom planu",
    )
    is_active = models.BooleanField(default=True, verbose_name="Aktivan")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum stvaranja")

    class Meta:
        verbose_name = "Analitički konto"
        verbose_name_plural = "Analitički konti"
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'account_code'], name='unique_analytic_code_per_tenant'),
        ]

    def __str__(self):
        return f"{self.account_code} - {self.account_name}"


class FiscalPeriod(TenantMixin, models.Model):
    STATUS_CHOICES = [
        ('open', 'Otvoreno'),
        ('closed', 'Zatvoreno'),
    ]

    year = models.PositiveSmallIntegerField(verbose_name="Godina")
    month = models.PositiveSmallIntegerField(verbose_name="Mjesec")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open', verbose_name="Status")
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="Datum zatvaranja")
    closed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='closed_periods',
        verbose_name="Zatvorio",
    )

    class Meta:
        verbose_name = "Fiskalno razdoblje"
        verbose_name_plural = "Fiskalna razdoblja"
        ordering = ['-year', '-month']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'year', 'month'], name='unique_fiscal_period_per_tenant'),
        ]

    def __str__(self):
        return f"{self.month:02d}/{self.year}"


class JournalEntry(TenantMixin, models.Model):
    STATUS_CHOICES = [
        ('draft', 'Nacrt'),
        ('posted', 'Knjiženo'),
        ('reversed', 'Stornirano'),
    ]

    entry_number = models.CharField(max_length=50, verbose_name="Broj temeljnice")
    entry_date = models.DateField(verbose_name="Datum knjiženja")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft', verbose_name="Status")
    description = models.TextField(verbose_name="Opis knjiženja")
    reference = models.CharField(max_length=100, blank=True, verbose_name="Referenca")
    is_auto = models.BooleanField(default=False, verbose_name="Automatsko knjiženje")
    source_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Tip izvora",
    )
    source_object_id = models.PositiveIntegerField(null=True, blank=True, verbose_name="ID izvora")
    source = GenericForeignKey('source_content_type', 'source_object_id')
    reversed_entry = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reversals',
        verbose_name="Storno temeljnice",
    )
    fiscal_period = models.ForeignKey(
        FiscalPeriod,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='journal_entries',
        verbose_name="Fiskalno razdoblje",
    )

    created_by = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Kreirao")
    posted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='posted_entries',
        verbose_name="Knjižio",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum stvaranja")
    posted_at = models.DateTimeField(null=True, blank=True, verbose_name="Datum knjiženja")

    class Meta:
        verbose_name = "Temeljnica"
        verbose_name_plural = "Temeljnice"
        ordering = ['-entry_date', '-entry_number']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'entry_number'], name='unique_entry_number_per_tenant'),
        ]

    def __str__(self):
        return f"{self.entry_number} - {self.entry_date}"

    @property
    def total_debit(self):
        return sum(line.debit_amount for line in self.lines.all())

    @property
    def total_credit(self):
        return sum(line.credit_amount for line in self.lines.all())

    @property
    def balance_difference(self):
        return self.total_debit - self.total_credit

    def clean(self):
        if self.status == 'posted' and self.balance_difference != Decimal('0.00'):
            raise ValidationError('Temeljnica mora biti uravnotežena (duguje = potražuje).')

    def post(self, user):
        if self.status != 'draft':
            raise ValidationError('Samo nacrt se može knjižiti.')
        if self.balance_difference != Decimal('0.00'):
            raise ValidationError('Temeljnica nije uravnotežena.')
        self.status = 'posted'
        self.posted_by = user
        self.posted_at = timezone.now()
        self.save(update_fields=['status', 'posted_by', 'posted_at'])

    def reverse(self, user):
        if self.status != 'posted':
            raise ValidationError('Samo knjižena temeljnica se može stornirati.')
        if self.matched_bank_transactions.exists():
            raise ValidationError(
                'Temeljnica je usklađena s bankovnom transakcijom. '
                'Prvo poništite usklađenje u bankovnoj transakciji.'
            )
        reversal = JournalEntry.objects.create(
            tenant=self.tenant,
            entry_number=f"{self.entry_number}-ST",
            entry_date=timezone.now().date(),
            status='posted',
            description=f"Storno: {self.description}",
            reference=self.reference,
            is_auto=self.is_auto,
            source_content_type=self.source_content_type,
            source_object_id=self.source_object_id,
            reversed_entry=self,
            fiscal_period=self.fiscal_period,
            created_by=user,
            posted_by=user,
            posted_at=timezone.now(),
        )
        for line in self.lines.all():
            JournalEntryLine.objects.create(
                journal_entry=reversal,
                account=line.account,
                analytic_account=line.analytic_account,
                description=f"Storno: {line.description}",
                debit_amount=line.credit_amount,
                credit_amount=line.debit_amount,
            )
        self.status = 'reversed'
        self.save(update_fields=['status'])
        from domains.finance.services.subledger import handle_journal_entry_reversal

        handle_journal_entry_reversal(self)
        return reversal


class JournalEntryLine(models.Model):
    journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.CASCADE,
        related_name='lines',
        verbose_name="Temeljnica",
    )
    account = models.ForeignKey(
        ChartOfAccounts,
        on_delete=models.CASCADE,
        related_name='journal_lines',
        verbose_name="Konto",
    )
    analytic_account = models.ForeignKey(
        AnalyticAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='journal_lines',
        verbose_name="Analitički konto",
    )
    description = models.TextField(blank=True, verbose_name="Opis stavke")
    debit_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name="Duguje",
    )
    credit_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name="Potražuje",
    )

    class Meta:
        verbose_name = "Stavka temeljnice"
        verbose_name_plural = "Stavke temeljnice"

    def __str__(self):
        return f"{self.account.account_code} - {self.debit_amount or self.credit_amount}"


class PostingRule(TenantMixin, models.Model):
    DOCUMENT_TYPES = [
        ('invoice_issued', 'Izdani račun'),
        ('invoice_paid', 'Naplata računa'),
        ('expense_approved', 'Odobren trošak'),
        ('expense_paid', 'Plaćen trošak'),
        ('payment_manual', 'Ručno plaćanje'),
    ]
    AMOUNT_FIELDS = [
        ('subtotal', 'Osnovica'),
        ('tax_amount', 'PDV'),
        ('total_amount', 'Ukupan iznos'),
        ('amount', 'Iznos'),
        ('net_amount', 'Neto iznos'),
    ]

    name = models.CharField(max_length=100, verbose_name="Naziv pravila")
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES, verbose_name="Tip dokumenta")
    debit_account_code = models.CharField(max_length=20, verbose_name="Konto duguje (šifra)")
    credit_account_code = models.CharField(max_length=20, verbose_name="Konto potražuje (šifra)")
    amount_field = models.CharField(max_length=20, choices=AMOUNT_FIELDS, verbose_name="Polje iznosa")
    condition = models.JSONField(default=dict, blank=True, verbose_name="Uvjet")
    priority = models.PositiveSmallIntegerField(default=100, verbose_name="Prioritet")
    is_active = models.BooleanField(default=True, verbose_name="Aktivno")
    use_analytic = models.BooleanField(default=False, verbose_name="Koristi analitiku")

    class Meta:
        verbose_name = "Pravilo knjiženja"
        verbose_name_plural = "Pravila knjiženja"
        ordering = ['document_type', 'priority']

    def __str__(self):
        return self.name


class VATPeriod(TenantMixin, models.Model):
    STATUS_CHOICES = [
        ('open', 'Otvoreno'),
        ('closed', 'Zatvoreno'),
        ('submitted', 'Predano'),
    ]

    year = models.PositiveSmallIntegerField(verbose_name="Godina")
    month = models.PositiveSmallIntegerField(verbose_name="Mjesec")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open', verbose_name="Status")
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name="Datum predaje")

    class Meta:
        verbose_name = "PDV razdoblje"
        verbose_name_plural = "PDV razdoblja"
        ordering = ['-year', '-month']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'year', 'month'], name='unique_vat_period_per_tenant'),
        ]

    def __str__(self):
        return f"PDV {self.month:02d}/{self.year}"

    @property
    def current_return(self):
        submitted = self.returns.filter(status=VATReturnStatus.SUBMITTED).order_by('-version').first()
        if submitted is not None:
            return submitted
        return self.returns.order_by('-version').first()


def vat_return_upload_to(instance, filename: str) -> str:
    period = instance.vat_period
    return (
        f'vat_returns/{instance.tenant.slug}/'
        f'{period.year}/{period.month:02d}/v{instance.version}/{os.path.basename(filename)}'
    )


class VATReturnStatus(models.TextChoices):
    DRAFT = 'draft', 'Nacrt'
    GENERATED = 'generated', 'Generirano'
    SIGNED = 'signed', 'Potpisano'
    SUBMITTED = 'submitted', 'Predano'
    SUPERSEDED = 'superseded', 'Zamijenjeno'
    IMPORTED = 'imported', 'Importirano'


class VATReturnSource(models.TextChoices):
    ERP_GENERATED = 'erp_generated', 'ERP generirano'
    IMPORTED = 'imported', 'Importirano'
    MANUAL_UPLOAD = 'manual_upload', 'Ručni upload'


_VAT_RETURN_IMMUTABLE_STATUSES = frozenset({
    VATReturnStatus.SUBMITTED,
    VATReturnStatus.IMPORTED,
})
_VAT_RETURN_IMMUTABLE_FIELDS = frozenset({
    'payload_snapshot',
    'payload_hash',
    'payload_json',
    'xml_unsigned',
    'xml_submitted',
    'xml_sha256',
})


class VATReturnSubmissionMethod(models.TextChoices):
    MANUAL_EP = 'manual_ep', 'Ručna predaja (ePorezna)'
    API = 'api', 'ePorezna API'
    IMPORT = 'import', 'Import XML'


class VATReturn(TenantMixin, models.Model):
    vat_period = models.ForeignKey(
        VATPeriod,
        on_delete=models.CASCADE,
        related_name='returns',
        verbose_name='PDV razdoblje',
    )
    version = models.PositiveSmallIntegerField(verbose_name='Verzija')
    status = models.CharField(
        max_length=12,
        choices=VATReturnStatus.choices,
        default=VATReturnStatus.GENERATED,
        verbose_name='Status',
    )
    source = models.CharField(
        max_length=16,
        choices=VATReturnSource.choices,
        default=VATReturnSource.ERP_GENERATED,
        verbose_name='Izvor',
    )
    schema_version = models.CharField(
        max_length=10,
        default='11.0',
        verbose_name='Verzija sheme',
    )
    mapping_version = models.PositiveSmallIntegerField(
        default=1,
        verbose_name='Verzija mapiranja',
    )
    payload_snapshot = models.JSONField(verbose_name='Snapshot payloada')
    payload_hash = models.CharField(max_length=64, verbose_name='Hash payloada')
    payload_json = models.FileField(
        upload_to=vat_return_upload_to,
        verbose_name='payload.json',
    )
    xml_unsigned = models.FileField(
        upload_to=vat_return_upload_to,
        null=True,
        blank=True,
        verbose_name='Nepotpisani XML',
    )
    xml_submitted = models.FileField(
        upload_to=vat_return_upload_to,
        null=True,
        blank=True,
        verbose_name='Predani XML',
    )
    xml_sha256 = models.CharField(max_length=64, blank=True, verbose_name='SHA256 predanog XML-a')
    unsigned_xml_sha256 = models.CharField(
        max_length=64,
        blank=True,
        verbose_name='SHA256 nepotpisanog XML-a',
    )
    prepared_by = models.ForeignKey(
        'settings.ResponsiblePerson',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vat_returns_prepared',
        verbose_name='Sastavio',
    )
    superseded_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='supersedes',
        verbose_name='Zamijenjeno verzijom',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Kreirano')

    class Meta:
        verbose_name = 'PDV obrazac'
        verbose_name_plural = 'PDV obrasci'
        ordering = ['-vat_period__year', '-vat_period__month', '-version']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'vat_period', 'version'],
                name='unique_vat_return_version_per_period',
            ),
        ]

    def __str__(self):
        return f'PDV {self.vat_period} v{self.version} ({self.get_status_display()})'

    def get_period(self):
        return self.vat_period

    def get_display_name(self) -> str:
        return str(self)

    def get_version(self) -> int:
        return self.version

    def get_payload_hash(self) -> str | None:
        return self.payload_hash or None

    def save(self, *args, **kwargs):
        if self.pk:
            previous = VATReturn.all_objects.get(pk=self.pk)
            if previous.status in _VAT_RETURN_IMMUTABLE_STATUSES:
                for field in _VAT_RETURN_IMMUTABLE_FIELDS:
                    if getattr(previous, field) != getattr(self, field):
                        raise ValidationError(
                            f'PDV obrazac u statusu "{previous.get_status_display()}" '
                            f'ne smije mijenjati polje {field}.'
                        )
        super().save(*args, **kwargs)


def pdv_s_submission_upload_to(instance, filename: str) -> str:
    """Legacy upload path — retained for historical migrations (0011)."""
    period = instance.vat_period
    return (
        f'pdv_s_submissions/{instance.tenant.slug}/'
        f'{period.year}/{period.month:02d}/{os.path.basename(filename)}'
    )


def zp_return_upload_to(instance, filename: str) -> str:
    period = instance.vat_period
    return (
        f'zp_returns/{instance.tenant.slug}/'
        f'{period.year}/{period.month:02d}/v{instance.version}/{os.path.basename(filename)}'
    )


class ZPReturn(TenantMixin, models.Model):
    vat_period = models.ForeignKey(
        VATPeriod,
        on_delete=models.CASCADE,
        related_name='zp_returns',
        verbose_name='PDV razdoblje',
    )
    version = models.PositiveSmallIntegerField(verbose_name='Verzija')
    schema_version = models.CharField(
        max_length=10,
        default='1.0',
        verbose_name='Verzija sheme',
    )
    mapping_version = models.PositiveSmallIntegerField(
        default=1,
        verbose_name='Verzija mapiranja',
    )
    payload_snapshot = models.JSONField(verbose_name='Snapshot payloada')
    payload_hash = models.CharField(max_length=64, verbose_name='Hash payloada')
    payload_json = models.FileField(
        upload_to=zp_return_upload_to,
        verbose_name='payload.json',
    )
    xml_unsigned = models.FileField(
        upload_to=zp_return_upload_to,
        null=True,
        blank=True,
        verbose_name='Nepotpisani XML',
    )
    xml_submitted = models.FileField(
        upload_to=zp_return_upload_to,
        null=True,
        blank=True,
        verbose_name='Predani XML',
    )
    unsigned_xml_sha256 = models.CharField(
        max_length=64,
        blank=True,
        verbose_name='SHA256 nepotpisanog XML-a',
    )
    prepared_by = models.ForeignKey(
        'settings.ResponsiblePerson',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='zp_returns_prepared',
        verbose_name='Sastavio',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Kreirano')

    class Meta:
        verbose_name = 'ZP obrazac'
        verbose_name_plural = 'ZP obrasci'
        ordering = ['-vat_period__year', '-vat_period__month', '-version']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'vat_period', 'version'],
                name='unique_zp_return_version_per_period',
            ),
        ]

    def __str__(self):
        return f'ZP {self.vat_period} v{self.version}'

    def get_period(self):
        return self.vat_period

    def get_display_name(self) -> str:
        return str(self)

    def get_version(self) -> int:
        return self.version

    def get_payload_hash(self) -> str | None:
        return self.payload_hash or None


class TaxDocumentType(models.TextChoices):
    PDV = 'pdv', 'Obrazac PDV'
    PDV_S = 'pdv_s', 'Obrazac PDV-S'
    ZP = 'zp', 'Obrazac ZP'


class SubmissionDestination(models.TextChoices):
    EPOREZNA = 'eporezna', 'ePorezna'
    HZZO = 'hzzo', 'HZZO'
    FINA = 'fina', 'FINA'
    MOJEPOREZNA = 'mojeporezna', 'Moje porezna'
    FISKALIZACIJA = 'fiskalizacija', 'Fiskalizacija'
    TEST = 'test', 'Test'


class SubmissionEventState(models.TextChoices):
    PENDING = 'pending', 'Na čekanju'
    SUBMITTED = 'submitted', 'Predano'
    REJECTED = 'rejected', 'Odbijeno'
    CANCELLED = 'cancelled', 'Otkazano'


class SubmissionMethod(models.TextChoices):
    """Legacy enum — retained for VATReturnSubmissionMethod mapping only."""

    MANUAL = 'manual', 'Ručno'
    API = 'api', 'API'
    IMPORT = 'import', 'Import'


class SubmissionSource(models.TextChoices):
    MANUAL = 'manual', 'Ručno'
    API = 'api', 'API'
    MIGRATION = 'migration', 'Migracija'
    IMPORT = 'import', 'Import'
    SYSTEM = 'system', 'Sustav'


class PDVSReturn(TenantMixin, models.Model):
    vat_period = models.OneToOneField(
        VATPeriod,
        on_delete=models.CASCADE,
        related_name='pdv_s_return',
        verbose_name='PDV razdoblje',
    )
    version = models.PositiveSmallIntegerField(default=1, verbose_name='Verzija')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Kreirano')

    class Meta:
        verbose_name = 'PDV-S obrazac'
        verbose_name_plural = 'PDV-S obrasci'
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'vat_period'],
                name='unique_pdv_s_return_per_period',
            ),
        ]

    def __str__(self):
        return f'PDV-S {self.vat_period} v{self.version}'

    def get_period(self):
        return self.vat_period

    def get_display_name(self) -> str:
        return str(self)

    def get_version(self) -> int:
        return self.version

    def get_payload_hash(self) -> str | None:
        from accounting.services.submission.protocol import pdv_s_payload_hash

        return pdv_s_payload_hash(self)


_CONTENT_TYPE_DOCUMENT_TYPE = {
    'vatreturn': TaxDocumentType.PDV,
    'pdvsreturn': TaxDocumentType.PDV_S,
    'zpreturn': TaxDocumentType.ZP,
}


def document_type_for_content_type(content_type) -> str:
    slug = content_type.model
    try:
        return _CONTENT_TYPE_DOCUMENT_TYPE[slug]
    except KeyError as exc:
        raise ValueError(f'Nepodržani tip dokumenta za predaju: {slug}') from exc


def submission_event_upload_to(instance, filename: str) -> str:
    doc_type = document_type_for_content_type(instance.content_type)
    return (
        f'submission_events/{instance.tenant.slug}/'
        f'{doc_type}/{instance.submission_no}/'
        f'{os.path.basename(filename)}'
    )


_SUBMISSION_EVENT_IMMUTABLE_FIELDS = frozenset({
    'event_uuid',
    'content_type',
    'object_id',
    'submission_no',
    'destination',
    'external_identifier',
    'payload_hash',
    'submitted_at',
    'submitted_by',
    'source',
    'supersedes_submission',
})


def _submission_field_value(obj, field_name: str):
    if field_name == 'confirmation_attachment':
        file_val = getattr(obj, field_name)
        return file_val.name if file_val else ''
    if field_name in {'content_type', 'submitted_by', 'supersedes_submission'}:
        return getattr(obj, f'{field_name}_id')
    return getattr(obj, field_name)


class SubmissionEvent(TenantMixin, models.Model):
    event_uuid = models.UUIDField(
        unique=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name='Javni ID eventa',
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.PROTECT,
        verbose_name='Tip sadržaja',
    )
    object_id = models.PositiveBigIntegerField(verbose_name='ID dokumenta')
    document = GenericForeignKey('content_type', 'object_id')
    submission_no = models.PositiveSmallIntegerField(verbose_name='Broj predaje')
    state = models.CharField(
        max_length=12,
        choices=SubmissionEventState.choices,
        default=SubmissionEventState.SUBMITTED,
        verbose_name='Status',
    )
    destination = models.CharField(
        max_length=16,
        choices=SubmissionDestination.choices,
        verbose_name='Odredište',
    )
    external_identifier = models.UUIDField(verbose_name='Vanjski identifikator')
    payload_hash = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='Hash sadržaja predaje',
    )
    submitted_at = models.DateTimeField(verbose_name='Datum predaje')
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='submission_events',
        verbose_name='Predao',
    )
    source = models.CharField(
        max_length=10,
        choices=SubmissionSource.choices,
        verbose_name='Izvor zapisa',
    )
    confirmation_attachment = models.FileField(
        upload_to=submission_event_upload_to,
        null=True,
        blank=True,
        verbose_name='Potvrda predaje',
    )
    supersedes_submission = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='superseded_by_events',
        verbose_name='Zamjenjuje predaju',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Kreirano')

    class Meta:
        verbose_name = 'Evidencija predaje'
        verbose_name_plural = 'Evidencije predaje'
        ordering = ['-submitted_at', '-submission_no']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'content_type', 'object_id', 'submission_no'],
                name='unique_submission_no_per_document',
            ),
            models.UniqueConstraint(
                fields=['tenant', 'destination', 'external_identifier'],
                name='unique_external_id_per_destination',
            ),
        ]

    def __str__(self):
        return (
            f'#{self.submission_no} {self.document_type_label} '
            f'({self.get_state_display()}, {self.external_identifier})'
        )

    @property
    def document_type(self) -> str:
        return document_type_for_content_type(self.content_type)

    @property
    def document_type_label(self) -> str:
        return dict(TaxDocumentType.choices).get(self.document_type, self.document_type)

    @property
    def submission_type(self) -> str:
        return 'initial' if self.submission_no == 1 else 'correction'

    def save(self, *args, **kwargs):
        if self.pk:
            previous = SubmissionEvent.all_objects.get(pk=self.pk)
            allow_state_transition = getattr(self, '_allow_state_transition', False)
            allow_attachment_set = getattr(self, '_allow_attachment_set', False)
            for field in _SUBMISSION_EVENT_IMMUTABLE_FIELDS:
                if _submission_field_value(previous, field) != _submission_field_value(self, field):
                    raise ValidationError(
                        f'Evidencija predaje ne smije mijenjati polje {field}.',
                    )
            prev_attachment = _submission_field_value(previous, 'confirmation_attachment')
            new_attachment = _submission_field_value(self, 'confirmation_attachment')
            if prev_attachment != new_attachment:
                if prev_attachment and not allow_attachment_set:
                    raise ValidationError(
                        'Potvrda predaje ne smije se mijenjati nakon postavljanja.',
                    )
            if previous.state != self.state and not allow_state_transition:
                raise ValidationError(
                    'Status predaje može se mijenjati samo kroz transition_submission_state().',
                )
        super().save(*args, **kwargs)


class VATEntryCategory(models.TextChoices):
    DOMESTIC = 'domestic', 'Domestic'
    EU_SUPPLY = 'eu_supply', 'EU isporuka'
    EU_ACQUISITION = 'eu_acquisition', 'EU stjecanje'
    OSS_SUPPLY = 'oss_supply', 'OSS/IOSS e-trgovina'
    IMPORT = 'import', 'Uvoz'
    ADJUSTMENT = 'adjustment', 'Korekcija'


class VATLedgerOrigin(models.TextChoices):
    LEGACY = 'legacy', 'Legacy generator'
    ENGINE = 'engine', 'Tax classification engine'
    MANUAL = 'manual', 'Manual correction'


class VATProjectionRunStatus(models.TextChoices):
    PREPARED = 'PREPARED', 'Prepared'
    APPLIED = 'APPLIED', 'Applied'
    REJECTED = 'REJECTED', 'Rejected'
    STALE = 'STALE', 'Stale'
    FAILED = 'FAILED', 'Failed'


class VATProjectionRun(TenantMixin, models.Model):
    """Audit record for a projection prepare/apply. Gate B does not insert rows."""

    vat_period = models.ForeignKey(
        VATPeriod,
        on_delete=models.CASCADE,
        related_name='projection_runs',
        verbose_name='PDV razdoblje',
    )
    status = models.CharField(
        max_length=16,
        choices=VATProjectionRunStatus.choices,
        verbose_name='Status',
    )
    engine_version = models.PositiveSmallIntegerField(verbose_name='Verzija enginea')
    mapping_version = models.PositiveSmallIntegerField(verbose_name='Verzija mapiranja')
    input_fingerprint = models.CharField(max_length=64, verbose_name='Ulazni fingerprint')
    output_fingerprint = models.CharField(max_length=64, blank=True, verbose_name='Izlazni fingerprint')
    classified_count = models.PositiveIntegerField(default=0)
    not_relevant_count = models.PositiveIntegerField(default=0)
    review_count = models.PositiveIntegerField(default=0)
    invalid_count = models.PositiveIntegerField(default=0)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vat_projection_runs',
    )
    rejection_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'PDV projection run'
        verbose_name_plural = 'PDV projection runovi'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.vat_period} {self.status}'


class VATLedgerEntry(TenantMixin, models.Model):
    LEDGER_I_RA = 'I-RA'
    LEDGER_U_RA = 'U-RA'
    LEDGER_IRA_DEPRECATED = 'IRA'

    LEDGER_TYPES = [
        (LEDGER_I_RA, 'I-RA (izlazni)'),
        (LEDGER_U_RA, 'U-RA (ulazni)'),
        # Zastarjelo — zadržano radi kompatibilnosti; ukloniti u sljedećem većem releaseu.
        (LEDGER_IRA_DEPRECATED, 'IRA (zastarjelo)'),
    ]

    vat_period = models.ForeignKey(
        VATPeriod,
        on_delete=models.CASCADE,
        related_name='entries',
        verbose_name="PDV razdoblje",
    )
    ledger_type = models.CharField(max_length=5, choices=LEDGER_TYPES, verbose_name="Knjiga")
    entry_date = models.DateField(verbose_name="Datum")
    document_number = models.CharField(max_length=50, verbose_name="Broj dokumenta")
    partner_name = models.CharField(max_length=200, verbose_name="Partner")
    partner_oib = models.CharField(max_length=20, blank=True, verbose_name="OIB")
    base_amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Osnovica")
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, verbose_name="Stopa PDV (%)")
    vat_amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Iznos PDV")
    rrif_vat_account = models.CharField(max_length=10, blank=True, verbose_name="RRIF PDV konto")
    source_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    source_object_id = models.PositiveIntegerField(null=True, blank=True)
    source = GenericForeignKey('source_content_type', 'source_object_id')
    vat_box = models.CharField(
        max_length=3,
        choices=VATBox.choices,
        blank=True,
        verbose_name='PDV polje (VATBox)',
    )
    entry_category = models.CharField(
        max_length=20,
        choices=VATEntryCategory.choices,
        default=VATEntryCategory.DOMESTIC,
        verbose_name='Kategorija stavke',
    )
    is_manual = models.BooleanField(default=False, verbose_name='Ručna korekcija')
    origin = models.CharField(
        max_length=16,
        choices=VATLedgerOrigin.choices,
        default=VATLedgerOrigin.LEGACY,
        verbose_name='Podrijetlo retka',
    )
    projection_run = models.ForeignKey(
        'VATProjectionRun',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ledger_entries',
        verbose_name='Projection run',
    )
    mapping_version = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name='Verzija mapiranja',
    )
    source_line_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='ID izvorne stavke',
    )

    class Meta:
        verbose_name = "Stavka PDV knjige"
        verbose_name_plural = "Stavke PDV knjiga"
        ordering = ['entry_date', 'document_number']

    def save(self, *args, **kwargs):
        if self.is_manual and self.origin != VATLedgerOrigin.ENGINE:
            self.origin = VATLedgerOrigin.MANUAL
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ledger_type} {self.document_number}"


class FixedAssetStatus(models.TextChoices):
    IN_PREPARATION = 'in_preparation', 'U pripremi'
    ACTIVE = 'active', 'Aktivno'
    DISPOSED = 'disposed', 'Otpisano'


class FixedAssetOrigin(models.TextChoices):
    PURCHASE = 'purchase', 'Nabava'
    OPENING_BALANCE = 'opening_balance', 'Početno stanje'


class DepreciationMethod(models.TextChoices):
    LINEAR = 'linear', 'Linearna'


class FixedAsset(TenantMixin, models.Model):
    name = models.CharField(max_length=200, verbose_name='Naziv')
    inventory_number = models.CharField(max_length=50, blank=True, verbose_name='Inventarni broj')
    vin = models.CharField(max_length=17, blank=True, verbose_name='VIN')
    registration_plate = models.CharField(max_length=20, blank=True, verbose_name='Registracija')

    status = models.CharField(
        max_length=20,
        choices=FixedAssetStatus.choices,
        default=FixedAssetStatus.IN_PREPARATION,
        verbose_name='Status',
    )
    origin = models.CharField(
        max_length=20,
        choices=FixedAssetOrigin.choices,
        default=FixedAssetOrigin.PURCHASE,
        verbose_name='Porijeklo',
    )

    acquisition_cost = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Nabavna vrijednost',
    )
    purchase_date = models.DateField(verbose_name='Datum nabave')
    in_service_date = models.DateField(null=True, blank=True, verbose_name='Datum stavljanja u uporabu')
    disposed_at = models.DateField(null=True, blank=True, verbose_name='Datum otpisa')

    useful_life_months = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name='Trajnost (mjeseci)',
    )
    depreciation_method = models.CharField(
        max_length=20,
        choices=DepreciationMethod.choices,
        default=DepreciationMethod.LINEAR,
        verbose_name='Metoda amortizacije',
    )
    residual_value = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Ostatak vrijednosti',
    )

    construction_account = models.ForeignKey(
        ChartOfAccounts,
        on_delete=models.PROTECT,
        related_name='fixed_assets_construction',
        verbose_name='Konto u pripremi',
    )
    asset_account = models.ForeignKey(
        ChartOfAccounts,
        on_delete=models.PROTECT,
        related_name='fixed_assets_asset',
        verbose_name='Konto osnovnog sredstva',
    )
    accumulated_depreciation_account = models.ForeignKey(
        ChartOfAccounts,
        on_delete=models.PROTECT,
        related_name='fixed_assets_accumulated_depreciation',
        verbose_name='Konto ispravka vrijednosti',
    )
    depreciation_expense_account = models.ForeignKey(
        ChartOfAccounts,
        on_delete=models.PROTECT,
        related_name='fixed_assets_depreciation_expense',
        verbose_name='Konto troška amortizacije',
    )

    purchase_journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='fixed_assets_purchase',
        verbose_name='Temeljnica nabave',
    )
    activation_journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='fixed_assets_activation',
        verbose_name='Temeljnica aktivacije',
    )
    disposal_journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='fixed_assets_disposal',
        verbose_name='Temeljnica otpisa',
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Kreirano')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Ažurirano')

    class Meta:
        verbose_name = 'Osnovno sredstvo'
        verbose_name_plural = 'Osnovna sredstva'
        ordering = ['-purchase_date', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'vin'],
                condition=~models.Q(vin=''),
                name='unique_vin_per_tenant',
            ),
            models.UniqueConstraint(
                fields=['purchase_journal_entry'],
                condition=models.Q(purchase_journal_entry__isnull=False),
                name='unique_purchase_journal_entry',
            ),
        ]

    def __str__(self):
        label = self.name or self.vin or self.inventory_number or f'OS #{self.pk}'
        return label

    @property
    def accumulated_depreciation(self) -> Decimal:
        total = (
            DepreciationSchedule.all_objects.filter(
                fixed_asset=self,
                journal_entry__status='posted',
            ).aggregate(total=models.Sum('amount'))['total']
        )
        return total or Decimal('0.00')

    @property
    def current_book_value(self) -> Decimal:
        return self.acquisition_cost - self.accumulated_depreciation

    def clean(self):
        if self.origin == FixedAssetOrigin.PURCHASE and not self.purchase_journal_entry_id:
            raise ValidationError(
                {'purchase_journal_entry': 'Temeljnica nabave je obavezna za sredstva iz nabave.'},
            )
        if self.purchase_journal_entry_id:
            if self.purchase_journal_entry.tenant_id != self.tenant_id:
                raise ValidationError({'purchase_journal_entry': 'Temeljnica mora pripadati istom tenantu.'})
            if (
                self.origin == FixedAssetOrigin.PURCHASE
                and self.purchase_journal_entry.status != 'posted'
            ):
                raise ValidationError(
                    {'purchase_journal_entry': 'Temeljnica nabave mora biti knjižena.'},
                )
        account_fields = (
            'construction_account',
            'asset_account',
            'accumulated_depreciation_account',
            'depreciation_expense_account',
        )
        for field in account_fields:
            account = getattr(self, field, None)
            if account and account.tenant_id != self.tenant_id:
                raise ValidationError({field: 'Konto mora pripadati istom tenantu.'})
        if self.residual_value >= self.acquisition_cost:
            raise ValidationError(
                {'residual_value': 'Ostatak vrijednosti mora biti manji od nabavne vrijednosti.'},
            )
        if self.useful_life_months is not None and self.useful_life_months < 1:
            raise ValidationError(
                {'useful_life_months': 'Trajnost mora biti najmanje 1 mjesec.'},
            )


class DepreciationSchedule(TenantMixin, models.Model):
    """Planirana / knjižena mjesečna amortizacija po imovini."""

    fixed_asset = models.ForeignKey(
        FixedAsset,
        on_delete=models.CASCADE,
        related_name='depreciation_schedules',
        verbose_name='Osnovno sredstvo',
    )
    year = models.PositiveSmallIntegerField(verbose_name='Godina')
    month = models.PositiveSmallIntegerField(verbose_name='Mjesec')
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Iznos amortizacije',
    )
    journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='depreciation_schedules',
        verbose_name='Temeljnica amortizacije',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Kreirano')

    class Meta:
        verbose_name = 'Plan amortizacije'
        verbose_name_plural = 'Planovi amortizacije'
        ordering = ['year', 'month']
        constraints = [
            models.UniqueConstraint(
                fields=['fixed_asset', 'year', 'month'],
                name='unique_depreciation_per_asset_period',
            ),
        ]

    def __str__(self):
        return f'{self.fixed_asset} — {self.month:02d}/{self.year}: {self.amount}'


class SubledgerItem(TenantMixin, models.Model):
    """Otvorena stavka saldakonta (potraživanje / obveza prema partneru)."""

    DIRECTION_CHOICES = [
        ('receivable', 'Potraživanje'),
        ('payable', 'Obveza'),
    ]
    STATUS_CHOICES = [
        ('open', 'Otvoreno'),
        ('partial', 'Djelomično'),
        ('closed', 'Zatvoreno'),
        ('cancelled', 'Poništeno'),
    ]

    partner = models.ForeignKey(
        'partners.Partner',
        on_delete=models.CASCADE,
        related_name='subledger_items',
        verbose_name='Partner',
    )
    direction = models.CharField(max_length=12, choices=DIRECTION_CHOICES, verbose_name='Smjer')
    source_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name='subledger_source_items',
        verbose_name='Tip izvora',
    )
    source_object_id = models.PositiveIntegerField(verbose_name='ID izvora')
    source = GenericForeignKey('source_content_type', 'source_object_id')
    journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.CASCADE,
        related_name='subledger_items',
        verbose_name='Izvorna temeljnica',
    )
    original_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Izvorni iznos',
    )
    open_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Otvoreni iznos',
    )
    due_date = models.DateField(verbose_name='Datum dospijeća')
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='open',
        verbose_name='Status',
    )
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name='Datum zatvaranja')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Datum stvaranja')

    class Meta:
        verbose_name = 'Stavka saldakonta'
        verbose_name_plural = 'Stavke saldakonta'
        ordering = ['-due_date', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'source_content_type', 'source_object_id'],
                condition=~models.Q(status='cancelled'),
                name='unique_active_subledger_source_per_tenant',
            ),
        ]
        indexes = [
            models.Index(fields=['tenant', 'partner', 'status']),
            models.Index(fields=['tenant', 'direction', 'due_date']),
        ]

    def __str__(self):
        return f'{self.get_direction_display()} {self.partner} — {self.open_amount}'


class SubledgerAllocation(TenantMixin, models.Model):
    """Alokacija uplate/isplate na otvorenu stavku saldakonta."""

    subledger_item = models.ForeignKey(
        SubledgerItem,
        on_delete=models.CASCADE,
        related_name='allocations',
        verbose_name='Stavka saldakonta',
    )
    journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.CASCADE,
        related_name='subledger_allocations',
        verbose_name='Temeljnica uplate',
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Iznos alokacije',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Datum stvaranja')

    class Meta:
        verbose_name = 'Alokacija saldakonta'
        verbose_name_plural = 'Alokacije saldakonta'
        constraints = [
            models.UniqueConstraint(
                fields=['journal_entry'],
                name='unique_subledger_allocation_per_journal_entry',
            ),
        ]

    def __str__(self):
        return f'{self.amount} → {self.subledger_item_id}'
