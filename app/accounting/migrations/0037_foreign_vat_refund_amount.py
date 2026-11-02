from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0036_posting_amount_field_vehicle_split'),
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
                    ('vehicle_cost_recognized', 'Priznati dio neto troška vozila'),
                    ('vehicle_cost_limited', 'Nepriznati dio neto troška i PDV-a'),
                    ('vehicle_vat_deductible', 'Odbitni pretporez vozila'),
                    ('foreign_vat_refund_eur', 'Strani PDV kao potraživanje, u EUR'),
                ],
                max_length=32,
                verbose_name='Polje iznosa',
            ),
        ),
    ]
