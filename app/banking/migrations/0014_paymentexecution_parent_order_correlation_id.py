# Denormalizacija order.correlation_id na PaymentExecution (SQL/log bez JOIN-a)

from django.db import migrations, models


def backfill_parent_order_correlation_id(apps, schema_editor):
    PaymentExecution = apps.get_model('banking', 'PaymentExecution')
    for execution in PaymentExecution.objects.select_related('order').iterator():
        if execution.parent_order_correlation_id:
            continue
        PaymentExecution.objects.filter(pk=execution.pk).update(
            parent_order_correlation_id=execution.order.correlation_id,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0013_paymentexecution'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentexecution',
            name='parent_order_correlation_id',
            field=models.UUIDField(
                editable=False,
                help_text=(
                    'Immutable snapshot of PaymentOrder.correlation_id '
                    'captured when the execution was created. '
                    'Not kept in sync with the order. '
                    'Used for reporting, audit export, and log correlation.'
                ),
                null=True,
                verbose_name='Correlation ID naloga (denormalizirano)',
            ),
        ),
        migrations.RunPython(backfill_parent_order_correlation_id, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='paymentexecution',
            name='parent_order_correlation_id',
            field=models.UUIDField(
                editable=False,
                help_text=(
                    'Immutable snapshot of PaymentOrder.correlation_id '
                    'captured when the execution was created. '
                    'Not kept in sync with the order. '
                    'Used for reporting, audit export, and log correlation.'
                ),
                verbose_name='Correlation ID naloga (denormalizirano)',
            ),
        ),
    ]
