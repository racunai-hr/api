from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from banking.services.pis_e2e import resolve_pis_e2e_connection, run_pis_e2e_checks


class Command(BaseCommand):
    help = 'PIS E2E provjera OTP integracije (opcionalno testno plaćanje).'

    def add_arguments(self, parser):
        parser.add_argument('--connection-id', type=int, default=None)
        parser.add_argument('--tenant', default=None)
        parser.add_argument(
            '--submit-test-payment',
            action='store_true',
            help='Pošalji testni SEPA nalog (1 EUR, sandbox IBAN)',
        )
        parser.add_argument('--min-sync-streak', type=int, default=3)

    def handle(self, *args, **options):
        try:
            connection = resolve_pis_e2e_connection(
                connection_id=options['connection_id'],
                tenant_slug=options['tenant'],
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            f'PIS E2E: connection #{connection.pk} ({connection.tenant.slug})',
        )
        report = run_pis_e2e_checks(
            connection,
            submit_test_payment=options['submit_test_payment'],
            min_sync_streak=options['min_sync_streak'],
        )
        for check in report.checks:
            if check.passed:
                self.stdout.write(self.style.SUCCESS(f'✔ {check.name}: {check.detail}'))
            else:
                self.stdout.write(self.style.ERROR(f'✘ {check.name}: {check.detail}'))

        if not report.passed:
            raise CommandError('PIS E2E provjera nije prošla.')
        self.stdout.write(self.style.SUCCESS('PIS E2E OK.'))
