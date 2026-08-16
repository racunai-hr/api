# Generated manually for PR2 PaymentOrder posting idempotency

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0007_fixed_asset'),
        ('banking', '0011_paymentorder_transition'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentorder',
            name='posted_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Datum knjiženja'),
        ),
        migrations.AddField(
            model_name='paymentorder',
            name='posting_journal_entry',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='source_payment_orders',
                to='accounting.journalentry',
                verbose_name='Temeljnica knjiženja',
            ),
        ),
    ]
