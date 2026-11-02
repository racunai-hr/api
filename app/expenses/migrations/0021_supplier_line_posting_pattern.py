import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0020_rename_expenseline_posting_account'),
        ('partners', '0001_initial'),
        ('tenants', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='SupplierLinePostingPattern',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('line_signature', models.CharField(max_length=64, verbose_name='Potpis stavki')),
                ('accounts', models.JSONField(blank=True, default=list, verbose_name='Konta stavki')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'supplier',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='line_posting_patterns',
                        to='partners.partner',
                        verbose_name='Dobavljač',
                    ),
                ),
                (
                    'tenant',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='expenses_supplierlinepostingpattern_set',
                        to='tenants.tenant',
                        verbose_name='Tenant',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Uzorak knjiženja stavki',
                'verbose_name_plural': 'Uzorci knjiženja stavki',
            },
        ),
        migrations.CreateModel(
            name='ExpenseBankCloseIdempotency',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('idempotency_key', models.CharField(max_length=128)),
                ('result_payload', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'expense',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='bank_close_idempotency_keys',
                        to='expenses.expense',
                    ),
                ),
                (
                    'tenant',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='expenses_expensebankcloseidempotency_set',
                        to='tenants.tenant',
                        verbose_name='Tenant',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Idempotency zatvaranja nacrta',
            },
        ),
        migrations.AddConstraint(
            model_name='supplierlinepostingpattern',
            constraint=models.UniqueConstraint(
                fields=('tenant', 'supplier', 'line_signature'),
                name='unique_supplier_line_posting_pattern',
            ),
        ),
        migrations.AddConstraint(
            model_name='expensebankcloseidempotency',
            constraint=models.UniqueConstraint(
                fields=('tenant', 'idempotency_key'),
                name='unique_expense_bank_close_idempotency',
            ),
        ),
    ]
