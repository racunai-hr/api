# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0003_tenant_required'),
    ]

    operations = [
        migrations.AlterField(
            model_name='bankaccount',
            name='currency',
            field=models.CharField(default='EUR', max_length=3, verbose_name='Valuta'),
        ),
        migrations.AlterField(
            model_name='payment',
            name='currency',
            field=models.CharField(default='EUR', max_length=3, verbose_name='Valuta'),
        ),
    ]
