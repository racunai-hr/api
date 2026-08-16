"""SubledgerItem + SubledgerAllocation — saldakonti open items."""

import decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('partners', '0001_initial'),
        ('accounting', '0016_analyticaccount_remove_supplier'),
    ]

    operations = [
        migrations.CreateModel(
            name='SubledgerItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('direction', models.CharField(choices=[('receivable', 'Potraživanje'), ('payable', 'Obveza')], max_length=12, verbose_name='Smjer')),
                ('source_object_id', models.PositiveIntegerField(verbose_name='ID izvora')),
                ('original_amount', models.DecimalField(decimal_places=2, max_digits=15, validators=[django.core.validators.MinValueValidator(decimal.Decimal('0.01'))], verbose_name='Izvorni iznos')),
                ('open_amount', models.DecimalField(decimal_places=2, max_digits=15, validators=[django.core.validators.MinValueValidator(decimal.Decimal('0.00'))], verbose_name='Otvoreni iznos')),
                ('due_date', models.DateField(verbose_name='Datum dospijeća')),
                ('status', models.CharField(choices=[('open', 'Otvoreno'), ('partial', 'Djelomično'), ('closed', 'Zatvoreno'), ('cancelled', 'Poništeno')], default='open', max_length=10, verbose_name='Status')),
                ('closed_at', models.DateTimeField(blank=True, null=True, verbose_name='Datum zatvaranja')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Datum stvaranja')),
                ('journal_entry', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subledger_items', to='accounting.journalentry', verbose_name='Izvorna temeljnica')),
                ('partner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subledger_items', to='partners.partner', verbose_name='Partner')),
                ('source_content_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subledger_source_items', to='contenttypes.contenttype', verbose_name='Tip izvora')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='accounting_subledgeritem_set', to='tenants.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'Stavka saldakonta',
                'verbose_name_plural': 'Stavke saldakonta',
                'ordering': ['-due_date', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='SubledgerAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=15, validators=[django.core.validators.MinValueValidator(decimal.Decimal('0.01'))], verbose_name='Iznos alokacije')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Datum stvaranja')),
                ('journal_entry', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subledger_allocations', to='accounting.journalentry', verbose_name='Temeljnica uplate')),
                ('subledger_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='allocations', to='accounting.subledgeritem', verbose_name='Stavka saldakonta')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='accounting_subledgerallocation_set', to='tenants.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'Alokacija saldakonta',
                'verbose_name_plural': 'Alokacije saldakonta',
            },
        ),
        migrations.AddIndex(
            model_name='subledgeritem',
            index=models.Index(fields=['tenant', 'partner', 'status'], name='accounting__tenant__8a1f2d_idx'),
        ),
        migrations.AddIndex(
            model_name='subledgeritem',
            index=models.Index(fields=['tenant', 'direction', 'due_date'], name='accounting__tenant__c4e9b1_idx'),
        ),
        migrations.AddConstraint(
            model_name='subledgeritem',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'cancelled'), _negated=True), fields=('tenant', 'source_content_type', 'source_object_id'), name='unique_active_subledger_source_per_tenant'),
        ),
        migrations.AddConstraint(
            model_name='subledgerallocation',
            constraint=models.UniqueConstraint(fields=('journal_entry',), name='unique_subledger_allocation_per_journal_entry'),
        ),
    ]
