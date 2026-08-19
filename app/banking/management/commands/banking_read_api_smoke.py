"""Code/data smoke for banking JWT read API (ADR-0021 Slice 3).

Uses Django APIClient against an in-process Django — NOT live Gunicorn/Traefik.
For live HTTP, curl https://{tenant}-stage.racunai.hr/api/banking/... separately.

Does not create users, memberships, or banking rows. Does not print JWTs.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from banking.models import BankImportRun, BankStatement
from banking.provider_models import BankConnection
from tenants.models import Tenant, TenantMembership

LIST_PATHS = (
    '/api/banking/overview/',
    '/api/banking/bank-accounts/',
    '/api/banking/statements/',
    '/api/banking/transactions/',
    '/api/banking/payment-orders/',
)


class Command(BaseCommand):
    help = (
        'Code smoke JWT banking READ (APIClient, not live HTTP). '
        'Requires an existing FineStar membership; creates no data.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', default='finestar')
        parser.add_argument(
            '--username',
            default='banking-smoke-finestar',
            help='Existing username with membership on --tenant (default: banking-smoke-finestar).',
        )
        parser.add_argument('--host', default='')

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.WARNING(
                'NOTE: This is an in-process APIClient smoke, not a live Gunicorn/Traefik check.'
            )
        )

        tenant = Tenant.objects.filter(slug=options['tenant']).first()
        if tenant is None:
            raise CommandError(f'Tenant {options["tenant"]} ne postoji.')

        User = get_user_model()
        user = User.objects.filter(username=options['username']).first()
        if user is None:
            raise CommandError(
                f'Korisnik {options["username"]!r} ne postoji. '
                'Odaberi postojećeg člana tenanta (--username); smoke ne stvara korisnike.'
            )
        membership = TenantMembership.objects.filter(user=user, tenant=tenant).first()
        if membership is None:
            raise CommandError(
                f'Korisnik {options["username"]!r} nema membership na {tenant.slug}.'
            )

        from django.conf import settings

        infix = getattr(settings, 'TENANT_STAGE_INFIX', '') or ''
        host = options['host'] or f'{tenant.slug}{infix}.{settings.TENANT_PLATFORM_DOMAIN}'

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')
        client.defaults['HTTP_HOST'] = host

        self.stdout.write(
            f'Host={host} user={user.username} role={membership.role} (JWT not logged)'
        )

        with override_settings(SECURE_SSL_REDIRECT=False):
            self._run(client, tenant)

        self.stdout.write(self.style.SUCCESS('Banking read API code smoke PASSED'))

    def _run(self, client: APIClient, tenant: Tenant) -> None:
        for path in LIST_PATHS:
            response = client.get(path)
            if response.status_code != 200:
                raise CommandError(f'{path} očekivao 200, dobio {response.status_code}')
            body = response.json()
            if 'as_of' not in body:
                raise CommandError(f'{path} nema as_of')
            if path.endswith('overview/'):
                accounts = len(body.get('accounts') or [])
                self.stdout.write(
                    f'OK {path} accounts={accounts} '
                    f'unmatched={body.get("unmatched_transaction_count")} '
                    f'statements={body.get("statement_count")}'
                )
            else:
                if not {'count', 'page', 'page_size', 'results'}.issubset(body):
                    raise CommandError(f'{path} nema paginacijski omotač')
                self.stdout.write(f'OK {path} count={body["count"]} page={body["page"]}')

        statement = (
            BankStatement.all_objects.filter(tenant=tenant).order_by('-id').first()
        )
        if statement is None:
            missing = client.get('/api/banking/statements/999999999/')
            if missing.status_code != 404:
                raise CommandError(
                    f'statement detail bez podataka očekivao 404, dobio {missing.status_code}'
                )
            self.stdout.write('OK statement detail absent → 404')
        else:
            detail = client.get(f'/api/banking/statements/{statement.pk}/')
            if detail.status_code != 200:
                raise CommandError(
                    f'statement detail očekivao 200, dobio {detail.status_code}'
                )
            self.stdout.write(f'OK statement detail id={statement.pk}')

        import_run = (
            BankImportRun.all_objects.filter(tenant=tenant).order_by('-id').first()
        )
        if import_run is None:
            missing = client.get('/api/banking/statement-imports/999999999/')
            if missing.status_code != 404:
                raise CommandError(
                    f'import detail bez podataka očekivao 404, dobio {missing.status_code}'
                )
            self.stdout.write('OK statement-import detail absent → 404')
        else:
            detail = client.get(f'/api/banking/statement-imports/{import_run.pk}/')
            if detail.status_code != 200:
                raise CommandError(
                    f'import detail očekivao 200, dobio {detail.status_code}'
                )
            self.stdout.write(f'OK statement-import detail id={import_run.pk}')

        connection = (
            BankConnection.all_objects.filter(tenant=tenant).order_by('-id').first()
        )
        if connection is None:
            missing = client.get('/api/banking/connections/999999999/sync-status/')
            if missing.status_code != 404:
                raise CommandError(
                    f'sync-status bez connection očekivao 404, dobio {missing.status_code}'
                )
            self.stdout.write('OK sync-status absent → 404')
        else:
            status = client.get(f'/api/banking/connections/{connection.pk}/sync-status/')
            if status.status_code != 200:
                raise CommandError(
                    f'sync-status očekivao 200, dobio {status.status_code}'
                )
            self.stdout.write(f'OK sync-status connection_id={connection.pk}')
