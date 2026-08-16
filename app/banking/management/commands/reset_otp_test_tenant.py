from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from banking.models import BankStatement, BankTransaction
from banking.provider_models import (
    BankApiCall,
    BankConnection,
    BankConsent,
    BankWebhookEvent,
    PaymentExecution,
    PaymentExecutionTransition,
    PaymentOrder,
    PaymentOrderTransition,
)
from banking.otp.psu import ALL_OTP_TEST_TENANTS, DEFAULT_OTP_TEST_TENANTS, otp_test_psu_by_slug
from payments.models import BankAccount
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Resetira OTP test tenant — briše banking sloj, ostavlja tenant/company/accounting.'

    def add_arguments(self, parser):
        parser.add_argument('tenant_slug', type=str, help='Tenant slug (npr. otp-company-no1)')

    @transaction.atomic
    def handle(self, *args, **options):
        slug = options['tenant_slug']
        if slug not in otp_test_psu_by_slug():
            self.stdout.write(
                self.style.WARNING(f'{slug} nije poznati OTP test tenant — nastavljam reset.'),
            )

        try:
            tenant = Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant {slug} ne postoji.') from exc

        connections = BankConnection.all_objects.filter(tenant=tenant)
        connection_ids = list(connections.values_list('pk', flat=True))

        PaymentOrderTransition.all_objects.filter(tenant=tenant).delete()
        PaymentExecutionTransition.all_objects.filter(tenant=tenant).delete()
        PaymentExecution.all_objects.filter(tenant=tenant).delete()
        PaymentOrder.all_objects.filter(tenant=tenant).delete()
        BankConsent.objects.filter(connection_id__in=connection_ids).delete()
        BankApiCall.objects.filter(tenant=tenant).delete()
        BankWebhookEvent.all_objects.filter(tenant=tenant).delete()
        BankTransaction.all_objects.filter(tenant=tenant).delete()
        BankStatement.all_objects.filter(tenant=tenant).delete()
        connections.delete()

        reset_accounts = BankAccount.all_objects.filter(tenant=tenant)
        for account in reset_accounts:
            account.status = 'pending_discovery'
            account.iban = None
            account.external_account_id = ''
            account.provider_connection = None
            account.discovered_at = None
            account.save(
                update_fields=[
                    'status',
                    'iban',
                    'external_account_id',
                    'provider_connection',
                    'discovered_at',
                ],
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Reset tenant {slug}: {len(connection_ids)} veza, '
                f'{reset_accounts.count()} računa → pending_discovery.',
            ),
        )
