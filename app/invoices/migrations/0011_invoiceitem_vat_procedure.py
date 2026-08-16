from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('invoices', '0010_tenant_required'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoiceitem',
            name='vat_procedure',
            field=models.CharField(
                choices=[
                    ('standard', 'Standardno'),
                    ('oss', 'OSS (e-trgovina EU)'),
                    ('eu_distance', 'Prodaja na daljinu EU'),
                    ('eu_electronic', 'Elektroničko sučelje'),
                    ('ioss', 'IOSS (uvoz)'),
                ],
                default='standard',
                help_text='OSS/IOSS e-trgovina EU — mapira na PDV boxove 204/214/215',
                max_length=20,
                verbose_name='PDV postupak',
            ),
        ),
    ]
