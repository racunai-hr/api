from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from banking.services.healthcheck import (
    otp_healthcheck_provider_mismatch,
    run_otp_healthcheck,
)


class Command(BaseCommand):
    help = 'Provjera statičke OTP konfiguracije (cert, env, provider) bez mrežnih poziva.'

    def add_arguments(self, parser):
        parser.add_argument('--provider', default='otp_sandbox')

    def handle(self, *args, **options):
        provider_code = options['provider']
        report = run_otp_healthcheck(provider=provider_code)

        for item in report.checks:
            if item.passed:
                self._ok(item.label)
            else:
                self._fail(item.label)

        warning = otp_healthcheck_provider_mismatch(provider=provider_code)
        if warning:
            self.stdout.write(self.style.WARNING(warning))

        if not report.passed:
            raise CommandError('OTP healthcheck failed.')
        self.stdout.write(self.style.SUCCESS('OTP healthcheck OK.'))

    def _ok(self, message: str):
        self.stdout.write(self.style.SUCCESS(f'✔ {message}'))

    def _fail(self, message: str):
        self.stdout.write(self.style.ERROR(f'✘ {message}'))
