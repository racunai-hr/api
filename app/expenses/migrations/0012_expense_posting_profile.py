from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0011_private_funds_claim'),
    ]

    operations = [
        migrations.AddField(
            model_name='expense',
            name='posting_profile',
            field=models.CharField(
                choices=[
                    ('opex', 'Operativni trošak'),
                    ('asset_purchase', 'Nabava dugotrajne imovine'),
                ],
                default='opex',
                help_text='Poslovna vrsta dokumenta koja određuje način knjiženja (npr. opex, asset_purchase).',
                max_length=32,
                verbose_name='Posting profil',
            ),
        ),
    ]
