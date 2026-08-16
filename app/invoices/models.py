from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from decimal import Decimal
from django.utils import timezone

from accounting.services.tax_forms.pdv.supply_procedure import VatSupplyProcedure
from tenants.mixins import TenantMixin


class Invoice(TenantMixin, models.Model):
    STATUS_CHOICES = [
        ('draft', 'Nacrt'),
        ('sent', 'Poslan'),
        ('paid', 'Plaćen'),
        ('overdue', 'Dospijeće prošlo'),
        ('cancelled', 'Otkazan'),
    ]

    invoice_number = models.CharField(
        max_length=50, verbose_name="Broj računa", blank=True
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default='draft', verbose_name="Status"
    )

    company_to = models.ForeignKey(
        'partners.Partner',
        on_delete=models.CASCADE,
        related_name='invoices_received',
        verbose_name="Primatelj (partner)"
    )

    responsible_person = models.ForeignKey(
        'settings.ResponsiblePerson',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={'is_active': True},
        verbose_name="Odgovorna osoba",
        help_text="Odgovorna osoba za ovaj račun"
    )

    issue_date = models.DateField(verbose_name="Datum izdavanja")
    due_date = models.DateField(verbose_name="Datum dospijeća")
    service_date = models.DateField(blank=True, null=True, verbose_name="Datum usluge")

    subtotal = models.DecimalField(
        max_digits=15, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        default=0, verbose_name="Osnovica"
    )
    tax_amount = models.DecimalField(
        max_digits=15, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        default=0, verbose_name="Iznos PDV-a"
    )
    total_amount = models.DecimalField(
        max_digits=15, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        default=0, verbose_name="Ukupan iznos"
    )

    description = models.TextField(blank=True, verbose_name="Opis")
    notes = models.TextField(blank=True, verbose_name="Napomene")

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Kreirao")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum stvaranja")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Datum ažuriranja")

    class Meta:
        verbose_name = "Račun"
        verbose_name_plural = "Računi"
        ordering = ['-issue_date', '-invoice_number']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'invoice_number'],
                name='unique_invoice_number_per_tenant',
            ),
        ]

    def __str__(self):
        number = self.invoice_number or '(nacrt)'
        return f"{number} - {self.total_amount} EUR"

    def assign_invoice_number(self):
        if self.invoice_number:
            return self.invoice_number
        year = (self.issue_date or timezone.now().date()).year
        prefix = f'{year}-'
        last = (
            Invoice.all_objects.filter(tenant=self.tenant, invoice_number__startswith=prefix)
            .order_by('-invoice_number')
            .values_list('invoice_number', flat=True)
            .first()
        )
        if last and last[len(prefix):].isdigit():
            next_num = int(last[len(prefix):]) + 1
        else:
            next_num = 1
        self.invoice_number = f'{prefix}{next_num:04d}'
        return self.invoice_number

    def save(self, *args, **kwargs):
        if not self.invoice_number and self.status != 'draft':
            self.assign_invoice_number()
        super().save(*args, **kwargs)

    def recalculate_totals(self):
        items = self.items.all()
        subtotal = sum((item.unit_price or 0) * (item.quantity or 0) for item in items)
        tax = sum(((item.unit_price or 0) * (item.quantity or 0)) * (item.tax_rate or 0) / 100 for item in items)
        total = subtotal + tax

        self.subtotal = subtotal
        self.tax_amount = tax
        self.total_amount = total
        super().save(update_fields=['subtotal', 'tax_amount', 'total_amount'])


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE,
        related_name='items', verbose_name="Račun"
    )
    item_name = models.CharField(max_length=200, verbose_name="Naziv stavke")
    description = models.TextField(blank=True, verbose_name="Opis stavke")
    quantity = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Količina"
    )
    unit_price = models.DecimalField(
        max_digits=15, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name="Jedinična cijena"
    )
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=25.00,
        verbose_name="Stopa PDV-a (%)"
    )
    vat_procedure = models.CharField(
        max_length=20,
        choices=VatSupplyProcedure.choices,
        default=VatSupplyProcedure.STANDARD,
        verbose_name='PDV postupak',
        help_text='OSS/IOSS e-trgovina EU — mapira na PDV boxove 204/214/215',
    )
    line_total = models.DecimalField(
        max_digits=15, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        default=0, verbose_name="Ukupno"
    )

    class Meta:
        verbose_name = "Stavka računa"
        verbose_name_plural = "Stavke računa"

    def __str__(self):
        return f"{self.item_name} - {self.line_total} EUR"

    def save(self, *args, **kwargs):
        self.line_total = (self.quantity or 0) * (self.unit_price or 0)
        super().save(*args, **kwargs)
        self.invoice.recalculate_totals()

    def delete(self, *args, **kwargs):
        invoice = self.invoice
        super().delete(*args, **kwargs)
        invoice.recalculate_totals()
