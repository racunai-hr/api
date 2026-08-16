import re

from django.db import models

from tenants.mixins import TenantMixin


class TaxOffice(models.Model):
    """Globalni šifrarnik poreznih ureda (PU)."""

    code = models.CharField(max_length=4, unique=True, verbose_name="Šifra")
    name = models.CharField(max_length=200, verbose_name="Naziv")
    city = models.CharField(max_length=100, verbose_name="Grad")

    class Meta:
        verbose_name = "Porezni ured"
        verbose_name_plural = "Porezni uredi"
        ordering = ['code']

    def __str__(self):
        return f'{self.code} — {self.name}, {self.city}'


class TaxRate(TenantMixin, models.Model):
    """Porezne stope PDV-a"""
    name = models.CharField(max_length=100, verbose_name="Naziv")
    rate = models.DecimalField(max_digits=5, decimal_places=2, verbose_name="Stopa (%)")
    description = models.TextField(blank=True, verbose_name="Opis")
    is_default = models.BooleanField(default=False, verbose_name="Zadana stopa")
    is_active = models.BooleanField(default=True, verbose_name="Aktivna")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Kreirano")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Ažurirano")
    
    class Meta:
        verbose_name = "Porezna stopa"
        verbose_name_plural = "Porezne stope"
        ordering = ['rate']
    
    def __str__(self):
        return f"{self.name} ({self.rate}%)"
    
    def save(self, *args, **kwargs):
        if self.is_default:
            TaxRate.all_objects.filter(tenant=self.tenant, is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class CompanySettings(TenantMixin, models.Model):
    """Postavke tvrtke po tenantu"""
    company_name = models.CharField(max_length=200, verbose_name="Naziv tvrtke")
    company_address = models.TextField(verbose_name="Adresa tvrtke (deprecated)")
    street = models.CharField(max_length=200, blank=True, verbose_name="Ulica")
    house_number = models.CharField(max_length=20, blank=True, verbose_name="Kućni broj")
    postal_code = models.CharField(max_length=10, blank=True, verbose_name="Poštanski broj")
    city = models.CharField(max_length=100, blank=True, verbose_name="Grad")
    country = models.CharField(max_length=2, default='HR', verbose_name="Država")
    company_phone = models.CharField(max_length=50, verbose_name="Telefon tvrtke")
    company_email = models.EmailField(verbose_name="Email tvrtke")
    company_website = models.URLField(blank=True, verbose_name="Web stranica")
    company_logo = models.ImageField(upload_to='company/', blank=True, null=True, verbose_name="Logo tvrtke")
    
    vat_number = models.CharField(max_length=20, blank=True, verbose_name="OIB/VAT broj")
    tax_number = models.CharField(max_length=20, blank=True, verbose_name="Porezni broj")
    registration_number = models.CharField(max_length=20, blank=True, verbose_name="Matični broj")
    tax_office = models.ForeignKey(
        TaxOffice,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='companies',
        verbose_name="Porezni ured",
    )
    
    default_currency = models.CharField(max_length=3, default='EUR', verbose_name="Zadana valuta")
    default_tax_rate = models.ForeignKey(TaxRate, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Zadana porezna stopa")
    
    timezone = models.CharField(max_length=50, default='Europe/Zagreb', verbose_name="Vremenska zona")
    date_format = models.CharField(max_length=20, default='d.m.Y', verbose_name="Format datuma")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Kreirano")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Ažurirano")
    
    class Meta:
        verbose_name = "Postavke tvrtke"
        verbose_name_plural = "Postavke tvrtke"

    def __str__(self):
        return self.company_name

    @property
    def formatted_street(self) -> str:
        if self.street:
            if self.house_number:
                return f'{self.street} {self.house_number}'
            return self.street
        lines = (self.company_address or '').split('\n')
        return lines[0].strip() if lines else ''

    @property
    def formatted_city_line(self) -> str:
        if self.postal_code and self.city:
            return f'{self.postal_code} {self.city}'
        if self.city:
            return self.city
        lines = (self.company_address or '').split('\n')
        return lines[1].strip() if len(lines) > 1 else ''

    @classmethod
    def parse_legacy_address(cls, address: str | None) -> dict[str, str]:
        """Parsira legacy company_address (ulica broj\\npoštanski grad)."""
        lines = [line.strip() for line in (address or '').split('\n') if line.strip()]
        street = ''
        house_number = ''
        postal_code = ''
        city = ''

        if lines:
            match = re.match(r'^(.+?)\s+(\d+[A-Za-z]?)$', lines[0])
            if match:
                street, house_number = match.group(1), match.group(2)
            else:
                street = lines[0]

        if len(lines) > 1:
            match = re.match(r'^(\d{5})\s+(.+)$', lines[1])
            if match:
                postal_code, city = match.group(1), match.group(2)
            else:
                city = lines[1]

        return {
            'street': street,
            'house_number': house_number,
            'postal_code': postal_code,
            'city': city,
            'country': 'HR',
        }


class ResponsiblePerson(models.Model):
    """Odgovorne osobe tvrtke"""
    company_settings = models.ForeignKey(
        CompanySettings, 
        on_delete=models.CASCADE, 
        related_name='responsible_persons',
        verbose_name="Postavke tvrtke"
    )
    
    TITLE_CHOICES = [
        ('director', 'Direktor'),
        ('ceo', 'Glavni izvršni direktor (CEO)'),
        ('cfo', 'Financijski direktor (CFO)'),
        ('coo', 'Operativni direktor (COO)'),
        ('deputy_director', 'Zamjenik direktora'),
        ('prokurist', 'Prokurist'),
        ('manager', 'Manager'),
        ('department_head', 'Voditelj odjela'),
        ('accountant', 'Računovođa'),
        ('secretary', 'Tajnik'),
        ('legal_representative', 'Zakonski zastupnik'),
        ('authorized_person', 'Ovlaštena osoba'),
        ('other', 'Ostalo'),
    ]
    
    title = models.CharField(
        max_length=50, 
        choices=TITLE_CHOICES,
        verbose_name="Titula/Funkcija"
    )
    first_name = models.CharField(max_length=100, verbose_name="Ime")
    last_name = models.CharField(max_length=100, verbose_name="Prezime")
    email = models.EmailField(blank=True, verbose_name="Email")
    phone = models.CharField(max_length=50, blank=True, verbose_name="Telefon")
    is_primary = models.BooleanField(default=False, verbose_name="Glavna kontakt osoba")
    is_active = models.BooleanField(default=True, verbose_name="Aktivna")
    notes = models.TextField(blank=True, verbose_name="Napomene")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Kreirano")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Ažurirano")
    
    class Meta:
        verbose_name = "Odgovorna osoba"
        verbose_name_plural = "Odgovorne osobe"
        ordering = ['last_name', 'first_name']
        indexes = [
            models.Index(fields=['company_settings', 'is_active']),
            models.Index(fields=['is_primary']),
        ]
    
    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.get_title_display()})"
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"
    
    def save(self, *args, **kwargs):
        if self.is_primary:
            ResponsiblePerson.objects.filter(
                company_settings=self.company_settings,
                is_primary=True
            ).exclude(pk=self.pk).update(is_primary=False)
        super().save(*args, **kwargs)


class SystemParameter(TenantMixin, models.Model):
    """Sistemski parametri (key-value store) po tenantu"""
    key = models.CharField(max_length=100, verbose_name="Ključ")
    value = models.TextField(verbose_name="Vrijednost")
    description = models.TextField(blank=True, verbose_name="Opis")
    data_type = models.CharField(max_length=20, choices=[
        ('string', 'Tekst'),
        ('integer', 'Broj'),
        ('float', 'Decimalni broj'),
        ('boolean', 'Da/Ne'),
        ('json', 'JSON'),
    ], default='string', verbose_name="Tip podatka")
    
    def __str__(self):
        return f"{self.key}: {self.value}"
        
    class Meta:
        verbose_name = "Sistemski parametar"
        verbose_name_plural = "Sistemski parametri"
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'key'], name='unique_system_param_per_tenant'),
        ]
