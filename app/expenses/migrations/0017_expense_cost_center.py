from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0028_cost_center'),
        ('expenses', '0016_expense_vehicle'),
    ]

    operations = [
        migrations.AddField(
            model_name='expensecategory',
            name='default_cost_center',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='expense_categories',
                to='accounting.costcenter',
                verbose_name='Zadano mjesto troška',
            ),
        ),
        migrations.AddField(
            model_name='expense',
            name='cost_center',
            field=models.ForeignKey(
                blank=True,
                help_text='Input za resolver. Kanonski trag nakon knjiženja je JournalEntryLine.cost_center.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='expenses',
                to='accounting.costcenter',
                verbose_name='Mjesto troška',
            ),
        ),
    ]
