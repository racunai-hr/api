# PR4: migriraj legacy BankPaymentInitiation → PaymentOrder, zatim ukloni model.

from django.db import migrations

LEGACY_STATUS_MAP = {
    'received': 'submitted',
    'pending': 'sca_required',
    'accepted': 'accepted',
    'rejected': 'rejected',
    'cancelled': 'failed',
}


def migrate_legacy_initiations(apps, schema_editor):
    BankPaymentInitiation = apps.get_model('banking', 'BankPaymentInitiation')
    PaymentOrder = apps.get_model('banking', 'PaymentOrder')
    PaymentExecution = apps.get_model('banking', 'PaymentExecution')

    for initiation in BankPaymentInitiation.objects.select_related(
        'connection__bank_account', 'payment',
    ).iterator():
        payment = initiation.payment
        connection = initiation.connection
        try:
            bank_account = connection.bank_account
            debtor_iban = bank_account.iban or ''
        except Exception:
            debtor_iban = ''
        creditor_iban = payment.counterparty_account or ''
        if not debtor_iban or not creditor_iban:
            continue

        order_status = LEGACY_STATUS_MAP.get(initiation.status, 'submitted')
        order = PaymentOrder.objects.create(
            tenant_id=initiation.tenant_id,
            connection_id=connection.pk,
            payment_id=payment.pk,
            status=order_status,
            otp_payment_id=initiation.initiation_id,
            authorization_id=initiation.authorization_id,
            sca_redirect_url=initiation.sca_redirect_url,
            debtor_iban=debtor_iban,
            creditor_iban=creditor_iban,
            creditor_name=payment.counterparty_name or 'Primatelj',
            amount=payment.amount,
            currency=payment.currency,
            reference=payment.reference_number or payment.description or '',
            correlation_id=initiation.correlation_id,
            last_error=initiation.last_error,
            payment_product='sepa-credit-transfers',
        )
        PaymentExecution.objects.create(
            tenant_id=initiation.tenant_id,
            order_id=order.pk,
            attempt=1,
            status=order_status,
            provider_payment_id=initiation.initiation_id,
            authorization_id=initiation.authorization_id,
            sca_redirect_url=initiation.sca_redirect_url,
            payment_product='sepa-credit-transfers',
            last_error=initiation.last_error,
            correlation_id=initiation.correlation_id,
            parent_order_correlation_id=order.correlation_id,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0014_paymentexecution_parent_order_correlation_id'),
    ]

    operations = [
        migrations.RunPython(migrate_legacy_initiations, migrations.RunPython.noop),
        migrations.DeleteModel(
            name='BankPaymentInitiation',
        ),
    ]
