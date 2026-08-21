"""ADR-0023 step 2: country_code NOT NULL + conditional tax/vat uniques."""

from django.db import migrations, models
from django.db.models import Q


def assert_country_code_ready(apps, schema_editor):
    Partner = apps.get_model('partners', 'Partner')
    missing = list(Partner.objects.filter(country_code__isnull=True).values_list('pk', flat=True)[:50])
    if missing:
        raise RuntimeError(
            'ADR-0023: cannot set country_code NOT NULL; ambiguous partners remain: '
            f'{missing}. Map legacy country strings then re-run.'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('partners', '0007_partner_country_code_jurisdiction'),
    ]

    operations = [
        migrations.RunPython(assert_country_code_ready, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='partner',
            name='country_code',
            field=models.CharField(default='HR', max_length=2, verbose_name='Kod države'),
        ),
        migrations.RemoveConstraint(
            model_name='partner',
            name='unique_partner_tax_per_tenant',
        ),
        migrations.AddConstraint(
            model_name='partner',
            constraint=models.UniqueConstraint(
                condition=Q(tax_number__gt=''),
                fields=('tenant', 'tax_number'),
                name='unique_partner_tax_per_tenant',
            ),
        ),
        migrations.AddConstraint(
            model_name='partner',
            constraint=models.UniqueConstraint(
                condition=Q(vat_number__gt=''),
                fields=('tenant', 'vat_number'),
                name='unique_partner_vat_per_tenant',
            ),
        ),
    ]
