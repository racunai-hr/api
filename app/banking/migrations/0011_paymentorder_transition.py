# Generated manually for PR1 PaymentOrderTransition audit

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0007_create_finestar_tenant'),
        ('banking', '0010_paymentorder'),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentOrderTransition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sequence', models.PositiveSmallIntegerField()),
                ('from_status', models.CharField(max_length=20)),
                ('to_status', models.CharField(max_length=20)),
                ('actor', models.CharField(
                    choices=[
                        ('submit', 'Submit'),
                        ('otp_callback', 'OTP callback'),
                        ('otp_polling', 'OTP polling'),
                        ('admin_action', 'Admin akcija'),
                        ('system', 'Sustav'),
                        ('celery', 'Celery'),
                    ],
                    max_length=64,
                )),
                ('reason', models.CharField(blank=True, max_length=200)),
                ('correlation_id', models.UUIDField(blank=True, null=True)),
                ('metadata', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('order', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='transitions',
                    to='banking.paymentorder',
                )),
                ('tenant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to='tenants.tenant',
                )),
            ],
            options={
                'verbose_name': 'PIS prijelaz statusa',
                'verbose_name_plural': 'PIS prijelazi statusa',
                'ordering': ['order', 'sequence'],
            },
        ),
        migrations.AddConstraint(
            model_name='paymentordertransition',
            constraint=models.UniqueConstraint(
                fields=('order', 'sequence'),
                name='unique_payment_order_transition_sequence',
            ),
        ),
    ]
