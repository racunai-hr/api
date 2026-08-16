from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0008_td001_supplier_to_partner'),
    ]

    operations = [
        migrations.AddField(
            model_name='expense',
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
                help_text='IOSS uvoz — mapira na PDV box 308',
                max_length=20,
                verbose_name='PDV postupak',
            ),
        ),
    ]
