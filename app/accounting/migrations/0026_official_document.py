from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import accounting.models


class Migration(migrations.Migration):

    dependencies = [
        ('partners', '0008_partner_country_code_not_null_uniques'),
        ('tenants', '0007_create_finestar_tenant'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounting', '0025_vehicle'),
    ]

    operations = [
        migrations.CreateModel(
            name='OfficialDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('tax_decision', 'Porezno rješenje'), ('other', 'Ostalo')], default='tax_decision', max_length=20, verbose_name='Vrsta')),
                ('document_number', models.CharField(max_length=100, verbose_name='Broj dokumenta')),
                ('reference', models.CharField(blank=True, max_length=100, verbose_name='Referenca')),
                ('issue_date', models.DateField(verbose_name='Datum izdavanja')),
                ('due_date', models.DateField(blank=True, null=True, verbose_name='Datum dospijeća')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=15, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))], verbose_name='Iznos')),
                ('currency', models.CharField(default='EUR', max_length=3, verbose_name='Valuta')),
                ('status', models.CharField(choices=[('draft', 'Nacrt'), ('registered', 'Registrirano'), ('cancelled', 'Otkazano')], default='draft', max_length=12, verbose_name='Status')),
                ('original_file', models.FileField(blank=True, upload_to=accounting.models.official_document_upload_to, verbose_name='Izvorni dokument')),
                ('original_filename', models.CharField(blank=True, max_length=255, verbose_name='Originalni naziv')),
                ('content_type', models.CharField(blank=True, max_length=100)),
                ('file_sha256', models.CharField(blank=True, db_index=True, max_length=64)),
                ('file_size', models.PositiveIntegerField(default=0)),
                ('notes', models.TextField(blank=True, verbose_name='Napomene')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Kreirano')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Ažurirano')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_official_documents', to=settings.AUTH_USER_MODEL, verbose_name='Kreirao')),
                ('issuer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='official_documents', to='partners.partner', verbose_name='Izdavatelj')),
                ('related_fixed_asset', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='official_documents', to='accounting.fixedasset', verbose_name='Povezana imovina')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'Službeni dokument',
                'verbose_name_plural': 'Službeni dokumenti',
                'ordering': ['-issue_date', '-id'],
            },
        ),
        migrations.AddConstraint(
            model_name='officialdocument',
            constraint=models.UniqueConstraint(fields=('tenant', 'issuer', 'document_number'), name='unique_official_document_per_issuer'),
        ),
    ]
