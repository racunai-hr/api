from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0028_cost_center'),
        ('invoices', '0011_invoiceitem_vat_procedure'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='cost_center',
            field=models.ForeignKey(
                blank=True,
                help_text='Input za resolver prihoda. Kanonski trag nakon knjiženja je JournalEntryLine.cost_center.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='invoices',
                to='accounting.costcenter',
                verbose_name='Mjesto troška',
            ),
        ),
    ]
