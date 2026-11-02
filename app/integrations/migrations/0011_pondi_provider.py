from django.db import migrations, models


PONDI_CHOICES = [
    ('super', 'SUPER'),
    ('direct', 'Direktno (racunAI)'),
    ('pondi', 'Pondi'),
    ('mer', 'MER'),
    ('cis', 'CIS (Porezna)'),
    ('fiskal_platform', 'Fiskal Platform'),
    ('otp', 'OTP banka'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0010_inbound_gateway_resolve_cache'),
    ]

    operations = [
        migrations.AlterField(
            model_name='integrationauditlog',
            name='provider',
            field=models.CharField(blank=True, choices=PONDI_CHOICES, max_length=20),
        ),
        migrations.AlterField(
            model_name='integrationconfig',
            name='provider',
            field=models.CharField(
                choices=PONDI_CHOICES,
                max_length=20,
                verbose_name='Provider',
            ),
        ),
        migrations.AlterField(
            model_name='integrationoutboxmessage',
            name='provider',
            field=models.CharField(blank=True, choices=PONDI_CHOICES, max_length=20),
        ),
    ]
