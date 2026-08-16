# Generated manually for PR3 PaymentExecution

import uuid

from django.db import migrations, models
import django.db.models.deletion


def migrate_existing_payment_orders(apps, schema_editor):
    PaymentOrder = apps.get_model('banking', 'PaymentOrder')
    PaymentExecution = apps.get_model('banking', 'PaymentExecution')

    for order in PaymentOrder.objects.all():
        if order.executions.exists():
            continue
        PaymentExecution.objects.create(
            tenant_id=order.tenant_id,
            order_id=order.pk,
            attempt=1,
            status=order.status,
            provider_payment_id=order.otp_payment_id,
            authorization_id=order.authorization_id,
            sca_redirect_url=order.sca_redirect_url,
            payment_product=order.payment_product,
            last_error=order.last_error,
            correlation_id=order.correlation_id or uuid.uuid4(),
        )


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0012_paymentorder_posting_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentExecution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('attempt', models.PositiveSmallIntegerField()),
                ('status', models.CharField(
                    choices=[
                        ('draft', 'Nacrt'),
                        ('submitted', 'Poslan'),
                        ('sca_required', 'SCA potreban'),
                        ('authorised', 'Autoriziran'),
                        ('accepted', 'Prihvaćen'),
                        ('executed', 'Izvršen'),
                        ('rejected', 'Odbijen'),
                        ('failed', 'Neuspješan'),
                    ],
                    default='draft',
                    max_length=20,
                )),
                ('provider_payment_id', models.CharField(blank=True, max_length=100, verbose_name='OTP paymentId')),
                ('authorization_id', models.CharField(blank=True, max_length=100)),
                ('sca_redirect_url', models.URLField(blank=True)),
                ('payment_product', models.CharField(
                    choices=[
                        ('domestic-payment', 'Domaće plaćanje'),
                        ('sepa-credit-transfers', 'SEPA credit transfer'),
                    ],
                    default='sepa-credit-transfers',
                    max_length=40,
                )),
                ('last_error', models.TextField(blank=True)),
                ('correlation_id', models.UUIDField(default=uuid.uuid4, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='executions',
                    to='banking.paymentorder',
                )),
                ('tenant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to='tenants.tenant',
                )),
            ],
            options={
                'verbose_name': 'PIS izvršenje',
                'verbose_name_plural': 'PIS izvršenja',
                'ordering': ['order', '-attempt'],
            },
        ),
        migrations.CreateModel(
            name='PaymentExecutionTransition',
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
                ('execution', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='transitions',
                    to='banking.paymentexecution',
                )),
                ('tenant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to='tenants.tenant',
                )),
            ],
            options={
                'verbose_name': 'PIS prijelaz izvršenja',
                'verbose_name_plural': 'PIS prijelazi izvršenja',
                'ordering': ['execution', 'sequence'],
            },
        ),
        migrations.AddConstraint(
            model_name='paymentexecution',
            constraint=models.UniqueConstraint(
                fields=('order', 'attempt'),
                name='unique_payment_execution_attempt',
            ),
        ),
        migrations.AddConstraint(
            model_name='paymentexecution',
            constraint=models.UniqueConstraint(
                condition=models.Q(('provider_payment_id__gt', '')),
                fields=('tenant', 'provider_payment_id'),
                name='unique_payment_execution_provider_id_per_tenant',
            ),
        ),
        migrations.AddConstraint(
            model_name='paymentexecutiontransition',
            constraint=models.UniqueConstraint(
                fields=('execution', 'sequence'),
                name='unique_payment_execution_transition_sequence',
            ),
        ),
        migrations.RunPython(migrate_existing_payment_orders, migrations.RunPython.noop),
    ]
