from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from banking.services.ais_e2e import resolve_e2e_connection, run_ais_e2e_checks


class Command(BaseCommand):
    help = (
        'AIS E2E provjera OTP integracije nakon connect flowa '
        '(ne ovisi o broju sandbox transakcija).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--connection-id',
            type=int,
            default=None,
            help='BankConnection PK (default: najnovija connected veza)',
        )
        parser.add_argument(
            '--tenant',
            default=None,
            help='Tenant slug (default: bilo koja connected OTP veza)',
        )
        parser.add_argument(
            '--run-sync',
            action='store_true',
            help='Pokreni dva uzastopna synca i provjeri idempotentnost',
        )

    def handle(self, *args, **options):
        try:
            connection = resolve_e2e_connection(
                connection_id=options['connection_id'],
                tenant_slug=options['tenant'],
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            f'AIS E2E: connection #{connection.pk} '
            f'({connection.tenant.slug}, {connection.status})',
        )

        report = run_ais_e2e_checks(connection, run_sync=options['run_sync'])
        for check in report.checks:
            if check.passed:
                self.stdout.write(self.style.SUCCESS(f'✔ {check.name}: {check.detail}'))
            else:
                self.stdout.write(self.style.ERROR(f'✘ {check.name}: {check.detail}'))

        if not report.passed:
            raise CommandError('AIS E2E provjera nije prošla.')
        self.stdout.write(self.style.SUCCESS('AIS E2E OK.'))
