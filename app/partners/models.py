# ================================
# partners/models.py
# ================================

from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from decimal import Decimal

from tenants.mixins import TenantMixin


class PartnerType(TenantMixin, models.Model):
    TYPE_CHOICES = [
        ('customer', 'Kupac'),
        ('supplier', 'Dobavljač'),
        ('both', 'Kupac i dobavljač'),
        ('other', 'Ostalo'),
    ]
    
    name = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name="Tip partnera")
    description = models.TextField(blank=True, verbose_name="Opis")
    is_active = models.BooleanField(default=True, verbose_name="Aktivan")

    class Meta:
        verbose_name = "Tip partnera"
        verbose_name_plural = "Tipovi partnera"
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'name'], name='unique_partner_type_per_tenant'),
        ]

    def __str__(self):
        return self.get_name_display()


class Partner(TenantMixin, models.Model):
    PARTNER_TYPES = [
        ('customer', 'Kupac'),
        ('supplier', 'Dobavljač'),
        ('both', 'Kupac i dobavljač'),
        ('other', 'Ostalo'),
    ]
    
    STATUS_CHOICES = [
        ('active', 'Aktivan'),
        ('inactive', 'Neaktivan'),
        ('blocked', 'Blokiran'),
        ('prospect', 'Potencijalni'),
    ]

    partner_code = models.CharField(
        max_length=20,
        verbose_name="Šifra partnera",
        blank=True
    )
    name = models.CharField(max_length=200, verbose_name="Naziv partnera")
    short_name = models.CharField(max_length=50, blank=True, verbose_name="Kratki naziv")
    partner_type = models.CharField(max_length=20, choices=PARTNER_TYPES, verbose_name="Tip partnera")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active', verbose_name="Status")
    
    tax_number = models.CharField(max_length=20, verbose_name="OIB")
    vat_number = models.CharField(max_length=30, blank=True, verbose_name="PDV broj")
    registration_number = models.CharField(max_length=30, blank=True, verbose_name="Matični broj")
    
    address = models.TextField(verbose_name="Adresa")
    city = models.CharField(max_length=100, verbose_name="Grad")
    postal_code = models.CharField(max_length=20, verbose_name="Poštanski broj")
    country = models.CharField(max_length=100, default="Hrvatska", verbose_name="Država")
    
    email = models.EmailField(blank=True, verbose_name="E-mail")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Telefon")
    mobile = models.CharField(max_length=20, blank=True, verbose_name="Mobitel")
    fax = models.CharField(max_length=20, blank=True, verbose_name="Fax")
    website = models.URLField(blank=True, verbose_name="Web stranica")
    
    payment_terms = models.IntegerField(default=30, verbose_name="Uvjeti plaćanja (dani)")
    credit_limit = models.DecimalField(
        max_digits=15, decimal_places=2, default=0, 
        validators=[MinValueValidator(Decimal('0.00'))], 
        verbose_name="Kreditni limit"
    )
    discount_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, 
        verbose_name="Popust (%)"
    )
    
    notes = models.TextField(blank=True, verbose_name="Napomene")
    internal_notes = models.TextField(blank=True, verbose_name="Interne napomene")
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Kreirao")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum stvaranja")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Datum ažuriranja")

    class Meta:
        verbose_name = "Partner"
        verbose_name_plural = "Partneri"
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'partner_code'], name='unique_partner_code_per_tenant'),
            models.UniqueConstraint(fields=['tenant', 'tax_number'], name='unique_partner_tax_per_tenant'),
        ]

    def __str__(self):
        return f"{self.partner_code} - {self.name}"

    @property
    def is_customer(self):
        return self.partner_type in ['customer', 'both']

    @property
    def is_supplier(self):
        return self.partner_type in ['supplier', 'both']

    def save(self, *args, **kwargs):
        if not self.partner_code:
            last_partner = Partner.all_objects.filter(tenant=self.tenant).order_by('-id').first()
            if last_partner and last_partner.partner_code.isdigit():
                new_number = int(last_partner.partner_code) + 1
            else:
                new_number = 1
            self.partner_code = f"{new_number:05d}"
        super().save(*args, **kwargs)


class PartnerContact(models.Model):
    CONTACT_TYPES = [
        ('general', 'Općenito'),
        ('sales', 'Prodaja'),
        ('purchasing', 'Nabava'),
        ('accounting', 'Računovodstvo'),
        ('technical', 'Tehnička podrška'),
        ('management', 'Upravljanje'),
    ]

    partner = models.ForeignKey(
        Partner, on_delete=models.CASCADE, 
        related_name='contacts', verbose_name="Partner"
    )
    contact_type = models.CharField(
        max_length=15, choices=CONTACT_TYPES, 
        default='general', verbose_name="Tip kontakta"
    )
    
    first_name = models.CharField(max_length=50, verbose_name="Ime")
    last_name = models.CharField(max_length=50, verbose_name="Prezime")
    position = models.CharField(max_length=100, blank=True, verbose_name="Pozicija")
    department = models.CharField(max_length=100, blank=True, verbose_name="Odjel")
    
    email = models.EmailField(blank=True, verbose_name="E-mail")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Telefon")
    mobile = models.CharField(max_length=20, blank=True, verbose_name="Mobitel")
    
    notes = models.TextField(blank=True, verbose_name="Napomene")
    is_primary = models.BooleanField(default=False, verbose_name="Primarni kontakt")
    is_active = models.BooleanField(default=True, verbose_name="Aktivan")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum stvaranja")

    class Meta:
        verbose_name = "Kontakt partnera"
        verbose_name_plural = "Kontakti partnera"
        ordering = ['-is_primary', 'last_name', 'first_name']

    def __str__(self):
        return f"{self.first_name} {self.last_name} - {self.partner.name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class PartnerBankAccount(models.Model):
    partner = models.ForeignKey(
        Partner, on_delete=models.CASCADE, 
        related_name='bank_accounts', verbose_name="Partner"
    )
    bank_name = models.CharField(max_length=100, verbose_name="Naziv banke")
    bic = models.CharField(max_length=50, verbose_name="BIC/SWIFT kod")
    iban = models.CharField(max_length=34, verbose_name="IBAN")
    currency = models.CharField(max_length=3, default='EUR', verbose_name="Valuta")
    is_primary = models.BooleanField(default=False, verbose_name="Primarni račun")
    is_active = models.BooleanField(default=True, verbose_name="Aktivan")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Datum stvaranja")

    class Meta:
        verbose_name = "Bankovni račun partnera"
        verbose_name_plural = "Bankovni računi partnera"
        ordering = ['-is_primary', 'bank_name']

    def __str__(self):
        return f"{self.bank_name} - {self.iban}"
