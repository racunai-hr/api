import django.db.models.deletion
from django.db import migrations, models


def seed_ante_vrcan(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    ExpensePayer = apps.get_model('expenses', 'ExpensePayer')

    tenant = Tenant.objects.filter(slug='finestar').first()
    if not tenant:
        return

    ExpensePayer.objects.update_or_create(
        tenant=tenant,
        name='Ante Vrcan',
        defaults={
            'oib': '11528564544',
            'type': 'other',
            'is_active': True,
        },
    )


def unseed_ante_vrcan(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    ExpensePayer = apps.get_model('expenses', 'ExpensePayer')

    tenant = Tenant.objects.filter(slug='finestar').first()
    if not tenant:
        return

    ExpensePayer.objects.filter(tenant=tenant, oib='11528564544').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0006_expense_import_models'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExpensePayer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='Ime')),
                ('oib', models.CharField(blank=True, max_length=20, verbose_name='OIB')),
                ('type', models.CharField(
                    choices=[
                        ('employee', 'Zaposlenik'),
                        ('owner', 'Vlasnik'),
                        ('director', 'Direktor'),
                        ('other', 'Ostalo'),
                    ],
                    default='other',
                    max_length=20,
                    verbose_name='Tip',
                )),
                ('is_active', models.BooleanField(default=True, verbose_name='Aktivan')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Datum stvaranja')),
                ('tenant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='%(class)s_set',
                    to='tenants.tenant',
                )),
            ],
            options={
                'verbose_name': 'Platitelj troška',
                'verbose_name_plural': 'Platitelji troškova',
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='expense',
            name='reimbursement_status',
            field=models.CharField(
                choices=[
                    ('not_required', 'Nije potrebna nadoknada'),
                    ('pending', 'Čeka refundaciju'),
                    ('partially_reimbursed', 'Djelomično refundirano'),
                    ('reimbursed', 'U potpunosti refundirano'),
                ],
                default='not_required',
                max_length=30,
                verbose_name='Status nadoknade',
            ),
        ),
        migrations.AddField(
            model_name='expense',
            name='settlement_method',
            field=models.CharField(
                blank=True,
                choices=[
                    ('business_account', 'Poslovni račun'),
                    ('company_cash', 'Gotovina (blagajna)'),
                    ('private_card', 'Privatna kartica zaposlenika/vlasnika'),
                    ('private_cash', 'Privatna gotovina'),
                ],
                max_length=20,
                verbose_name='Način podmirenja',
            ),
        ),
        migrations.AddField(
            model_name='expense',
            name='paid_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='expenses',
                to='expenses.expensepayer',
                verbose_name='Podmirenje — platitelj',
            ),
        ),
        migrations.AddConstraint(
            model_name='expensepayer',
            constraint=models.UniqueConstraint(
                fields=('tenant', 'name'),
                name='unique_expense_payer_per_tenant',
            ),
        ),
        migrations.RunPython(seed_ante_vrcan, unseed_ante_vrcan),
    ]
