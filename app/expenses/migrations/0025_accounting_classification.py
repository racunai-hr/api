from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0007_create_finestar_tenant'),
        ('expenses', '0024_accounting_review'),
    ]

    operations = [
        migrations.CreateModel(
            name='AccountingClassification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('facts_hash', models.CharField(max_length=64, verbose_name='Hash činjenica')),
                ('prompt_version', models.PositiveSmallIntegerField(verbose_name='Verzija prompta')),
                ('schema_version', models.PositiveSmallIntegerField(verbose_name='Verzija sheme')),
                ('model', models.CharField(max_length=64, verbose_name='Model')),
                ('payload', models.JSONField(default=dict, verbose_name='Odgovor')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'Klasifikacija dokumenta',
                'verbose_name_plural': 'Klasifikacije dokumenata',
            },
        ),
        migrations.AddConstraint(
            model_name='accountingclassification',
            constraint=models.UniqueConstraint(fields=('tenant', 'facts_hash', 'prompt_version', 'schema_version', 'model'), name='unique_accounting_classification_cache'),
        ),
    ]
