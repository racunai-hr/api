import hashlib
import uuid

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from tenants.mixins import TenantMixin


class FiscalTenantConfig(TenantMixin, models.Model):
    CIS_ENV_DEMO = 'demo'
    CIS_ENV_PROD = 'prod'
    CIS_ENV_PTS = 'pts'
    CIS_ENV_CHOICES = [
        (CIS_ENV_DEMO, 'CIS demo (test)'),
        (CIS_ENV_PROD, 'CIS produkcija'),
        (CIS_ENV_PTS, 'CIS PTS (8511)'),
    ]

    oib = models.CharField(max_length=11, verbose_name='OIB obveznika')
    p12_path = models.CharField(
        max_length=500,
        blank=True,
        verbose_name='Putanja .p12',
        help_text='Prazno = koristi FISCAL_CERT_P12_PATH iz okoline.',
    )
    p12_password = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Lozinka .p12',
        help_text='Prazno = koristi FISCAL_CERT_P12_PASSWORD iz okoline.',
    )
    cis_env = models.CharField(
        max_length=10,
        choices=CIS_ENV_CHOICES,
        default=CIS_ENV_DEMO,
        verbose_name='CIS okolina',
    )
    certificate_label = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Oznaka certifikata',
        help_text='npr. FISKAL 2',
    )
    is_active = models.BooleanField(default=True, verbose_name='Aktivna fiskalizacija')
    last_ping_at = models.DateTimeField(null=True, blank=True, verbose_name='Zadnji ping')
    last_submission_at = models.DateTimeField(null=True, blank=True, verbose_name='Zadnja fiskalizacija')

    class Meta:
        verbose_name = 'Fiskalna konfiguracija'
        verbose_name_plural = 'Fiskalne konfiguracije'
        constraints = [
            models.UniqueConstraint(fields=['tenant'], name='unique_fiscal_config_per_tenant'),
        ]

    def __str__(self):
        return f'Fiskalizacija — {self.tenant.name} ({self.oib})'


class FiscalSubmissionLog(TenantMixin, models.Model):
    STATUS_PENDING = 'pending'
    STATUS_SUCCESS = 'success'
    STATUS_REJECTED = 'rejected'
    STATUS_ERROR = 'error'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'U tijeku'),
        (STATUS_SUCCESS, 'Uspješno'),
        (STATUS_REJECTED, 'Odbijeno (CIS)'),
        (STATUS_ERROR, 'Greška'),
    ]

    OPERATION_EVIDENTIRAJ = 'evidentiraj_eracun'
    OPERATION_CHOICES = [
        (OPERATION_EVIDENTIRAJ, 'Evidentiraj eRačun'),
    ]

    operation = models.CharField(max_length=40, choices=OPERATION_CHOICES)
    request_id = models.CharField(max_length=64, default=uuid.uuid4, unique=True)
    xml_hash = models.CharField(max_length=64, blank=True)
    request_xml = models.TextField(blank=True)
    response_xml = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    cis_request_id = models.CharField(max_length=64, blank=True, verbose_name='CIS idZahtjeva')
    jir = models.CharField(max_length=64, blank=True, verbose_name='JIR')
    error_code = models.CharField(max_length=20, blank=True)
    error_message = models.TextField(blank=True)
    invoice = models.ForeignKey(
        'invoices.Invoice',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='fiscal_submissions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Fiskalni log slanja'
        verbose_name_plural = 'Fiskalni logovi slanja'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.operation} — {self.status} ({self.created_at:%Y-%m-%d %H:%M})'

    @staticmethod
    def hash_xml(xml_text: str) -> str:
        return hashlib.sha256(xml_text.encode('utf-8')).hexdigest()


class DirectTenantConfig(TenantMixin, models.Model):
    oib = models.CharField(max_length=11, verbose_name='OIB pristupne točke')
    ap_party_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Domibus AP PartyId',
        help_text='Prazno = koristi DOMIBUS_AP_PARTY_ID iz okoline.',
    )
    domibus_ws_url = models.CharField(
        max_length=500,
        blank=True,
        verbose_name='Domibus WS URL',
        help_text='Prazno = koristi DOMIBUS_WS_URL iz okoline.',
    )
    mps_service_url = models.CharField(
        max_length=500,
        blank=True,
        verbose_name='MPS servis URL',
        help_text='Prazno = koristi MPS_SERVICE_URL iz okoline.',
    )
    is_active = models.BooleanField(default=True, verbose_name='Aktivna DIRECT integracija')
    last_outbound_at = models.DateTimeField(null=True, blank=True, verbose_name='Zadnji izlazni AS4')
    last_inbound_at = models.DateTimeField(null=True, blank=True, verbose_name='Zadnji ulazni AS4')

    class Meta:
        verbose_name = 'DIRECT eRačun konfiguracija'
        verbose_name_plural = 'DIRECT eRačun konfiguracije'
        constraints = [
            models.UniqueConstraint(fields=['tenant'], name='unique_direct_config_per_tenant'),
        ]

    def __str__(self):
        return f'DIRECT — {self.tenant.name} ({self.oib})'


class As4DocumentLink(TenantMixin, models.Model):
    DIRECTION_INBOUND = 'inbound'
    DIRECTION_OUTBOUND = 'outbound'
    DIRECTION_CHOICES = [
        (DIRECTION_INBOUND, 'Ulazni'),
        (DIRECTION_OUTBOUND, 'Izlazni'),
    ]

    STATUS_SENT = 'sent'
    STATUS_DELIVERED = 'delivered'
    STATUS_FAILED = 'failed'
    STATUS_ACCEPTED = 'accepted'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_SENT, 'Poslan'),
        (STATUS_DELIVERED, 'Dostavljen'),
        (STATUS_FAILED, 'Neuspješan'),
        (STATUS_ACCEPTED, 'Prihvaćen'),
        (STATUS_REJECTED, 'Odbijen'),
    ]

    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    message_id = models.CharField(max_length=200, db_index=True)
    ref_message_id = models.CharField(max_length=200, blank=True)
    as4_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SENT)
    ubl_xml = models.TextField(blank=True)
    recipient_oib = models.CharField(max_length=11, blank=True)
    supplier_oib = models.CharField(max_length=11, blank=True)
    conversation_id = models.CharField(max_length=200, blank=True)
    from_party_id = models.CharField(max_length=200, blank=True)

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')

    last_synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'AS4 dokument'
        verbose_name_plural = 'AS4 dokumenti'
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'direction', 'message_id'],
                name='unique_as4_message_per_tenant_direction',
            ),
        ]

    def __str__(self):
        return f'{self.get_direction_display()} {self.message_id}'
