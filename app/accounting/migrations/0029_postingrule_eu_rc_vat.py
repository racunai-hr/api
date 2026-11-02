from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0028_cost_center'),
    ]

    operations = [
        migrations.AlterField(
            model_name='postingrule',
            name='amount_field',
            field=models.CharField(
                choices=[
                    ('subtotal', 'Osnovica'),
                    ('tax_amount', 'PDV'),
                    ('total_amount', 'Ukupan iznos'),
                    ('amount', 'Iznos'),
                    ('net_amount', 'Neto iznos'),
                    ('eu_rc_vat', 'EU reverse-charge PDV'),
                ],
                max_length=20,
                verbose_name='Polje iznosa',
            ),
        ),
    ]
