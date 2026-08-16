from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from banking.config import get_active_otp_provider, resolve_otp_credentials
from banking.otp.client import OtpClientError, build_client
from banking.provider_models import BankApiCall, BankConnection


class Command(BaseCommand):
    help = 'Live OTP dijagnostika: TLS, IAM metadata, API dostupnost, zadnji ApiCall/sync.'

    def add_arguments(self, parser):
        parser.add_argument('--provider', default=None, help='BankProvider code (default: active OTP)')

    def handle(self, *args, **options):
        ok = True
        provider = None
        if options['provider']:
            from banking.provider_models import BankProvider

            provider = BankProvider.objects.filter(code=options['provider'], is_active=True).first()
        else:
            provider = get_active_otp_provider()

        if provider is None:
            raise CommandError('OTP provider nije pronađen.')

        credentials = resolve_otp_credentials()
        stale_cutoff = timezone.now() - timedelta(hours=24)

        try:
            with build_client(provider=provider) as client:
                started = timezone.now()
                response = client.iam_get('/.well-known/openid-configuration')
                elapsed_ms = int((timezone.now() - started).total_seconds() * 1000)
                if response.status_code == 200:
                    self._ok(f'TLS handshake OK')
                    self._ok(f'IAM /.well-known/openid-configuration (200, {elapsed_ms}ms)')
                    metadata = response.json()
                    version = metadata.get('issuer', metadata.get('authorization_endpoint', 'n/a'))
                    self._ok(f'OAuth metadata version: {version}')
                else:
                    ok = False
                    self._fail(f'IAM metadata HTTP {response.status_code}')

                if client.ping():
                    self._ok('API reachable')
                else:
                    ok = False
                    self._fail('API ping failed')
        except OtpClientError as exc:
            ok = False
            self._fail(f'TLS/API error: {exc}')

        last_call = BankApiCall.objects.filter(provider=provider).order_by('-created_at').first()
        if last_call:
            self._ok(
                f'Last BankApiCall: #{last_call.pk} {last_call.created_at:%Y-%m-%d %H:%M} '
                f'status={last_call.http_status}',
            )
        else:
            self.stdout.write('  (no BankApiCall records yet)')

        last_sync = (
            BankConnection.all_objects.filter(
                bank_provider=provider,
                last_sync_at__isnull=False,
            )
            .order_by('-last_sync_at')
            .first()
        )
        if last_sync:
            self._ok(
                f'Last successful sync: connection #{last_sync.pk} at '
                f'{last_sync.last_sync_at:%Y-%m-%d %H:%M}',
            )
        else:
            self.stdout.write('  (no successful sync yet)')

        stale_count = BankConnection.all_objects.filter(
            bank_provider=provider,
            status='connected',
            last_sync_at__lt=stale_cutoff,
        ).count()
        if stale_count:
            self._fail(f'Stale connections: {stale_count} (last_sync > 24h)')
            ok = False
        else:
            self._ok('No stale connections')

        if not ok:
            raise CommandError('OTP diagnose found issues.')
        self.stdout.write(self.style.SUCCESS('OTP diagnose OK.'))

    def _ok(self, message: str):
        self.stdout.write(self.style.SUCCESS(f'✔ {message}'))

    def _fail(self, message: str):
        self.stdout.write(self.style.ERROR(f'✘ {message}'))
