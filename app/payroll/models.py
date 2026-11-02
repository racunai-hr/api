"""Modul za plaće — matični podaci zaposlenika, obračunski periodi i obračunske liste.

Reference:
    - Uzorak izlaznih obrazaca: "plaća obračun.pdf" (FINE STAR d.o.o., 2022.) —
      sadrži Knjiženje plače, Rekapitulaciju plače po datumu isplate,
      Rekapitulaciju obračunskih lista i Obračunsku listu.
    - Plan razvoja: .cursor/plans/modul_za_plaće_(payroll)_a7a0b274.plan.md

NAPOMENA — zakonski parametri (``PayrollTaxYear``, ``MunicipalityIncomeTaxRate``) su
platformski (nisu po tenantu), jer su propisani zakonom/odlukama JLS-ova i jednaki
za sve tenante. Vrijednosti za 2026. uneseni su prema javno dostupnim izvorima
(Porezna uprava, RRiF, racunovodstvo.hr) u studenom 2026. — PRIJE produkcijske
uporabe potrebna je provjera ovlaštenog računovođe/ovih brojki uz Narodne novine.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from tenants.mixins import TenantMixin


# ---------------------------------------------------------------------------
# Platformski (ne-tenant) zakonski referentni podaci
# ---------------------------------------------------------------------------


class PayrollTaxYear(models.Model):
    """Zakonski parametri obračuna plaće za jednu kalendarsku godinu.

    Nacionalni propis (Zakon o doprinosima, Zakon o porezu na dohodak, Uredba o
    minimalnoj plaći, Ovršni zakon) — isti za sve tenante, zato nije TenantMixin.
    """

    year = models.PositiveSmallIntegerField(unique=True, verbose_name="Godina")
    basic_personal_allowance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('600.00'),
        verbose_name="Osnovni osobni odbitak (mjesečno, EUR)",
        help_text="Porezna uprava: osnovica osobnog odbitka.",
    )
    mio1_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('15.00'),
        verbose_name="MIO I. stup — iz plaće (%)",
    )
    mio2_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('5.00'),
        verbose_name="MIO II. stup — iz plaće (%)",
    )
    mio1_only_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('20.00'),
        verbose_name="MIO samo I. stup (generacijska solidarnost) — iz plaće (%)",
    )
    zo_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('16.50'),
        verbose_name="Zdravstveno osiguranje — na plaću, trošak poslodavca (%)",
    )
    mio1_base_reduction_threshold = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('1300.00'),
        verbose_name="Prag umanjenja osnovice MIO I. stupa (EUR)",
        help_text="Osnovica MIO I umanjuje se za 0,5 × (prag − bruto) kada je bruto < prag.",
    )
    minimum_wage_gross = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('1050.00'),
        verbose_name="Zakonska minimalna bruto plaća (EUR, puno radno vrijeme)",
    )
    protected_account_reference_net = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('1434.00'),
        verbose_name="Referentna neto plaća za zaštićeni račun (DZS, prethodna god.)",
    )
    income_tax_annual_bracket_threshold = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('60000.00'),
        verbose_name="Godišnji prag niže/više stope poreza na dohodak (EUR)",
    )

    class Meta:
        verbose_name = "Zakonski parametri plaće (godina)"
        verbose_name_plural = "Zakonski parametri plaće (po godinama)"
        ordering = ['-year']

    def __str__(self) -> str:
        return f"Parametri obračuna plaće za {self.year}."


class PayrollLegalParameter(models.Model):
    """Jedan zakonski parametar s vlastitom osnovicom datuma važenja."""

    PAYMENT_DATE = 'PAYMENT_DATE'
    PAYROLL_MONTH = 'PAYROLL_MONTH'
    ENTITLEMENT_PERIOD = 'ENTITLEMENT_PERIOD'
    EVENT_DATE = 'EVENT_DATE'
    BASIS_CHOICES = [
        (PAYMENT_DATE, 'Datum isplate'),
        (PAYROLL_MONTH, 'Mjesec obračuna'),
        (ENTITLEMENT_PERIOD, 'Razdoblje prava'),
        (EVENT_DATE, 'Datum događaja'),
    ]

    code = models.CharField(max_length=64, verbose_name="Šifra parametra")
    value = models.DecimalField(max_digits=14, decimal_places=4, verbose_name="Vrijednost")
    valid_from = models.DateField(verbose_name="Vrijedi od")
    valid_to = models.DateField(null=True, blank=True, verbose_name="Vrijedi do")
    effective_basis = models.CharField(
        max_length=24, choices=BASIS_CHOICES, verbose_name="Osnovica datuma",
    )
    source_reference = models.CharField(max_length=120, blank=True, verbose_name="Izvor (NN)")
    verified_by = models.CharField(max_length=120, blank=True, verbose_name="Potvrdio")
    verified_at = models.DateTimeField(null=True, blank=True, verbose_name="Potvrđeno")

    class Meta:
        verbose_name = "Zakonski parametar plaće"
        verbose_name_plural = "Zakonski parametri plaće"
        ordering = ['code', '-valid_from']
        constraints = [
            models.UniqueConstraint(fields=['code', 'valid_from'], name='unique_payroll_legal_parameter_from'),
        ]

    def __str__(self) -> str:
        return f"{self.code} = {self.value} od {self.valid_from} ({self.effective_basis})"


class MunicipalityIncomeTaxRate(models.Model):
    """Stopa poreza na dohodak po gradu/općini prebivališta radnika (JLS odluka).

    Od 1.1.2024. nema prireza — JLS samostalno propisuju nižu i višu stopu poreza
    na dohodak unutar zakonskog raspona. Niža stopa se primjenjuje do godišnje
    porezne osnovice iz ``PayrollTaxYear.income_tax_annual_bracket_threshold``.
    """

    code = models.CharField(max_length=5, verbose_name="Šifra JLS")
    name = models.CharField(max_length=200, verbose_name="Naziv grada/općine")
    year = models.PositiveSmallIntegerField(verbose_name="Godina važenja")
    lower_rate = models.DecimalField(max_digits=5, decimal_places=2, verbose_name="Niža stopa (%)")
    higher_rate = models.DecimalField(max_digits=5, decimal_places=2, verbose_name="Viša stopa (%)")
    valid_from = models.DateField(null=True, blank=True, verbose_name="Vrijedi od")
    valid_to = models.DateField(null=True, blank=True, verbose_name="Vrijedi do")
    effective_basis = models.CharField(
        max_length=24, default=PayrollLegalParameter.PAYMENT_DATE, verbose_name="Osnovica datuma",
    )
    source_reference = models.CharField(max_length=120, blank=True, verbose_name="Izvor (NN)")

    class Meta:
        verbose_name = "Stopa poreza na dohodak JLS"
        verbose_name_plural = "Stope poreza na dohodak JLS"
        ordering = ['year', 'name']
        constraints = [
            models.UniqueConstraint(fields=['year', 'code'], name='unique_municipality_rate_per_year'),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.year}): {self.lower_rate}% / {self.higher_rate}%"


# ---------------------------------------------------------------------------
# Matični podaci (po tenantu)
# ---------------------------------------------------------------------------


class Employee(TenantMixin, models.Model):
    MIO_BOTH_PILLARS = 'both'
    MIO_FIRST_ONLY = 'first_only'
    MIO_PILLAR_CHOICES = [
        (MIO_BOTH_PILLARS, 'I. i II. stup'),
        (MIO_FIRST_ONLY, 'Samo I. stup (generacijska solidarnost)'),
    ]

    code = models.CharField(max_length=20, blank=True, verbose_name="Šifra radnika")
    first_name = models.CharField(max_length=100, verbose_name="Ime")
    last_name = models.CharField(max_length=100, verbose_name="Prezime")
    oib = models.CharField(max_length=11, verbose_name="OIB")
    date_of_birth = models.DateField(null=True, blank=True, verbose_name="Datum rođenja")
    street = models.CharField(max_length=200, blank=True, verbose_name="Ulica i kućni broj")
    city = models.CharField(max_length=100, blank=True, verbose_name="Mjesto prebivališta")
    municipality_code = models.CharField(
        max_length=5, blank=True, verbose_name="Šifra JLS prebivališta",
        help_text="Šifra grada/općine prebivališta — određuje stopu poreza na dohodak i JOPPD podatke.",
    )
    work_location = models.CharField(max_length=150, blank=True, verbose_name="Mjesto rada")
    iban = models.CharField(max_length=34, blank=True, verbose_name="IBAN")
    bank_name = models.CharField(max_length=150, blank=True, verbose_name="Naziv banke")
    mio_pillar = models.CharField(
        max_length=12, choices=MIO_PILLAR_CHOICES, default=MIO_BOTH_PILLARS,
        verbose_name="Mirovinski stup",
    )
    personal_allowance_coefficient = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal('0.0000'),
        verbose_name="Koeficijent uvećanja osobnog odbitka",
        help_text=(
            "Zbroj koeficijenata s porezne kartice za uzdržavane članove, djecu i "
            "invalidnost — BEZ osnovnog koeficijenta 1,0 (taj je uključen u "
            "basic_personal_allowance)."
        ),
    )
    is_active = models.BooleanField(default=True, verbose_name="Aktivan")
    notes = models.TextField(blank=True, verbose_name="Napomene")
    sex = models.CharField(max_length=16, blank=True, verbose_name="Spol")
    citizenship = models.CharField(max_length=64, blank=True, verbose_name="Državljanstvo")
    residence_street = models.CharField(max_length=200, blank=True, verbose_name="Ulica prebivališta (ako se razlikuje)")
    residence_city = models.CharField(max_length=100, blank=True, verbose_name="Mjesto prebivališta (ako se razlikuje)")
    residence_municipality_code = models.CharField(max_length=5, blank=True, verbose_name="JLS prebivališta (ako se razlikuje)")
    occupation = models.CharField(max_length=150, blank=True, verbose_name="Zanimanje")
    qualification = models.CharField(max_length=150, blank=True, verbose_name="Kvalifikacija")
    licence_note = models.CharField(max_length=255, blank=True, verbose_name="Dozvole / licence")
    employment_status = models.CharField(max_length=32, blank=True, verbose_name="Status radnika")
    work_permit_number = models.CharField(max_length=64, blank=True, verbose_name="Broj dozvole za rad")
    work_permit_valid_to = models.DateField(null=True, blank=True, verbose_name="Dozvola vrijedi do")
    hzzo_registered_at = models.DateField(null=True, blank=True, verbose_name="Prijava HZZO")
    hzmo_registered_at = models.DateField(null=True, blank=True, verbose_name="Prijava HZMO")
    insurance_changed_at = models.DateField(null=True, blank=True, verbose_name="Promjena osiguranja")
    deregistered_at = models.DateField(null=True, blank=True, verbose_name="Odjava osiguranja")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Kreiran")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Ažuriran")

    class Meta:
        verbose_name = "Zaposlenik"
        verbose_name_plural = "Zaposlenici"
        ordering = ['last_name', 'first_name']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'oib'], name='unique_employee_oib_per_tenant'),
        ]

    def __str__(self) -> str:
        return self.full_name

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class EmploymentContract(TenantMixin, models.Model):
    DEFINITE = 'definite'
    INDEFINITE = 'indefinite'
    CONTRACT_TYPE_CHOICES = [
        (DEFINITE, 'Na određeno vrijeme'),
        (INDEFINITE, 'Na neodređeno vrijeme'),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='contracts', verbose_name="Zaposlenik",
    )
    start_date = models.DateField(verbose_name="Datum početka rada")
    end_date = models.DateField(null=True, blank=True, verbose_name="Datum prestanka rada")
    contract_type = models.CharField(
        max_length=12, choices=CONTRACT_TYPE_CHOICES, default=INDEFINITE, verbose_name="Vrsta ugovora",
    )
    work_time_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('100.00'), verbose_name="Radno vrijeme (%)",
    )
    base_gross_salary = models.DecimalField(
        max_digits=15, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name="Osnovna (ugovorena) bruto plaća",
    )
    recipient_code = models.CharField(
        max_length=4, blank=True, default='0001', verbose_name="Oznaka stjecatelja (JOPPD, stranica A)",
    )
    income_type_code = models.CharField(
        max_length=4, blank=True, default='0001', verbose_name="Oznaka primitka (JOPPD, stranica A)",
    )
    is_active = models.BooleanField(default=True, verbose_name="Aktivan")
    signed_at = models.DateField(null=True, blank=True, verbose_name="Datum potpisa")
    weekly_hours = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Tjedni sati",
    )
    job_title = models.CharField(max_length=150, blank=True, verbose_name="Naziv radnog mjesta")
    probation_months = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Probni rok (mjeseci)")
    notice_rule = models.CharField(max_length=255, blank=True, verbose_name="Otkazni rok")
    annual_leave_rule = models.CharField(max_length=255, blank=True, verbose_name="Godišnji odmor")
    collective_agreement_ref = models.CharField(max_length=255, blank=True, verbose_name="Kolektivni ugovor / pravilnik")
    termination_reason = models.CharField(max_length=255, blank=True, verbose_name="Razlog prestanka")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Kreiran")

    class Meta:
        verbose_name = "Ugovor o radu"
        verbose_name_plural = "Ugovori o radu"
        ordering = ['-start_date']

    def __str__(self) -> str:
        return f"{self.employee} — {self.get_contract_type_display()} od {self.start_date}"


# ---------------------------------------------------------------------------
# Obračun
# ---------------------------------------------------------------------------


class PayrollPeriod(TenantMixin, models.Model):
    DRAFT = 'draft'
    CALCULATED = 'calculated'
    APPROVED = 'approved'
    POSTED = 'posted'
    CLOSED = 'closed'
    STATUS_CHOICES = [
        (DRAFT, 'Nacrt'),
        (CALCULATED, 'Obračunato'),
        (APPROVED, 'Odobreno'),
        (POSTED, 'Knjiženo'),
        (CLOSED, 'Zatvoreno'),
    ]

    year = models.PositiveSmallIntegerField(verbose_name="Godina")
    month = models.PositiveSmallIntegerField(verbose_name="Mjesec")
    calculation_date = models.DateField(verbose_name="Datum obračuna")
    payment_date = models.DateField(verbose_name="Datum isplate")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT, verbose_name="Status")
    journal_entry = models.ForeignKey(
        'accounting.JournalEntry',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='payroll_periods',
        verbose_name="Temeljnica (knjiženje plaće)",
    )
    posted_at = models.DateTimeField(null=True, blank=True, verbose_name="Datum knjiženja")
    reversal_entry = models.ForeignKey(
        'accounting.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='payroll_period_reversals', verbose_name="Storno temeljnica",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Kreiran")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Ažuriran")

    class Meta:
        verbose_name = "Obračunski period plaće"
        verbose_name_plural = "Obračunski periodi plaće"
        ordering = ['-year', '-month']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'year', 'month'], name='unique_payroll_period_per_tenant'),
        ]

    def __str__(self) -> str:
        return f"Plaća {self.month:02d}/{self.year}"


class Payslip(TenantMixin, models.Model):
    DRAFT = 'draft'
    CALCULATED = 'calculated'
    POSTED = 'posted'
    STATUS_CHOICES = [
        (DRAFT, 'Nacrt'),
        (CALCULATED, 'Obračunato'),
        (POSTED, 'Knjiženo'),
    ]

    BANK_TRANSFER = 'bank_transfer'
    CASH = 'cash'
    PAYMENT_METHOD_CHOICES = [
        (BANK_TRANSFER, 'Na tekući račun'),
        (CASH, 'U gotovini'),
    ]

    period = models.ForeignKey(PayrollPeriod, on_delete=models.PROTECT, related_name='payslips', verbose_name="Period")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='payslips', verbose_name="Zaposlenik")
    contract = models.ForeignKey(
        EmploymentContract, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='payslips', verbose_name="Ugovor o radu",
    )

    date_from = models.DateField(verbose_name="Od dana")
    date_to = models.DateField(verbose_name="Do dana")
    is_first_month = models.BooleanField(default=False, verbose_name="Prvi mjesec")
    is_last_month = models.BooleanField(default=False, verbose_name="Zadnji mjesec")
    payment_method = models.CharField(
        max_length=20, choices=PAYMENT_METHOD_CHOICES, default=BANK_TRANSFER, verbose_name="Način isplate",
    )
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT, verbose_name="Status")

    # --- Obračun (popunjava services.calculate) -----------------------------
    gross_salary = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Bruto plaća")

    mio1_base = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Osnovica MIO I.")
    mio1_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Doprinos MIO I. (iz plaće)")
    mio2_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Doprinos MIO II. (iz plaće)")

    income_base = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Dohodak (bruto − doprinosi iz plaće)")
    personal_allowance_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Osobni odbitak")
    tax_base = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Porezna osnovica")
    income_tax_rate_used = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="Primijenjena stopa poreza (%)")
    income_tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Porez na dohodak")
    surtax_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Prirez (ukinut od 1.1.2024. — uvijek 0,00)",
    )
    net_salary = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Neto plaća")

    zo_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Doprinos ZO (na plaću, trošak poslodavca)")
    total_labor_cost = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Ukupni trošak rada")

    # --- Zaštićeni račun (Ovršni zakon, čl. 173.) ---------------------------
    protected_sick_leave_hzzo = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Zaštićeno bolovanje HZZO")
    protected_sick_leave_42d = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Zaštićeno bolovanje do 42. dana")
    protected_adjustments = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Zaštićeni dodaci")
    protected_salary_part = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Zaštićeni dio plaće")
    protected_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Ukupan zaštićeni iznos")
    regular_account_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Redovni račun")

    payout_index = models.PositiveSmallIntegerField(
        default=1, verbose_name="Redni broj isplate u mjesecu",
        help_text="Više od 1 kad isti radnik ima više isplata u istom obračunskom mjesecu.",
    )
    amount_to_pay = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Iznos za isplatu",
    )
    non_taxable_total = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Neoporezivi primitci",
    )
    deductions_total = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Obustave",
    )
    employee_contributions_total = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Doprinosi iz plaće",
    )
    compliance_flags = models.JSONField(default=list, blank=True, verbose_name="Oznake usklađenosti")
    calculation_version = models.PositiveSmallIntegerField(default=1, verbose_name="Verzija obračuna")
    parameters_snapshot = models.JSONField(default=dict, blank=True, verbose_name="Snimka parametara")
    kind = models.CharField(
        max_length=16, default='regular', verbose_name="Vrsta liste",
        choices=[('regular', 'Redovna'), ('termination', 'Prestanak rada')],
    )
    payment_status = models.CharField(
        max_length=20, default='not_due', verbose_name="Status isplate",
        choices=[
            ('not_due', 'Nije dospjelo'),
            ('due', 'Dospjelo'),
            ('partially_paid', 'Djelomično plaćeno'),
            ('paid', 'Plaćeno'),
            ('unpaid', 'Neplaćeno'),
        ],
    )
    paid_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Plaćeno")
    supersedes = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.PROTECT, related_name='corrections',
        verbose_name="Zamjenjuje listu",
    )

    delivery_form = models.CharField(max_length=10, blank=True, default='IP1', verbose_name="Obrazac isplate")
    delivered_to_employee_at = models.DateField(null=True, blank=True, verbose_name="Datum predaje radniku")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Kreiran")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Ažuriran")

    class Meta:
        verbose_name = "Obračunska lista"
        verbose_name_plural = "Obračunske liste"
        ordering = ['period', 'employee']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'period', 'employee', 'payout_index'],
                name='unique_payslip_payout_per_period_employee',
            ),
        ]

    def __str__(self) -> str:
        return f"Obračunska lista — {self.employee} ({self.period})"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = Payslip.all_objects.filter(pk=self.pk).values_list('status', flat=True).first()
            if previous == self.POSTED:
                raise ValidationError('Knjižena obračunska lista ne može se mijenjati.')
        super().save(*args, **kwargs)


class PayslipEarningLine(TenantMixin, models.Model):
    """Radni sati/naknade (npr. redovni rad, bolovanje, godišnji odmor, blagdan)."""

    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name='earning_lines', verbose_name="Obračunska lista")
    name = models.CharField(max_length=150, verbose_name="Naziv naknade")
    hours = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0.00'), verbose_name="Sati")
    factor = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal('1.0000'), verbose_name="Faktor obračuna")
    gross_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Bruto iznos")
    note = models.CharField(max_length=255, blank=True, verbose_name="Napomena")
    work_time_type = models.ForeignKey(
        'WorkTimeType', null=True, blank=True, on_delete=models.PROTECT, related_name='earning_lines',
        verbose_name="Vrsta rada",
    )
    rate_pct = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'), verbose_name="Postotak uvećanja")
    base_hourly_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'), verbose_name="Satnica")
    source = models.CharField(
        max_length=16, default='manual', verbose_name="Izvor",
        choices=[('manual', 'Ručno'), ('worktime', 'Evidencija'), ('contract', 'Ugovor')],
    )
    is_taxable = models.BooleanField(default=True, verbose_name="Oporezivo")
    is_contributable = models.BooleanField(default=True, verbose_name="Ulazi u osnovicu doprinosa")

    class Meta:
        verbose_name = "Stavka radnih sati/naknade"
        verbose_name_plural = "Stavke radnih sati/naknada"
        ordering = ['id']

    def save(self, *args, **kwargs):
        _reject_posted_payslip_edit(self.payslip_id)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _reject_posted_payslip_edit(self.payslip_id)
        super().delete(*args, **kwargs)


class PayslipContributionLine(TenantMixin, models.Model):
    """Doprinosi (MIO iz plaće, ZO na plaću) po obračunskoj listi."""

    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name='contribution_lines', verbose_name="Obračunska lista")
    name = models.CharField(max_length=150, verbose_name="Naziv doprinosa")
    base_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Osnovica")
    rate_from_salary = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('0.000'), verbose_name="% iz plaće")
    amount_from_salary = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Doprinos iz plaće")
    rate_on_salary = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('0.000'), verbose_name="% na plaću")
    amount_on_salary = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Doprinos na plaću")

    class Meta:
        verbose_name = "Stavka doprinosa"
        verbose_name_plural = "Stavke doprinosa"
        ordering = ['id']

    def save(self, *args, **kwargs):
        _reject_posted_payslip_edit(self.payslip_id)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _reject_posted_payslip_edit(self.payslip_id)
        super().delete(*args, **kwargs)


class PayslipAdjustmentLine(TenantMixin, models.Model):
    """Dodaci/obustave (bonusi, neoporezive naknade, sudske obustave i sl.)."""

    TAXABLE_ADDITION = 'taxable_addition'
    NON_TAXABLE_RECEIPT = 'non_taxable_receipt'
    DEDUCTION = 'deduction'
    KIND_CHOICES = [
        (TAXABLE_ADDITION, 'Oporezivi dodatak'),
        (NON_TAXABLE_RECEIPT, 'Neoporezivi primitak'),
        (DEDUCTION, 'Obustava'),
    ]

    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name='adjustment_lines', verbose_name="Obračunska lista")
    name = models.CharField(max_length=150, verbose_name="Naziv dodatka/obustave")
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Iznos")
    is_taxable = models.BooleanField(default=True, verbose_name="Oporezivo")
    kind = models.CharField(
        max_length=24, choices=KIND_CHOICES, default=TAXABLE_ADDITION, verbose_name="Vrsta",
    )
    note = models.CharField(max_length=255, blank=True, verbose_name="Napomena / oznaka")
    creditor_iban = models.CharField(max_length=34, blank=True, verbose_name="IBAN vjerovnika")
    enforcement = models.ForeignKey(
        'EmployeeEnforcement', null=True, blank=True, on_delete=models.PROTECT, related_name='adjustment_lines',
        verbose_name="Ovrha",
    )

    class Meta:
        verbose_name = "Stavka dodatka/obustave"
        verbose_name_plural = "Stavke dodataka/obustava"
        ordering = ['id']

    def save(self, *args, **kwargs):
        _reject_posted_payslip_edit(self.payslip_id)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _reject_posted_payslip_edit(self.payslip_id)
        super().delete(*args, **kwargs)


class WorkTimeType(models.Model):
    WORK = 'work'
    ABSENCE = 'absence'
    ALLOWANCE = 'allowance'
    CATEGORY_CHOICES = [(WORK, 'Rad'), (ABSENCE, 'Odsutnost'), (ALLOWANCE, 'Naknada')]

    code = models.CharField(max_length=32, unique=True, verbose_name="Šifra")
    name = models.CharField(max_length=150, verbose_name="Naziv")
    category = models.CharField(max_length=16, choices=CATEGORY_CHOICES, default=WORK, verbose_name="Kategorija")
    payer = models.CharField(max_length=16, default='employer', verbose_name="Platitelj")
    counts_as_work_hours = models.BooleanField(default=True, verbose_name="Ulazi u sate rada")
    default_pct_increase = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'), verbose_name="Zadano uvećanje (%)")
    legal_min_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name="Zakonski minimum (%)")
    joppd_hours_bucket = models.CharField(max_length=32, blank=True, verbose_name="JOPPD grupa sati")
    valid_from = models.DateField(null=True, blank=True, verbose_name="Vrijedi od")
    valid_to = models.DateField(null=True, blank=True, verbose_name="Vrijedi do")
    effective_basis = models.CharField(max_length=24, default=PayrollLegalParameter.PAYROLL_MONTH, verbose_name="Osnovica datuma")

    class Meta:
        verbose_name = "Vrsta radnog vremena"
        verbose_name_plural = "Vrste radnog vremena"
        ordering = ['code']

    def __str__(self) -> str:
        return self.name


class WorkTimeMonth(TenantMixin, models.Model):
    OPEN = 'open'
    REVIEWED = 'reviewed'
    LOCKED = 'locked'
    REOPENED = 'reopened'
    STATUS_CHOICES = [
        (OPEN, 'Otvoreno'), (REVIEWED, 'Pregledano'), (LOCKED, 'Zaključano'), (REOPENED, 'Ponovno otvoreno'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='worktime_months', verbose_name="Zaposlenik")
    year = models.PositiveSmallIntegerField(verbose_name="Godina")
    month = models.PositiveSmallIntegerField(verbose_name="Mjesec")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=OPEN, verbose_name="Status")
    locked_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='locked_worktime_months',
    )
    locked_at = models.DateTimeField(null=True, blank=True, verbose_name="Zaključano")
    content_hash = models.CharField(max_length=64, blank=True, verbose_name="Hash sadržaja")

    class Meta:
        verbose_name = "Mjesec evidencije"
        verbose_name_plural = "Mjeseci evidencije"
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'employee', 'year', 'month'], name='unique_worktime_month'),
        ]


class WorkTimeEntry(TenantMixin, models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='worktime_entries', verbose_name="Zaposlenik")
    contract = models.ForeignKey(EmploymentContract, null=True, blank=True, on_delete=models.PROTECT, related_name='worktime_entries')
    date = models.DateField(verbose_name="Datum")
    work_time_type = models.ForeignKey(WorkTimeType, on_delete=models.PROTECT, related_name='entries', verbose_name="Vrsta")
    hours = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'), verbose_name="Sati")
    work_start = models.TimeField(null=True, blank=True, verbose_name="Od")
    work_end = models.TimeField(null=True, blank=True, verbose_name="Do")
    note = models.CharField(max_length=255, blank=True, verbose_name="Napomena")
    month_lock = models.ForeignKey(WorkTimeMonth, null=True, blank=True, on_delete=models.PROTECT, related_name='entries')
    created_by = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='worktime_entries')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Dnevni unos radnog vremena"
        verbose_name_plural = "Dnevni unosi radnog vremena"
        constraints = [
            models.UniqueConstraint(fields=['employee', 'date', 'work_time_type'], name='unique_worktime_entry'),
        ]


class WorkTimeRateOverride(TenantMixin, models.Model):
    contract = models.ForeignKey(EmploymentContract, null=True, blank=True, on_delete=models.CASCADE, related_name='rate_overrides')
    work_time_type = models.ForeignKey(WorkTimeType, on_delete=models.PROTECT, related_name='overrides')
    rate_pct = models.DecimalField(max_digits=6, decimal_places=2, verbose_name="Uvećanje (%)")
    valid_from = models.DateField(verbose_name="Vrijedi od")
    valid_to = models.DateField(null=True, blank=True, verbose_name="Vrijedi do")

    class Meta:
        verbose_name = "Ugovorno uvećanje"
        verbose_name_plural = "Ugovorna uvećanja"


class EmployeeEnforcement(TenantMixin, models.Model):
    ORDINARY = 'ordinary'
    MAINTENANCE = 'maintenance'
    SPECIAL = 'special'
    KIND_CHOICES = [(ORDINARY, 'Obična'), (MAINTENANCE, 'Uzdržavanje'), (SPECIAL, 'Posebna')]

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='enforcements')
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, verbose_name="Vrsta")
    priority = models.PositiveSmallIntegerField(default=100, verbose_name="Prioritet")
    creditor_name = models.CharField(max_length=200, verbose_name="Vjerovnik")
    creditor_iban = models.CharField(max_length=34, blank=True, verbose_name="IBAN")
    reference = models.CharField(max_length=64, blank=True, verbose_name="Poziv na broj")
    amount_total = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Ukupno")
    amount_remaining = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Preostalo")
    valid_from = models.DateField(verbose_name="Od")
    valid_to = models.DateField(null=True, blank=True, verbose_name="Do")

    class Meta:
        verbose_name = "Ovrha"
        verbose_name_plural = "Ovrhe"
        ordering = ['priority', 'id']


class PayrollPayment(TenantMixin, models.Model):
    NET = 'net'
    TAX = 'tax'
    MIO1 = 'mio1'
    MIO2 = 'mio2'
    ZO = 'zo'
    DEDUCTION = 'deduction'
    KIND_CHOICES = [
        (NET, 'Neto'), (TAX, 'Porez'), (MIO1, 'MIO I'), (MIO2, 'MIO II'), (ZO, 'ZO'), (DEDUCTION, 'Obustava'),
    ]

    payslip = models.ForeignKey(Payslip, on_delete=models.PROTECT, related_name='payments')
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_order = models.ForeignKey(
        'banking.PaymentOrder', null=True, blank=True, on_delete=models.SET_NULL, related_name='payroll_payments',
    )
    journal_entry = models.ForeignKey(
        'accounting.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL, related_name='payroll_payments',
    )

    class Meta:
        verbose_name = "Isplata plaće"
        verbose_name_plural = "Isplate plaće"


class AnnualLeaveEntitlement(TenantMixin, models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='leave_entitlements')
    year = models.PositiveSmallIntegerField()
    days_entitled = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    days_used = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    payout_base = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    payout_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'employee', 'year'], name='unique_leave_entitlement'),
        ]

    @property
    def days_unused(self) -> Decimal:
        return self.days_entitled - self.days_used


class PayrollDocument(TenantMixin, models.Model):
    IP1 = 'IP1'
    NP1 = 'NP1'
    IO1 = 'IO1'
    NO1 = 'NO1'
    KIND_CHOICES = [(IP1, 'IP1'), (NP1, 'NP1'), (IO1, 'IO1'), (NO1, 'NO1')]

    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    payslip = models.ForeignKey(Payslip, on_delete=models.PROTECT, related_name='documents')
    payload = models.JSONField(default=dict)
    content_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Isprava plaće"
        verbose_name_plural = "Isprave plaće"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Isprava plaće je nepromjenjiva.')
        super().save(*args, **kwargs)


class JoppdCode(models.Model):
    code_type = models.CharField(max_length=32, verbose_name="Vrsta šifre")
    code = models.CharField(max_length=8, verbose_name="Šifra")
    label = models.CharField(max_length=200, verbose_name="Naziv")
    valid_from = models.DateField(verbose_name="Vrijedi od")
    valid_to = models.DateField(null=True, blank=True, verbose_name="Vrijedi do")
    schema_version = models.CharField(max_length=32, default='v1-0-draft', verbose_name="Verzija sheme")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['code_type', 'code', 'valid_from'], name='unique_joppd_code'),
        ]


def _reject_posted_payslip_edit(payslip_id: int | None) -> None:
    if not payslip_id:
        return
    status = Payslip.all_objects.filter(pk=payslip_id).values_list('status', flat=True).first()
    if status == Payslip.POSTED:
        raise ValidationError('Stavka knjižene obračunske liste ne može se mijenjati.')
