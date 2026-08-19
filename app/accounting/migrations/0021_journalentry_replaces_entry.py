# Generated manually for JE technical repost provenance.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0020_vat_projection_prepare'),
    ]

    operations = [
        migrations.AddField(
            model_name='journalentry',
            name='replaces_entry',
            field=models.ForeignKey(
                blank=True,
                help_text='Eksplicitni replacement JE (tehničko preknjiženje); ne koristiti timestamp heuristiku.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='replaced_by',
                to='accounting.journalentry',
                verbose_name='Zamjenjuje temeljnicu',
            ),
        ),
    ]
