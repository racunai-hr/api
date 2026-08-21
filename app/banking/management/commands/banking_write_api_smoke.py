"""Stage smoke for banking JWT write flow (ADR-0021 Slice 4).

upload CAMT → poll import → list transactions → match → unmatch → sync enqueue.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from banking.models import BankTransaction
from banking.provider_models import BankConnection, BankProvider
from banking.services.import_runs import execute_import_run
from payments.models import BankAccount, Payment
from tenants.models import Tenant, TenantMembership

SAMPLE = (
    Path(__file__).resolve().parents[2] / 'tests' / 'fixtures' / 'otp_camt053_sample.xml'
)


class Command(BaseCommand):
    help = 'Smoke JWT write API: CAMT import → poll → match/unmatch → sync'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', default='finestar')
        parser.add_argument('--host', default='')
        parser.add_argument(
            '--execute-import',
            action='store_true',
            help='Synchronous execute of queued import (when Celery worker not awaited).',
        )

    def handle(self, *args, **options):
        if not SAMPLE.exists():
            raise CommandError(f'Fixture missing: {SAMPLE}')

        tenant = Tenant.objects.filter(slug=options['tenant']).first()
        if tenant is None:
            raise CommandError(f'Tenant {options["tenant"]} ne postoji.')

        from django.conf import settings

        infix = getattr(settings, 'TENANT_STAGE_INFIX', '') or ''
        host = options['host'] or f'{tenant.slug}{infix}.{settings.TENANT_PLATFORM_DOMAIN}'

        User = get_user_model()
        username = f'banking-smoke-{tenant.slug}'
        user, created = User.objects.get_or_create(
            username=username,
            defaults={'email': f'{username}@example.test'},
        )
        if created:
            user.set_password(uuid.uuid4().hex)
            user.save()
        TenantMembership.objects.update_or_create(
            user=user,
            tenant=tenant,
            defaults={'role': 'accountant'},
        )

        account = (
            BankAccount.all_objects.filter(tenant=tenant, iban='HR6124070001100204771')
            .order_by('id')
            .first()
        )
        if account is None:
            raise CommandError('Nema BankAccount s CAMT IBAN-om na tenantu.')

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')
        client.defaults['HTTP_HOST'] = host

        key = f'smoke-{uuid.uuid4().hex}'
        self.stdout.write(f'Host={host} user={username} idem={key}')

        with override_settings(SECURE_SSL_REDIRECT=False):
            self._run_flow(client, tenant, account, user, key, options)

    def _run_flow(self, client, tenant, account, user, key, options):
        # 1) Upload CAMT
        upload = client.post(
            '/api/banking/statement-imports/',
            {'file': SimpleUploadedFile('otp_camt053_sample.xml', SAMPLE.read_bytes())},
            format='multipart',
            HTTP_IDEMPOTENCY_KEY=key,
        )
        if upload.status_code != 202:
            raise CommandError(f'Import očekivao 202, dobio {upload.status_code}: {upload.content}')
        run_id = upload.json()['id']
        self.stdout.write(self.style.SUCCESS(f'1) import accepted run_id={run_id}'))

        if options['execute_import'] or upload.json().get('status') == 'queued':
            execute_import_run(run_id)

        # 2) Poll
        poll = client.get(f'/api/banking/statement-imports/{run_id}/')
        if poll.status_code != 200:
            raise CommandError(f'Poll failed: {poll.status_code}')
        poll_body = poll.json()
        if poll_body['status'] not in ('succeeded', 'failed', 'running', 'queued'):
            raise CommandError(f'Neočekivan status: {poll_body["status"]}')
        if options['execute_import']:
            poll = client.get(f'/api/banking/statement-imports/{run_id}/')
            poll_body = poll.json()
            if poll_body['status'] != 'succeeded':
                raise CommandError(f'Import nije succeeded: {poll_body}')
        self.stdout.write(
            self.style.SUCCESS(
                f'2) poll status={poll_body["status"]} tx_created={poll_body.get("transactions_created")}'
            )
        )

        # 3) List unmatched transactions for account
        listed = client.get(
            '/api/banking/transactions/',
            {'bank_account': account.pk, 'match_status': 'unmatched', 'page_size': 5},
        )
        if listed.status_code != 200:
            raise CommandError(f'List tx failed: {listed.status_code}')
        results = listed.json()['results']
        if not results:
            raise CommandError('Nema unmatched transakcija za match smoke.')
        tx_id = results[0]['id']
        tx = BankTransaction.all_objects.get(pk=tx_id)
        self.stdout.write(self.style.SUCCESS(f'3) listed unmatched tx_id={tx_id} amount={tx.amount}'))

        payment = Payment.all_objects.create(
            tenant=tenant,
            payment_number=f'SMOKE-{uuid.uuid4().hex[:8]}',
            payment_type='incoming' if tx.transaction_type == 'credit' else 'outgoing',
            payment_method='bank_transfer',
            status='completed',
            amount=tx.amount,
            currency=tx.currency,
            bank_account=account,
            payment_date=tx.transaction_date or date.today(),
            created_by=user,
        )

        # 4) Match
        matched = client.post(
            f'/api/banking/transactions/{tx_id}/match/',
            {'target_type': 'payment', 'target_id': payment.pk},
            format='json',
        )
        if matched.status_code != 200:
            raise CommandError(f'Match failed: {matched.status_code} {matched.content}')
        self.stdout.write(self.style.SUCCESS(f'4) matched payment_id={payment.pk}'))

        # 5) Unmatch
        unmatched = client.post(f'/api/banking/transactions/{tx_id}/unmatch/')
        if unmatched.status_code != 200:
            raise CommandError(f'Unmatch failed: {unmatched.status_code} {unmatched.content}')
        if unmatched.json()['match_status'] != 'unmatched':
            raise CommandError('Unmatch nije vratio unmatched status.')
        self.stdout.write(self.style.SUCCESS('5) unmatched OK (payment ostaje)'))

        # 6) Sync enqueue (create disposable connection if needed)
        connection = (
            BankConnection.all_objects.filter(tenant=tenant, bank_account=account)
            .order_by('-id')
            .first()
        )
        if connection is None:
            provider, _ = BankProvider.objects.get_or_create(
                code='smoke-provider',
                defaults={
                    'name': 'Smoke Provider',
                    'environment': 'sandbox',
                    'iam_base': 'https://iam.example.test',
                    'api_base': 'https://api.example.test',
                    'service_host': 'api.example.test',
                    'is_active': True,
                },
            )
            connection = BankConnection.all_objects.create(
                tenant=tenant,
                bank_provider=provider,
                bank_account=account,
                status='connected',
                last_sync_at=timezone.now(),
            )

        sync1 = client.post(f'/api/banking/connections/{connection.pk}/sync/')
        if sync1.status_code != 202:
            raise CommandError(f'Sync 1 failed: {sync1.status_code} {sync1.content}')
        sync2 = client.post(f'/api/banking/connections/{connection.pk}/sync/')
        if sync2.status_code != 202:
            raise CommandError(f'Sync 2 failed: {sync2.status_code} {sync2.content}')
        if sync1.json()['id'] != sync2.json()['id']:
            raise CommandError('Ponovljeni sync nije vratio isti BankSyncRun.')
        if sync2.json().get('auto_create_payments') is not False:
            raise CommandError('auto_create_payments mora biti False.')
        self.stdout.write(
            self.style.SUCCESS(
                f'6) sync 202 run_id={sync1.json()["id"]} reused={not sync2.json()["created"]}'
            )
        )
        self.stdout.write(self.style.SUCCESS('Banking write API smoke PASSED'))
